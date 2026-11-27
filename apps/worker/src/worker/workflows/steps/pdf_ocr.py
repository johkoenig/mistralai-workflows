"""Generic PDF OCR processor: one file, one folder or a folder tree.

target_path is either a single PDF file (extension .pdf, case
insensitive) or a folder. For a folder, all PDFs of the first level are
processed, with recursive=true also all subfolders.

Per file:

- PDF without a text layer -> OCR (Tesseract deu+eng, deskew, page rotation,
  see worker/ocr.py); the result replaces the file in Nextcloud.
- PDF with a text layer -> skipped; force=true forces reprocessing.
- keep_original (default true): before the file is replaced, the original is
  moved to <folder>/.originals/pdf-ocr/<name> and kept; with keep_original=false
  the original is overwritten. If a backup already exists without a target
  file, a previously interrupted run is continued; if both exist, the file
  stays with an error message.
- In folder mode the .originals folder is never descended into, so backups
  are never reprocessed.

Only PDFs (case insensitive), no images. At most MAX_FILES_PER_RUN files are
processed per run; the rest is reported and skipped (please process large
batches locally).
apply_changes=false (dry run) executes OCR and checks fully,
but writes nothing back.

connector: name of the file connector from FILE_CONNECTOR_NAMES (empty = default);
with several active Nextcloud connectors the caller selects the target connector.
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
from mistralai.workflows.plugins.mistralai.connectors import (
    ToolCallClient,
    uses_connectors,
)

from worker.ocr import OCR_LANGUAGES, read_text_info, run_ocrmypdf, run_with_heartbeat
from worker.pdf_ops import (
    DEFAULT_FILE_CONNECTOR,
    FILE_CONNECTOR_NAMES,
    MAX_CONCURRENT_OCR,
    ORIGINALS_DIR_NAME,
    SPLIT_TEXT_PER_PAGE,
    TEXT_SNIPPET_CHARS,
    assess_ocr_quality,
    ensure_folder,
    file_client,
    file_connectors,
    http_get,
    http_put,
    list_names,
    listing_entries,
    nc_call,
    originals_dir,
    pick_url,
)

MAX_FILES_PER_RUN = 200
MAX_PAGES_TEXT = 100  # cap for the per-page text snippets (with_pages_text)
HEARTBEAT_TIMEOUT = timedelta(seconds=120)

logger = logging.getLogger("worker.pdf_ocr")


class PdfOcrInput(pydantic.BaseModel):
    """Start input: target, recursion, force, keep_original and languages."""

    target_path: str
    recursive: bool = False
    force: bool = False
    keep_original: bool = True
    apply_changes: bool = True
    languages: str = OCR_LANGUAGES
    connector: str = ""
    with_pages_text: bool = False
    """When true, every result action also carries has_text, a text snippet and
    pages_text (per page, capped) so pipeline callers (pdf-scanner-processor)
    need no second download. Off by default to keep folder-run results small."""


def is_pdf_name(name: str) -> bool:
    """True if the name ends in .pdf (case insensitive)."""
    return name.lower().endswith(".pdf")


def split_entries(
    folder: str, entries: list[Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Splits a folder listing into PDF files and subfolder paths.

    The .originals folder is skipped: backups stored there are never
    reprocessed, even with recursive=true.
    """
    files: list[dict[str, Any]] = []
    subfolders: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name") or ""
        if not name:
            continue
        if entry.get("isDir"):
            if name != ORIGINALS_DIR_NAME:
                subfolders.append(f"{folder}/{name}")
        elif is_pdf_name(name):
            files.append(
                {
                    "name": name,
                    "path": f"{folder}/{name}",
                    "size": int(entry.get("size") or 0),
                }
            )
    return files, subfolders


@workflows.activity(
    name="ocr-list-targets",
    start_to_close_timeout=timedelta(minutes=15),
    retry_policy_max_attempts=2,
)
async def list_targets(
    target_path: str, recursive: bool, connector: str = ""
) -> dict[str, Any]:
    """Single file (extension .pdf) or folder (contents) listing as targets."""
    nc = file_client(connector)
    if is_pdf_name(target_path):
        return {
            "kind": "file",
            "files": [
                {
                    "name": os.path.basename(target_path),
                    "path": target_path,
                    "size": 0,
                }
            ],
            "total": 1,
            "truncated": False,
        }

    folder = target_path.rstrip("/")
    files: list[dict[str, Any]] = []
    pending = [folder]
    while pending:
        current = pending.pop(0)
        result = await nc_call(nc, "files_list", {"path": current})
        current_files, subfolders = split_entries(current, listing_entries(result))
        files += current_files
        if recursive:
            pending += subfolders
    files.sort(key=lambda f: f["path"])
    total = len(files)
    return {
        "kind": "folder",
        "files": files[:MAX_FILES_PER_RUN],
        "total": total,
        "truncated": total > MAX_FILES_PER_RUN,
    }


def _text_metadata(info: dict[str, Any], with_pages_text: bool) -> dict[str, Any]:
    """has_text + text snippet (+ per-page snippets) for the result action."""
    meta: dict[str, Any] = {
        "has_text": info["has_text"],
        "text": "\n".join(info["pages"])[:TEXT_SNIPPET_CHARS],
    }
    if with_pages_text:
        meta["pages_text"] = [
            (page or "")[:SPLIT_TEXT_PER_PAGE] for page in info["pages"]
        ][:MAX_PAGES_TEXT]
    return meta


async def _process_file_inner(
    entry: dict[str, Any],
    force: bool,
    keep_original: bool,
    apply_changes: bool,
    languages: str,
    nc: ToolCallClient,
    with_pages_text: bool = False,
) -> dict[str, Any]:
    """Loads a PDF, checks the text layer, OCRs it and writes it back."""
    workdir = tempfile.mkdtemp(prefix="pdfocr-")
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
        report: dict[str, Any] = {
            "path": entry["path"],
            "pages": info["page_count"],
            **_text_metadata(info, with_pages_text),
        }
        if info["has_text"] and not force:
            report["action"] = "skipped"
            report["reason"] = "text layer present"
            return report

        out_path = os.path.join(workdir, "ocr.pdf")
        run = await run_ocrmypdf(local, out_path, languages=languages)
        if not run["ok"]:
            report["action"] = "error"
            report["error"] = run["error"]
            return report
        new_info = read_text_info(out_path)
        report["pages"] = new_info["page_count"]
        report.update(_text_metadata(new_info, with_pages_text))
        if not new_info["has_text"]:
            report["action"] = "error"
            report["error"] = "OCR ran through, but no text layer was produced"
            return report
        if not apply_changes:
            report["action"] = "planned"
            report["keep_original"] = keep_original
            return report

        size = os.path.getsize(out_path)
        if keep_original:
            parent = os.path.dirname(entry["path"])
            originals_root = originals_dir(parent, "pdf-ocr")
            orig_path = f"{originals_root}/{entry['name']}"
            await ensure_folder(nc, originals_root)
            if entry["name"] in await list_names(nc, originals_root):
                if entry["name"] in await list_names(nc, parent):
                    report["action"] = "error"
                    report["error"] = (
                        f"backup already exists: {orig_path} "
                        "(and target file as well); please clean up"
                    )
                    return report
                # Backup without target file: a previous run was interrupted
                # after the move; continue the upload now.
            else:
                await nc_call(
                    nc,
                    "files_move",
                    {
                        "from": entry["path"],
                        "to": orig_path,
                        "confirm": True,
                        "confirm_shared": True,
                    },
                )
            upload = await nc_call(
                nc,
                "files_upload",
                {
                    "path": entry["path"],
                    "size": size,
                    "confirm": True,
                    "confirm_shared": True,
                },
            )
            await run_with_heartbeat(
                http_put,
                pick_url(upload, "upload_url", "uploadUrl"),
                out_path,
                detail={"phase": "upload", "file": entry["name"]},
            )
            report["action"] = "replaced"
            report["original"] = orig_path
            return report

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
        report["action"] = "replaced"
        return report
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@workflows.activity(
    name="ocr-process-file",
    start_to_close_timeout=timedelta(minutes=45),
    heartbeat_timeout=HEARTBEAT_TIMEOUT,
    retry_policy_max_attempts=2,
)
async def process_file(
    entry: dict[str, Any],
    force: bool,
    keep_original: bool,
    apply_changes: bool,
    languages: str,
    connector: str = "",
    with_pages_text: bool = False,
) -> dict[str, Any]:
    """Processes one PDF file completely; log wrapper around _process_file_inner."""
    nc = file_client(connector)
    logger.info(
        "processing %s (force=%s keep_original=%s apply=%s)",
        entry["path"],
        force,
        keep_original,
        apply_changes,
    )
    report = await _process_file_inner(
        entry, force, keep_original, apply_changes, languages, nc, with_pages_text
    )
    logger.info(
        "%s -> %s (%s)",
        entry["path"],
        report.get("action"),
        report.get("reason") or report.get("error") or "ok",
    )
    return report


@workflows.workflow.define(
    name="pdf-ocr",
    workflow_display_name="OCR-process PDFs",
    workflow_description=(
        "Generic PDF OCR processor: a single PDF or a folder "
        "(optionally with subfolders). PDFs without a text layer are processed "
        "with Tesseract (deskew, page rotation). keep_original (default true) "
        "secures the original under .originals/pdf-ocr/."
    ),
    on_behalf_of=True,
    execution_timeout=timedelta(hours=3),
)
@uses_connectors(*file_connectors.values())
class PdfOcrWorkflow:
    """OCR processing of one file or a folder (flow: module docstring)."""

    @workflows.workflow.entrypoint
    async def run(self, input: PdfOcrInput) -> dict[str, Any]:
        """Collects the targets and processes them with bounded parallelism (MAX_CONCURRENT_OCR)."""
        connector = input.connector or DEFAULT_FILE_CONNECTOR
        if connector not in file_connectors:
            return {
                "target": input.target_path,
                "kind": "error",
                "applied": False,
                "actions": [],
                "error": (
                    f"unknown connector {input.connector!r}; available "
                    f"(FILE_CONNECTOR_NAMES): {FILE_CONNECTOR_NAMES}"
                ),
            }
        listing = await list_targets(input.target_path, input.recursive, connector)
        result: dict[str, Any] = {
            "target": input.target_path,
            "kind": listing["kind"],
            "applied": input.apply_changes,
            "actions": [],
        }
        if listing.get("truncated"):
            result["note"] = (
                f"{listing['total']} PDFs found, limit is {MAX_FILES_PER_RUN} "
                "per run; the rest was not processed (please start more "
                "targeted or process locally)"
            )
        files = listing["files"]
        if not files:
            result["note"] = "no PDFs found"
            return result

        semaphore = asyncio.Semaphore(MAX_CONCURRENT_OCR)

        async def handle(file: dict[str, Any]) -> dict[str, Any]:
            async with semaphore:
                try:
                    report = await process_file(
                        file,
                        input.force,
                        input.keep_original,
                        input.apply_changes,
                        input.languages,
                        connector,
                        input.with_pages_text,
                    )
                    if report.get("action") in ("replaced", "planned") and report.get(
                        "has_text"
                    ):
                        # Roadmap F: flag weak OCR results as low_confidence.
                        report["ocr_quality"] = await assess_ocr_quality(
                            report.get("pages_text") or [report.get("text", "")]
                        )
                    return report
                except Exception as exc:
                    return {
                        "path": file["path"],
                        "action": "error",
                        "error": str(exc) or exc.__class__.__name__,
                    }

        result["actions"] = list(await asyncio.gather(*(handle(f) for f in files)))
        return result
