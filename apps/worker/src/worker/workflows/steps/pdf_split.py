"""Splitting of a PDF into parts (pdf-split).

Start input:

- target_path: path of exactly one PDF file in Nextcloud
- split_at: page numbers (1-based) AFTER which to split, e.g. [2, 4]
  yields parts 1-2, 3-4, 5-8 for 8 pages. Without split_at the
  LLM plans split points and part titles from the OCR texts (AI mode).
  An explicit split_at always wins over the plan.
- plan_splits (default true): AI planning of the split points. With false and
  no split_at the run aborts with an error message.
- titles: optional labels per part (count must match the number of parts).
  Explicit titles take precedence and disable AI naming.
- name_parts (default true): AI naming of the parts. With false the
  parts are called "<stem> Part-N.pdf".
- model: LLM for the planning (default mistral-small-latest)
- apply_changes: dry run reads and checks everything, writes nothing, and
  reports the planned parts with their exact page ranges ("parts[].pages")
- connector: name of the file connector from FILE_CONNECTOR_NAMES (empty = default);
  with several active Nextcloud connectors the caller selects the target connector

Naming rules: before AI naming the workflow reads an AGENTS.md file
from the target PDF's folder and passes it to the model as
additional, folder-specific rules (max. 4000 characters). If the
file is missing, the model names using only the base prompt. AI-named parts
are called "<title>.pdf" (without the original stem prefix); the original
is stored with its full name under .originals/pdf-split/.

In AI mode (no split_at) the workflow answers with
action="clarification" (question plus proposal, nothing is written)
when the model is unsure or asks questions. action="no_split"
reports that everything is one coherent document. Otherwise the
proposed split points and titles are applied.

The parts are created in the same folder as the original file. The original
is moved as a backup to "<folder>/.originals/pdf-split/<stem>/".
".originals" is the uniform storage location for originals of all workflows
(one subfolder per workflow, one further folder per file). The
workflow never overwrites anything: existing names get counter suffixes.

The workflow is started directly (Studio or chat) or called by
pdf-scanner-processor as a child workflow. Long activities send
heartbeats and are retried on worker restart.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from datetime import timedelta
from typing import Any, cast

import pydantic
from mistralai import workflows
from mistralai.workflows.plugins.mistralai.connectors import uses_connectors

from worker.ocr import run_with_heartbeat
from worker.pdf_ops import (
    DEFAULT_FILE_CONNECTOR,
    FILE_CONNECTOR_NAMES,
    MIN_NAMING_CONFIDENCE,
    ensure_folder,
    extract_texts,
    file_client,
    file_connectors,
    http_get,
    http_put,
    list_names,
    listing_entries,
    load_naming_rules,
    nc_call,
    originals_dir,
    part_ranges,
    pick_url,
    plan_split,
    sanitize_title,
    split_pdf_into_ranges,
    unique_filename,
)

HEARTBEAT_TIMEOUT = timedelta(seconds=120)
logger = logging.getLogger("worker.pdf_split")


class PdfSplitInput(pydantic.BaseModel):
    """Start input of the workflow; the fields are explained in the module docstring."""

    target_path: str
    split_at: list[int] | None = pydantic.Field(
        default=None,
        description=(
            "Page numbers (1-based) AFTER which the PDF is cut; the given page "
            "stays in the previous part. Example: [2, 4] on 8 pages yields "
            "parts 1-2, 3-4, 5-8. An explicit split_at always wins over AI "
            "planning. A dry run (apply_changes=false) reports the page range "
            "of every part before anything is written."
        ),
    )
    plan_splits: bool = True
    titles: list[str] | None = None
    name_parts: bool = True
    model: str = "mistral-small-latest"
    apply_changes: bool = True
    connector: str = ""


def resolve_plan(plan: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Evaluates an LLM plan proposal.

    Returns ("split", {"split_at": [...], "titles": [...] | None}) when it
    should be applied, otherwise ("clarification" | "no_split", details).
    """
    if plan.get("needs_input") or plan.get("confidence", 0.0) < MIN_NAMING_CONFIDENCE:
        details = {
            "question": plan.get("question") or "split not confident enough",
            "confidence": plan.get("confidence", 0.0),
            "proposal": {
                "split_at": plan.get("split_at", []),
                "titles": plan.get("titles", []),
            },
        }
        return "clarification", details
    if not plan.get("split_at"):
        return "no_split", {
            "titles": plan.get("titles", []),
            "confidence": plan.get("confidence", 0.0),
            "note": "everything is one coherent document",
        }
    return "split", {
        "split_at": plan["split_at"],
        "titles": plan.get("titles") or None,
    }


def part_filename(stem: str, number: int, title: str | None) -> str:
    """File name of a part.

    With an (AI or explicit) title the part is called "<title>.pdf" without
    the original stem prefix; without a title "<stem> Part-N.pdf". A
    title consisting only of whitespace counts as a missing title.
    """
    if title is not None and title.strip():
        # sanitize_title never returns an empty string (fallback "Document")
        return f"{sanitize_title(title)}.pdf"
    return f"{stem} Part-{number}.pdf"


@workflows.activity(
    name="load-split-pages",
    start_to_close_timeout=timedelta(minutes=15),
    heartbeat_timeout=HEARTBEAT_TIMEOUT,
    retry_policy_max_attempts=2,
)
async def load_pages(target_path: str, connector: str = "") -> dict[str, Any]:
    """Loads the PDF and returns texts and page count; writes nothing."""
    nc = file_client(connector)
    base_name = os.path.basename(target_path)
    stem = (
        base_name[: -len(".pdf")] if base_name.lower().endswith(".pdf") else base_name
    )
    parent = os.path.dirname(target_path)
    workdir = tempfile.mkdtemp(prefix="load-")
    try:
        logger.info("loading %s", base_name)
        try:
            checkout = await nc_call(
                nc,
                "files_checkout",
                {"path": target_path, "confirm": True, "confirm_shared": True},
            )
            local = os.path.join(workdir, "original.pdf")
            await run_with_heartbeat(
                http_get,
                pick_url(checkout, "download_url", "downloadUrl"),
                local,
                detail={"phase": "download", "file": base_name},
            )
        except Exception as exc:
            return {"action": "error", "error": f"source file not loadable: {exc}"}
        try:
            pages, page_count = extract_texts(local)
        except Exception as exc:
            return {"action": "error", "error": f"PDF not readable: {exc}"}
        logger.info("loaded: %s (%d pages)", base_name, page_count)
        return {
            "action": "loaded",
            "base_name": base_name,
            "stem": stem,
            "parent": parent,
            "pages": pages,
            "page_count": page_count,
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@workflows.activity(
    name="split-file",
    start_to_close_timeout=timedelta(minutes=45),
    heartbeat_timeout=HEARTBEAT_TIMEOUT,
    retry_policy_max_attempts=2,
)
async def split_file(
    target_path: str,
    split_at: list[int],
    titles: list[str] | None,
    apply_changes: bool,
    connector: str = "",
) -> dict[str, Any]:
    """Splits the PDF at the given split points and secures the original."""
    nc = file_client(connector)
    base_name = os.path.basename(target_path)
    stem = (
        base_name[: -len(".pdf")] if base_name.lower().endswith(".pdf") else base_name
    )
    parent = os.path.dirname(target_path)

    def report(action: str, **extra: Any) -> dict[str, Any]:
        return {
            "target": target_path,
            "applied": apply_changes,
            "action": action,
            "parts": [],
            **extra,
        }

    workdir = tempfile.mkdtemp(prefix="split-")
    try:
        logger.info("split start: %s (split_at=%s)", base_name, split_at)
        try:
            checkout = await nc_call(
                nc,
                "files_checkout",
                {"path": target_path, "confirm": True, "confirm_shared": True},
            )
            local = os.path.join(workdir, "original.pdf")
            await run_with_heartbeat(
                http_get,
                pick_url(checkout, "download_url", "downloadUrl"),
                local,
                detail={"phase": "download", "file": base_name},
            )
        except Exception as exc:
            return report("error", error=f"source file not loadable: {exc}")

        try:
            _, page_count = extract_texts(local)
        except Exception as exc:
            return report("error", error=f"PDF not readable: {exc}")

        try:
            ranges = part_ranges(page_count, split_at)
        except ValueError as exc:
            return report("error", pages=page_count, error=str(exc))

        clean_titles: list[str] = []
        if titles is not None:
            clean_titles = [str(title) for title in titles]
            if len(clean_titles) != len(ranges):
                return report(
                    "error",
                    pages=page_count,
                    error=(
                        f"titles has {len(clean_titles)} entries, "
                        f"expected {len(ranges)} parts"
                    ),
                )

        listing = await nc_call(nc, "files_list", {"path": parent})
        taken = {
            cast("str", entry.get("name"))
            for entry in listing_entries(listing)
            if isinstance(entry, dict)
        }
        taken.discard(base_name)

        parts: list[dict[str, Any]] = []
        for number, (start, end) in enumerate(ranges, start=1):
            title = clean_titles[number - 1] if clean_titles else None
            part_name = unique_filename(part_filename(stem, number, title), taken)
            taken.add(part_name)
            parts.append(
                {
                    "pages": f"{start}-{end}",
                    "count": end - start + 1,
                    "to": f"{parent}/{part_name}",
                }
            )

        if not apply_changes:
            logger.info("split planned: %s -> %d parts", base_name, len(parts))
            return report("planned", pages=page_count, parts=parts)

        out_paths = [
            os.path.join(workdir, f"part-{number}.pdf")
            for number in range(1, len(parts) + 1)
        ]
        try:
            split_pdf_into_ranges(local, ranges, out_paths)
        except Exception as exc:
            return report("error", error=f"splitting failed: {exc}")

        for part, out_path in zip(parts, out_paths):
            size = os.path.getsize(out_path)
            upload = await nc_call(
                nc,
                "files_upload",
                {
                    "path": part["to"],
                    "size": size,
                    "confirm": True,
                    "confirm_shared": True,
                },
            )
            await run_with_heartbeat(
                http_put,
                pick_url(upload, "upload_url", "uploadUrl"),
                out_path,
                detail={"phase": "upload", "file": os.path.basename(part["to"])},
            )

        originals_root = originals_dir(parent, "pdf-split")
        await ensure_folder(nc, originals_root)
        arch_names = await list_names(nc, originals_root)
        park_dir = f"{originals_root}/{unique_filename(stem, arch_names)}"
        try:
            await nc_call(
                nc,
                "files_mkdir",
                {"path": park_dir, "confirm": True, "confirm_shared": True},
            )
        except Exception as setup_error:
            logger.debug(
                "files_mkdir %s skipped (probably exists): %s",
                park_dir,
                setup_error,
            )
        moved_to = f"{park_dir}/{base_name}"
        await nc_call(
            nc,
            "files_move",
            {
                "from": target_path,
                "to": moved_to,
                "confirm": True,
                "confirm_shared": True,
            },
        )

        logger.info(
            "split done: %s -> %d parts, original to %s",
            base_name,
            len(parts),
            moved_to,
        )
        return report("split", pages=page_count, parts=parts, original=moved_to)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@workflows.workflow.define(
    name="pdf-split",
    workflow_display_name="Split PDF",
    workflow_description=(
        "Splits a PDF into parts: at explicitly given page numbers or "
        "automatically planned by an LLM from the OCR texts (split points and "
        "part titles). split_at lists the page numbers (1-based) AFTER which "
        "the PDF is cut - the given page stays in the previous part (example: "
        "[2, 4] on 8 pages yields parts 1-2, 3-4, 5-8). A dry run "
        "(apply_changes=false) reports the page range of every part before "
        "anything is written. Naming rules may live as AGENTS.md in the folder. "
        "When unsure, the workflow asks back without writing. Parts stay in the "
        "original file's folder, the original is secured under "
        ".originals/pdf-split/."
    ),
    on_behalf_of=True,
    execution_timeout=timedelta(hours=1),
)
@uses_connectors(*file_connectors.values())
class PdfSplitWorkflow:
    """Split a PDF into parts, explicit or LLM-planned (flow: module docstring)."""

    @workflows.workflow.entrypoint
    async def run(self, input: PdfSplitInput) -> dict[str, Any]:
        """Loads the PDF, clarifies split points and titles, then splits."""
        if not input.target_path.lower().endswith(".pdf"):
            return {
                "target": input.target_path,
                "applied": input.apply_changes,
                "action": "error",
                "parts": [],
                "error": "target_path must point to a PDF file",
            }

        connector = input.connector or DEFAULT_FILE_CONNECTOR
        if connector not in file_connectors:
            return {
                "target": input.target_path,
                "applied": False,
                "action": "error",
                "parts": [],
                "error": (
                    f"unknown connector {input.connector!r}; available "
                    f"(FILE_CONNECTOR_NAMES): {FILE_CONNECTOR_NAMES}"
                ),
            }

        loaded = await load_pages(input.target_path, connector)
        if loaded.get("action") != "loaded":
            return {
                "target": input.target_path,
                "applied": input.apply_changes,
                "action": "error",
                "parts": [],
                "error": loaded.get("error", "source file not loadable"),
            }

        page_count = loaded["page_count"]
        split_at = input.split_at
        titles = input.titles

        if split_at is None and not input.plan_splits:
            return {
                "target": input.target_path,
                "applied": False,
                "action": "error",
                "parts": [],
                "error": (
                    "plan_splits is disabled, but no split points (split_at) were given"
                ),
            }

        # Load naming rules only when the AI should name something.
        rules = ""
        if (split_at is None and input.plan_splits) or (
            split_at is not None and titles is None and input.name_parts
        ):
            rules_doc = await load_naming_rules(loaded["parent"], connector)
            rules = rules_doc["rules"]

        if split_at is None:
            # AI mode: let the LLM plan split points and titles.
            plan = await plan_split(
                loaded["pages"], page_count, None, input.model, rules
            )
            kind, details = resolve_plan(plan)
            if kind != "split":
                return {
                    "target": input.target_path,
                    # Nothing was written, so applied=false here always,
                    # regardless of the requested apply_changes.
                    "applied": False,
                    "action": kind,
                    "mode": "auto",
                    "pages": page_count,
                    "parts": [],
                    **details,
                }
            split_at = details["split_at"]
            if input.name_parts:
                titles = details["titles"]
            else:
                # Naming disabled: discard the plan's titles, the
                # parts get the Part-N fallback.
                titles = None
        elif titles is None and input.name_parts:
            # Explicit split points without titles: LLM proposes part names;
            # insecure proposals fall back to Part-N.
            naming = await plan_split(
                loaded["pages"], page_count, split_at, input.model, rules
            )
            if (
                not naming.get("needs_input")
                and naming.get("confidence", 0.0) >= MIN_NAMING_CONFIDENCE
                and naming.get("titles")
            ):
                titles = naming["titles"]

        return await split_file(
            input.target_path, split_at, titles, input.apply_changes, connector
        )
