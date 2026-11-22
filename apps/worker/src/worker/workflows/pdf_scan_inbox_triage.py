"""Sorts scans from the Nextcloud scanner inbox.

The inbox folder is given at start via folder_path; if omitted,
the default lies in the environment variable SCAN_INBOX_FOLDER (bound in the
deployment as a workspace secret). The start parameter always wins.

connector: name of the file connector from FILE_CONNECTOR_NAMES (empty = default);
with several active Nextcloud connectors the caller selects the target connector.

Per run, first folder level only:

1. Find new scans "Scan.pdf" / "ScanNNNN.pdf".
2. OCR every PDF without a text layer (Tesseract deu+eng, deskew,
   automatic page rotation, see worker/ocr.py), replace the content in
   Nextcloud. All files are analyzed in parallel first, before anything
   is named.
3. Detect duplex pairs (two consecutive scans, same page count,
   similar size, high text overlap). Pairs are NOT merged here: both files
   get the same LLM title plus a front/back suffix in the document's
   language, so they can be passed manually to pdf-merge-duplex via a
   control file later.
4. Name "YYYY-MM-DD short descriptive title.pdf" (or only a title without a
   reliable date) via LLM from the OCR text, in the document's language.
5. Rename only. Everything uncertain (pair assignment, content, date, naming)
   stays unchanged. This workflow never merges, archives or deletes anything.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import timedelta
from typing import Any

import pydantic
from mistralai import workflows
from mistralai.workflows.plugins.mistralai.connectors import uses_connectors

from worker.ocr import read_text_info, run_ocrmypdf, run_with_heartbeat
from worker.pdf_ops import (
    DEFAULT_FILE_CONNECTOR,
    FILE_CONNECTOR_NAMES,
    MAX_CONCURRENT_OCR,
    MIN_NAMING_CONFIDENCE,
    TEXT_SNIPPET_CHARS,
    build_filename,
    detect_pairs,
    file_client,
    file_connectors,
    front_back_suffixes,
    http_get,
    http_put,
    load_naming_rules,
    merge_texts,
    name_document,
    nc_call,
    pick_url,
    unique_filename,
)

# Default folder from the deployment (workspace secret, environment variable);
# a folder_path given at start always takes precedence.
DEFAULT_FOLDER = os.environ.get("SCAN_INBOX_FOLDER", "")
SCAN_NAME_RE = re.compile(r"^Scan(\d{4})?\.pdf$", re.IGNORECASE)

HEARTBEAT_TIMEOUT = timedelta(seconds=120)

logger = logging.getLogger("worker.pdf_scan_inbox_triage")


class PdfScanInboxTriageInput(pydantic.BaseModel):
    """Start input: inbox folder, apply_changes and model."""

    folder_path: str = DEFAULT_FOLDER
    apply_changes: bool = True
    model: str = "mistral-small-latest"
    connector: str = ""


def scan_order(name: str) -> int | None:
    """Scan number from the name; Scan.pdf counts as first (-1), otherwise None."""
    match = SCAN_NAME_RE.match(name)
    if not match:
        return None
    digits = match.group(1)
    return -1 if digits is None else int(digits)


@workflows.activity(name="list-scan-files", start_to_close_timeout=timedelta(minutes=5))
async def list_scan_files(folder_path: str, connector: str = "") -> dict[str, Any]:
    """Lists the inbox folder: found scans and all existing file names."""
    nc = file_client(connector)
    result = await nc_call(nc, "files_list", {"path": folder_path})
    if isinstance(result, dict):
        entries = next((v for v in result.values() if isinstance(v, list)), [])
    else:
        entries = result
    scans: list[dict[str, Any]] = []
    existing_names: list[str] = []
    for entry in entries:
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
    start_to_close_timeout=timedelta(minutes=45),
    heartbeat_timeout=HEARTBEAT_TIMEOUT,
    retry_policy_max_attempts=2,
)
async def analyze_scan_file(
    entry: dict[str, Any], apply_changes: bool, connector: str = ""
) -> dict[str, Any]:
    """Loads a scan, OCRs it when the text layer is missing and replaces it in Nextcloud."""
    nc = file_client(connector)
    import os
    import shutil
    import tempfile

    logger.info("analyzing %s", entry["path"])
    workdir = tempfile.mkdtemp(prefix="scan-")
    try:
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
        ocr_done = False
        out_path = local
        if not info["has_text"]:
            out_path = os.path.join(workdir, "ocr.pdf")
            run = await run_ocrmypdf(local, out_path)
            if run["ok"]:
                ocr_done = True
                info = read_text_info(out_path)

        if ocr_done and apply_changes:
            replacement = await nc_call(
                nc,
                "files_checkout",
                {"path": entry["path"], "confirm": True, "confirm_shared": True},
            )
            await run_with_heartbeat(
                http_put,
                pick_url(replacement, "upload_url", "uploadUrl"),
                out_path,
                detail={"phase": "upload", "file": entry["name"]},
            )

        logger.info(
            "analysis done: %s (ocr=%s text=%s pages=%s)",
            entry["name"],
            ocr_done,
            info["has_text"],
            info["page_count"],
        )
        return {
            "name": entry["name"],
            "path": entry["path"],
            "size": entry["size"],
            "etag": entry.get("etag"),
            "order": entry["order"],
            "page_count": info["page_count"],
            "has_text": info["has_text"],
            "ocr_done": ocr_done,
            "text": "\n".join(info["pages"])[:TEXT_SNIPPET_CHARS],
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


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
    name="pdf-scan-inbox-triage",
    workflow_display_name="Sort PDF scan inbox",
    workflow_description=(
        "OCR and naming of new scans (Scan.pdf / ScanNNNN.pdf) in the "
        "Nextcloud scanner inbox. Duplex pairs are detected and named with a "
        "front/back suffix in the document's language, but not merged. "
        "Uncertain cases are left unchanged."
    ),
    on_behalf_of=True,
    execution_timeout=timedelta(hours=3),
)
@uses_connectors(*file_connectors.values())
class PdfScanInboxTriageWorkflow:
    """OCR the inbox scans, detect duplex pairs and rename only confidently named ones."""

    @workflows.workflow.entrypoint
    async def run(self, input: PdfScanInboxTriageInput) -> dict[str, Any]:
        """Analyzes all scans in parallel, detects pairs and renames only confident hits."""
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
        listing = await list_scan_files(input.folder_path, connector)
        scans = listing["scans"]
        if not scans:
            return {
                "folder": folder,
                "applied": input.apply_changes,
                "actions": [],
                "note": "no scan files found",
            }

        # All files are analyzed in parallel: downloads/uploads of one file overlap
        # with the OCR run of another. asyncio.gather keeps the result order aligned
        # with the scan order, so pairing and naming below stay deterministic.
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_OCR)

        async def analyze_bounded(scan: dict[str, Any]) -> dict[str, Any]:
            async with semaphore:
                return await analyze_scan_file(scan, input.apply_changes, connector)

        analyzed = list(
            await asyncio.gather(*(analyze_bounded(scan) for scan in scans))
        )
        pairs = detect_pairs(analyzed)
        paired_paths = {f["path"] for pair in pairs for f in pair}
        units: list[dict[str, Any]] = []
        for item in analyzed:
            if item["path"] not in paired_paths:
                units.append({"kind": "single", "files": [item], "text": item["text"]})
        for first, second in pairs:
            units.append(
                {
                    "kind": "pair",
                    "files": [first, second],
                    "text": merge_texts(first["text"], second["text"]),
                }
            )

        rules = (await load_naming_rules(folder, connector))["rules"]

        taken = set(listing["existing_names"])
        actions: list[dict[str, Any]] = []
        for unit in units:
            naming = await name_document(unit["text"], input.model, rules)
            files = unit["files"]
            names = [f["name"] for f in files]
            if naming["confidence"] < MIN_NAMING_CONFIDENCE or not naming["title"]:
                actions.append(
                    {
                        "action": "left_unchanged",
                        "files": names,
                        "kind": unit["kind"],
                        "ocr": any(f["ocr_done"] for f in files),
                        "pages": files[0]["page_count"],
                        "confidence": naming["confidence"],
                        "reason": "naming or date not confident enough",
                    }
                )
                continue

            if unit["kind"] == "single":
                target_name = unique_filename(
                    build_filename(naming["date"], naming["title"]), taken
                )
                taken.add(target_name)
                target_path = f"{folder}/{target_name}"
                if not input.apply_changes:
                    actions.append(
                        {
                            "action": "planned",
                            "files": names,
                            "to": target_path,
                            "ocr": files[0]["ocr_done"],
                            "pages": files[0]["page_count"],
                            "confidence": naming["confidence"],
                        }
                    )
                    continue
                await rename_scan_file(files[0]["path"], target_path, connector)
                actions.append(
                    {
                        "action": "renamed",
                        "files": names,
                        "to": target_path,
                        "ocr": files[0]["ocr_done"],
                        "pages": files[0]["page_count"],
                        "confidence": naming["confidence"],
                    }
                )
            else:
                base = naming["title"]
                front_suffix, back_suffix = front_back_suffixes(unit["text"])
                front_name = unique_filename(
                    build_filename(naming["date"], f"{base} {front_suffix}"), taken
                )
                taken.add(front_name)
                back_name = unique_filename(
                    build_filename(naming["date"], f"{base} {back_suffix}"), taken
                )
                taken.add(back_name)
                front_to = f"{folder}/{front_name}"
                back_to = f"{folder}/{back_name}"
                pair_action = {
                    "files": names,
                    "to": [front_to, back_to],
                    "ocr": any(f["ocr_done"] for f in files),
                    "pages": files[0]["page_count"],
                    "confidence": naming["confidence"],
                }
                if not input.apply_changes:
                    actions.append({"action": "planned_pair", **pair_action})
                    continue
                await rename_scan_file(files[0]["path"], front_to, connector)
                await rename_scan_file(files[-1]["path"], back_to, connector)
                actions.append({"action": "renamed_pair", **pair_action})

        return {"folder": folder, "applied": input.apply_changes, "actions": actions}
