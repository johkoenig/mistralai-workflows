"""PDF scanner processor: orchestrates the step workflows over a scanner inbox.

The inbox folder is given at start via folder_path; if omitted, the default
lies in the environment variable SCAN_INBOX_FOLDER (bound in the deployment
as a workspace secret). The start parameter always wins.

connector: name of the file connector from FILE_CONNECTOR_NAMES (empty = default);
with several active Nextcloud connectors the caller selects the target connector.

Pipeline (per run, first folder level only), built from the single-purpose step
workflows in workflows/steps/ - each step is also directly triggerable on its own:

1. Find new scans "Scan.pdf" / "ScanNNNN.pdf" and park a copy of each raw scan
   under <folder>/.originals/pdf-scanner-processor/ (before anything modifies it).
2. Step pdf-ocr on the whole folder (with_pages_text=true): PDFs without a
   text layer get OCR (Tesseract deu+eng, deskew, page rotation); the result
   replaces the file in Nextcloud (the step secures its own backup under
   .originals/pdf-ocr/). The step returns per file page count, has_text and
   text snippets, so step 3 needs no second download.
3. Detect front/back pairs from the OCR metadata (analyze-scan-file only as
   fallback for uncovered files): structural prefilter (consecutive scan
   order, same page count, size ratio 0.5-2.0, both with text), then the LLM
   activity front-back-check decides by content (acceptance >= 0.7). The
   detection is name-independent: front and back of one duplex document can
   get completely different names (date only on page 1, invoice spillover).
4. Step pdf-alternating-merge per confirmed pair with the intermediate title
   "ScanMerged<N>.pdf" (N = larger scan number of the pair). The sources are
   archived under .originals/pdf-alternating-merge/<target>/.
5. Split planning inline via the plan_split activity per unit - BEFORE naming:
   splitting is content-based, not name-based, and the parts are named from
   the plan, so a pre-split name would be discarded anyway. The pdf-split
   child is spawned only when a split is actually confirmed, then with fixed
   split_at + titles (no AI planning inside the child).
6. Naming via name-document per unit that was NOT split (parts of split units
   are already named by pdf-split; merged pairs via combined text): the naming
   conventions come from the nearest AGENTS.md (the inbox folder or a parent
   folder) and take precedence over the base prompt; without an AGENTS.md the
   LLM picks a suitable name itself. Confidence below the threshold ->
   left unchanged. Rename only.

apply_changes=false (dry run) plans everything and writes nothing.
Step switches: merge_front_back=false skips step 4, split_parts=false skips step 5.

Child workflows get deterministic execution IDs ({run}-{step}-{item}), so a
replayed run reuses the existing child executions instead of duplicating them.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from datetime import timedelta
from typing import Any

import pydantic
from mistralai import workflows
from mistralai.workflows.plugins.mistralai.connectors import uses_connectors

from worker.ocr import read_text_info, run_with_heartbeat
from worker.pdf_ops import (
    DEFAULT_FILE_CONNECTOR,
    FILE_CONNECTOR_NAMES,
    FRONT_BACK_MIN_CONFIDENCE,
    MAX_CONCURRENT_OCR,
    MIN_NAMING_CONFIDENCE,
    TEXT_SNIPPET_CHARS,
    build_filename,
    detect_front_back,
    file_client,
    file_connectors,
    front_back_check,
    http_get,
    listing_entries,
    load_naming_rules,
    merge_texts,
    merged_scan_name,
    name_document,
    nc_call,
    originals_dir,
    park_original,
    pick_url,
    plan_split,
    scan_order,
    unique_filename,
)
from worker.workflows.steps.pdf_alternating_merge import (
    PdfAlternatingMergeInput,
    PdfAlternatingMergeWorkflow,
)
from worker.workflows.steps.pdf_ocr import PdfOcrInput, PdfOcrWorkflow
from worker.workflows.steps.pdf_split import (
    PdfSplitInput,
    PdfSplitWorkflow,
    resolve_plan,
)

# Default folder from the deployment (workspace secret, environment variable);
# a folder_path given at start always takes precedence.
DEFAULT_FOLDER = os.environ.get("SCAN_INBOX_FOLDER", "")
WORKFLOW_NAME = "pdf-scanner-processor"

HEARTBEAT_TIMEOUT = timedelta(seconds=120)

logger = logging.getLogger("worker.pdf_scanner_processor")


class PdfScannerProcessorInput(pydantic.BaseModel):
    """Start input: inbox folder, apply_changes, model and the step switches."""

    folder_path: str = DEFAULT_FOLDER
    apply_changes: bool = True
    model: str = "mistral-small-latest"
    connector: str = ""
    merge_front_back: bool = True
    split_parts: bool = True


# Step workflows of the pipeline, called as child workflows; tests substitute
# the entries with fakes.
STEPS: dict[str, type[Any]] = {
    "ocr": PdfOcrWorkflow,
    "merge": PdfAlternatingMergeWorkflow,
    "split": PdfSplitWorkflow,
}


@workflows.activity(name="list-scan-files", start_to_close_timeout=timedelta(minutes=5))
async def list_scan_files(folder_path: str, connector: str = "") -> dict[str, Any]:
    """Lists the inbox folder: found scans and all existing file names."""
    nc = file_client(connector)
    result = await nc_call(nc, "files_list", {"path": folder_path})
    scans: list[dict[str, Any]] = []
    existing_names: list[str] = []
    for entry in listing_entries(result):
        if not isinstance(entry, dict):
            continue
        name = entry.get("name", "")
        if entry.get("isDir"):
            continue
        existing_names.append(name)
        order = scan_order(name)
        if order is None:
            continue
        scans.append(
            {
                "name": name,
                "path": entry.get("path") or f"{folder_path.rstrip('/')}/{name}",
                "size": int(entry.get("size") or 0),
                "etag": entry.get("etag"),
                "order": order,
            }
        )
    scans.sort(key=lambda s: s["order"])
    return {"folder": folder_path, "scans": scans, "existing_names": existing_names}


@workflows.activity(
    name="analyze-scan-file",
    start_to_close_timeout=timedelta(minutes=15),
    heartbeat_timeout=HEARTBEAT_TIMEOUT,
    retry_policy_max_attempts=2,
)
async def analyze_scan_file(
    entry: dict[str, Any], connector: str = ""
) -> dict[str, Any]:
    """Downloads a scan and returns page count, text-layer info and a text snippet."""
    nc = file_client(connector)
    workdir = tempfile.mkdtemp(prefix="scan-")
    try:
        logger.info("analyzing %s", entry["path"])
        checkout = await nc_call(
            nc,
            "files_checkout",
            {"path": entry["path"], "confirm": True, "confirm_shared": True},
        )
        local = os.path.join(workdir, entry["name"])
        await run_with_heartbeat(
            http_get,
            pick_url(checkout, "download_url", "downloadUrl"),
            local,
            detail={"phase": "download", "file": entry["name"]},
        )
        info = read_text_info(local)
        logger.info(
            "analysis done: %s (text=%s pages=%s)",
            entry["name"],
            info["has_text"],
            info["page_count"],
        )
        return {
            "name": entry["name"],
            "path": entry["path"],
            "size": os.path.getsize(local),
            "etag": entry.get("etag"),
            "order": entry["order"],
            "page_count": info["page_count"],
            "has_text": info["has_text"],
            "text": "\n".join(info["pages"])[:TEXT_SNIPPET_CHARS],
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@workflows.activity(
    name="backup-scan-file",
    start_to_close_timeout=timedelta(minutes=5),
    retry_policy_max_attempts=2,
)
async def backup_scan_file(from_path: str, connector: str = "") -> dict[str, Any]:
    """Parks a copy of the raw scan under .originals/pdf-scanner-processor/ before it is modified."""
    nc = file_client(connector)
    backup = await park_original(
        nc, from_path, originals_dir(os.path.dirname(from_path), WORKFLOW_NAME)
    )
    return {"from": from_path, "backup": backup}


@workflows.activity(
    name="rename-scan-file",
    start_to_close_timeout=timedelta(minutes=5),
    retry_policy_max_attempts=1,
)
async def rename_scan_file(
    from_path: str, to_path: str, connector: str = ""
) -> dict[str, Any]:
    """Renames a file via files_move."""
    nc = file_client(connector)
    await nc_call(
        nc,
        "files_move",
        {"from": from_path, "to": to_path, "confirm": True, "confirm_shared": True},
    )
    return {"from": from_path, "to": to_path}


@workflows.workflow.define(
    name="pdf-scanner-processor",
    workflow_display_name="Process PDF scanner inbox",
    workflow_description=(
        "End-to-end processing of a scanner inbox folder: OCR of new scans, "
        "detection and alternating merge of front/back pairs (intermediate "
        "ScanMerged<N>.pdf), a confidence-checked split of multi-part PDFs "
        "(the split step names its parts), then LLM naming of the remaining "
        "files (per the nearest AGENTS.md; without one the LLM picks a "
        "suitable name) - built from the step workflows pdf-ocr, "
        "pdf-alternating-merge and pdf-split. Uncertain cases are left unchanged."
    ),
    on_behalf_of=True,
    execution_timeout=timedelta(hours=6),
)
@uses_connectors(*file_connectors.values())
class PdfScannerProcessorWorkflow:
    """Orchestrates the step workflows over the scanner inbox (flow: module docstring)."""

    @workflows.workflow.entrypoint
    async def run(self, input: PdfScannerProcessorInput) -> dict[str, Any]:
        """Runs the pipeline: park raw scans, OCR, detect, merge, split, name."""
        folder = input.folder_path.rstrip("/") if input.folder_path else ""
        if not folder:
            return {
                "folder": "",
                "applied": input.apply_changes,
                "actions": [],
                "error": (
                    "folder_path missing: pass it at start or set "
                    "SCAN_INBOX_FOLDER in the deployment"
                ),
            }
        connector = input.connector or DEFAULT_FILE_CONNECTOR
        if connector not in file_connectors:
            return {
                "folder": folder,
                "applied": input.apply_changes,
                "actions": [],
                "error": (
                    f"unknown connector {input.connector!r}; available "
                    f"(FILE_CONNECTOR_NAMES): {FILE_CONNECTOR_NAMES}"
                ),
            }
        # Deterministic per run (stable on replay) as prefix of the child IDs.
        run_id = workflows.workflow.uuid4().hex[:12]

        listing = await list_scan_files(input.folder_path, connector)
        scans = listing["scans"]
        if not scans:
            return {
                "folder": folder,
                "applied": input.apply_changes,
                "actions": [],
                "note": "no scan files found",
            }

        actions: list[dict[str, Any]] = []
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_OCR)

        # 1) Park the raw scans before anything modifies them (never in a dry
        #    run); parallel with the shared semaphore.
        if input.apply_changes:

            async def backup_bounded(scan: dict[str, Any]) -> dict[str, Any]:
                async with semaphore:
                    return await backup_scan_file(scan["path"], connector)

            backups = list(await asyncio.gather(*(backup_bounded(s) for s in scans)))
            for scan, backup in zip(scans, backups, strict=True):
                actions.append(
                    {
                        "step": "backup",
                        "from": scan["path"],
                        "backup": backup["backup"],
                    }
                )

        # 2) OCR the whole folder via the pdf-ocr step; it returns per-file
        #    text metadata (with_pages_text) so step 3 needs no second download.
        ocr_result = await workflows.workflow.execute_workflow(
            STEPS["ocr"],
            PdfOcrInput(
                target_path=folder,
                recursive=False,
                keep_original=True,
                apply_changes=input.apply_changes,
                with_pages_text=True,
                connector=connector,
            ),
            execution_id=f"{run_id}-ocr",
        )
        actions.append({"step": "ocr", "result": ocr_result})

        # 3) Text metadata comes from the pdf-ocr step result (no re-download);
        #    analyze-scan-file only as fallback for scans the OCR step did not
        #    cover. Then detect front/back pairs.
        ocr_meta = {
            action.get("path"): action
            for action in ocr_result.get("actions", [])
            if action.get("path")
        }

        async def metadata_bounded(scan: dict[str, Any]) -> dict[str, Any]:
            action = ocr_meta.get(scan["path"])
            if action and action.get("has_text") is not None and "text" in action:
                return {
                    **scan,
                    "page_count": action.get("pages", 0),
                    "has_text": action["has_text"],
                    "text": action["text"],
                    "pages_text": action.get("pages_text") or [action["text"]],
                }
            async with semaphore:
                return await analyze_scan_file(scan, connector)

        analyzed = list(await asyncio.gather(*(metadata_bounded(s) for s in scans)))

        pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
        if input.merge_front_back:
            candidates = detect_front_back(analyzed)

            async def check_bounded(
                pair: tuple[dict[str, Any], dict[str, Any]],
            ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
                first, second = pair
                async with semaphore:
                    check = await front_back_check(
                        first["text"], second["text"], input.model
                    )
                return first, second, check

            checks = list(await asyncio.gather(*(check_bounded(p) for p in candidates)))
            for first, second, check in checks:
                accepted = (
                    check["is_front_back"]
                    and check["confidence"] >= FRONT_BACK_MIN_CONFIDENCE
                )
                if accepted:
                    pairs.append((first, second))
                actions.append(
                    {
                        "step": "front-back",
                        "files": [first["name"], second["name"]],
                        "action": "pair" if accepted else "no_pair",
                        "confidence": check["confidence"],
                        "reason": check["reason"],
                    }
                )

        # 4) Merge confirmed pairs via the pdf-alternating-merge step (parallel),
        #    intermediate name ScanMerged<N>.pdf (N = larger scan number).
        paired_paths = {f["path"] for pair in pairs for f in pair}
        units: list[dict[str, Any]] = [
            {
                "kind": "single",
                "files": [item],
                "path": item["path"],
                "text": item["text"],
                "page_count": item.get("page_count", 0),
                "pages_text": item.get("pages_text") or [item.get("text", "")],
            }
            for item in analyzed
            if item["path"] not in paired_paths
        ]

        if pairs:

            async def merge_bounded(
                index: int, first: dict[str, Any], second: dict[str, Any]
            ) -> dict[str, Any]:
                title = merged_scan_name([first["order"], second["order"]])
                async with semaphore:
                    handle = await workflows.workflow.execute_workflow(
                        STEPS["merge"],
                        PdfAlternatingMergeInput(
                            pairs=[
                                {
                                    "front": first["path"],
                                    "back": second["path"],
                                    "title": title,
                                }
                            ],
                            apply_changes=input.apply_changes,
                            model=input.model,
                            connector=connector,
                        ),
                        execution_id=f"{run_id}-merge-{index}",
                        wait=False,
                    )
                    result = await handle
                entry = result["actions"][0] if result.get("actions") else {}
                return {
                    "step": "merge",
                    "files": [first["name"], second["name"]],
                    "action": entry.get("action", "error"),
                    "to": entry.get("to") or f"{folder}/{title}",
                    "archive": entry.get("archive"),
                    "pages": entry.get("pages"),
                    "error": entry.get("error"),
                }

            merge_actions = list(
                await asyncio.gather(
                    *(
                        merge_bounded(i, first, second)
                        for i, (first, second) in enumerate(pairs)
                    )
                )
            )
            actions.extend(merge_actions)
            for merge_action, (first, second) in zip(merge_actions, pairs, strict=True):
                if merge_action["action"] in ("merged", "planned"):
                    units.append(
                        {
                            "kind": "merged",
                            "files": [first, second],
                            "path": merge_action["to"],
                            "text": merge_texts(first["text"], second["text"]),
                            "page_count": first.get("page_count", 0)
                            + second.get("page_count", 0),
                            "pages_text": (first.get("pages_text") or [])
                            + (second.get("pages_text") or []),
                        }
                    )
                else:
                    # Merge failed: both halves stay as individual scans.
                    for f in (first, second):
                        units.append(
                            {
                                "kind": "single",
                                "files": [f],
                                "path": f["path"],
                                "text": f["text"],
                                "page_count": f.get("page_count", 0),
                                "pages_text": f.get("pages_text")
                                or [f.get("text", "")],
                            }
                        )

        # 5) Split planning inline via the plan_split activity (parallel) - BEFORE
        #    naming: splitting is content-based, not name-based, and the parts
        #    are named from the plan, so naming the pre-split file first would be
        #    a wasted LLM call. The pdf-split child is spawned only when a split
        #    is actually confirmed (with fixed split_at + titles, so the child
        #    does not plan again).
        rules = (await load_naming_rules(folder, connector))["rules"]
        split_results: list[dict[str, Any]] = []
        if input.split_parts:

            async def plan_bounded(index: int, unit: dict[str, Any]) -> dict[str, Any]:
                pages_text = unit.get("pages_text") or [unit.get("text", "")]
                async with semaphore:
                    plan = await plan_split(
                        pages_text,
                        unit.get("page_count", len(pages_text)),
                        None,
                        input.model,
                        rules,
                    )
                kind, details = resolve_plan(plan)
                record: dict[str, Any] = {
                    "step": "split",
                    "files": [f["name"] for f in unit["files"]],
                    "target": unit["path"],
                    "action": kind,
                    "plan": {
                        "split_at": details.get("split_at", plan.get("split_at", [])),
                        "titles": details.get("titles", plan.get("titles")),
                        "confidence": plan.get("confidence", 0.0),
                        "needs_input": plan.get("needs_input", False),
                        "question": details.get("question") or plan.get("question"),
                    },
                }
                spawn = kind == "split" and not (
                    unit["kind"] == "merged" and not input.apply_changes
                )
                if spawn:
                    # Confirmed split: hand the fixed plan to the pdf-split child.
                    async with semaphore:
                        handle = await workflows.workflow.execute_workflow(
                            STEPS["split"],
                            PdfSplitInput(
                                target_path=unit["path"],
                                split_at=details["split_at"],
                                titles=details.get("titles"),
                                apply_changes=input.apply_changes,
                                model=input.model,
                                connector=connector,
                            ),
                            execution_id=f"{run_id}-split-{index}",
                            wait=False,
                        )
                        result = await handle
                    record["action"] = result.get("action", kind)
                    record["result"] = result
                elif kind == "split":
                    # Dry run, merged unit: the merged file is only planned, not
                    # created, so no child - report the confirmed plan as planned.
                    record["action"] = "planned"
                return record

            split_results = list(
                await asyncio.gather(
                    *(plan_bounded(i, unit) for i, unit in enumerate(units))
                )
            )
            actions.extend(split_results)

        # 6) Naming: LLM title + date per unit that was NOT split (merged pairs
        #    via combined text; parts of split units are named from the split
        #    plan). LLM calls in parallel, unique-name assignment and renames
        #    sequential. Rename only when confident.
        taken = set(listing["existing_names"])
        for unit in units:
            if unit["kind"] == "merged":
                # str ops only: the workflow sandbox restricts os.path
                taken.add(unit["path"].rsplit("/", 1)[-1])
        name_units = [
            unit
            for index, unit in enumerate(units)
            if not (
                index < len(split_results)
                and split_results[index].get("action") in ("split", "planned")
            )
        ]

        async def name_bounded(unit: dict[str, Any]) -> dict[str, Any]:
            async with semaphore:
                return await name_document(unit["text"], input.model, rules)

        namings = list(await asyncio.gather(*(name_bounded(u) for u in name_units)))
        for unit, naming in zip(name_units, namings, strict=True):
            names = [f["name"] for f in unit["files"]]
            if naming["confidence"] < MIN_NAMING_CONFIDENCE or not naming["title"]:
                actions.append(
                    {
                        "step": "name",
                        "files": names,
                        "action": "left_unchanged",
                        "path": unit["path"],
                        "confidence": naming["confidence"],
                        "reason": "naming or date not confident enough",
                    }
                )
                continue
            target_name = unique_filename(
                build_filename(naming["date"], naming["title"]), taken
            )
            taken.add(target_name)
            target_path = f"{folder}/{target_name}"
            if not input.apply_changes:
                actions.append(
                    {
                        "step": "name",
                        "files": names,
                        "action": "planned",
                        "from": unit["path"],
                        "to": target_path,
                        "confidence": naming["confidence"],
                    }
                )
                continue
            await rename_scan_file(unit["path"], target_path, connector)
            actions.append(
                {
                    "step": "name",
                    "files": names,
                    "action": "renamed",
                    "from": unit["path"],
                    "to": target_path,
                    "confidence": naming["confidence"],
                }
            )
        # Units that were split: their parts are already named from the split
        # plan (named_by_split) - record that instead of renaming.
        for index, unit in enumerate(units):
            if index < len(split_results) and split_results[index].get("action") in (
                "split",
                "planned",
            ):
                split_result = split_results[index]
                parts = [
                    str(part.get("to", "")).rsplit("/", 1)[-1]
                    for part in (split_result.get("result") or {}).get("parts", [])
                ] or list(split_result.get("plan", {}).get("titles") or [])
                actions.append(
                    {
                        "step": "name",
                        "files": [f["name"] for f in unit["files"]],
                        "action": "named_by_split",
                        "path": unit["path"],
                        "parts": parts,
                    }
                )

        return {"folder": folder, "applied": input.apply_changes, "actions": actions}
