"""Vision OCR fallback step (pdf-vision) - roadmap point G.

Renders pages of a scanned PDF and reads them with a vision-capable chat
model (base64 PNG parts, see vision-ocr-pass in pdf_ops.py). The vision text
feeds duplex detection, split planning and naming in the orchestrator - it
never replaces the PDF's searchable text layer (that stays Tesseract's job;
chat vision returns no coordinates, so no vision-based text layer, non-goal).

Standalone use: target_path (single PDF or folder), optional explicit pages,
force, apply_changes (dry run plans everything and writes nothing), model
(empty = PDF_VISION_MODEL env, default mistral-small-latest).

Result storage (apply_changes=true): a sidecar <name>.txt next to the PDF
plus a processing flag in the PDF metadata (model, confidence, date, pages).
Never overwrites: sidecar name collisions get _2, _3, ... suffixes. The
original PDF is only rewritten to add the metadata flag.

Without explicit pages, only files whose OCR quality heuristic
(assess-ocr-quality) flags low_confidence - or all files with force=true -
are vision-read; page count per file is capped by MAX_VISION_PAGES.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
from datetime import datetime, timedelta
from typing import Any

import pydantic
from mistralai import workflows
from mistralai.workflows.plugins.mistralai.connectors import (
    uses_connectors,
)

from worker.ocr import OCR_LANGUAGES, read_text_info, run_with_heartbeat
from worker.pdf_ops import (
    MAX_CONCURRENT_OCR,
    file_client,
    file_connectors,
    http_get,
    http_put,
    list_names,
    nc_call,
    ocr_quality_metrics,
    pick_url,
    unique_filename,
    vision_ocr_pass,
)
from worker.workflows.steps.pdf_ocr import list_targets

HEARTBEAT_TIMEOUT = timedelta(seconds=120)

logger = logging.getLogger("worker.pdf_vision")


class PdfVisionInput(pydantic.BaseModel):
    """Start input: target, page selection, force, dry run, model, connector."""

    target_path: str
    recursive: bool = False
    pages: list[int] | None = None
    force: bool = False
    apply_changes: bool = True
    model: str = ""  # empty -> PDF_VISION_MODEL env knob
    languages: str = OCR_LANGUAGES  # interface compatibility; vision reads the document language itself
    connector: str = ""


@workflows.activity(
    name="vision-load-file",
    start_to_close_timeout=timedelta(minutes=10),
    heartbeat_timeout=HEARTBEAT_TIMEOUT,
    retry_policy_max_attempts=2,
)
async def vision_load_file(
    entry: dict[str, Any],
    pages: list[int] | None,
    force: bool,
    connector: str = "",
) -> dict[str, Any]:
    """Downloads one PDF and decides which pages to vision-read.

    Explicit pages win; otherwise force=true reads all pages, and without
    force only files flagged low_confidence by the OCR quality heuristic.
    """
    nc = file_client(connector)
    workdir = tempfile.mkdtemp(prefix="pdfvision-")
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
        quality = ocr_quality_metrics(info["pages"])
        if pages:
            render_pages = sorted({p for p in pages if 1 <= p <= info["page_count"]})
            action = "vision_pass"
        elif force or quality["low_confidence"] or not info["has_text"]:
            render_pages = list(range(1, info["page_count"] + 1))
            action = "vision_pass"
        else:
            shutil.rmtree(workdir, ignore_errors=True)
            return {
                "path": entry["path"],
                "action": "skipped",
                "reason": "OCR quality ok",
                "ocr_quality": quality,
            }
        return {
            "path": entry["path"],
            "name": entry["name"],
            "action": action,
            "local_path": local,
            "workdir": workdir,
            "page_count": info["page_count"],
            "has_text": info["has_text"],
            "render_pages": render_pages,
            "ocr_quality": quality,
        }
    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        raise


@workflows.activity(
    name="vision-store-result",
    start_to_close_timeout=timedelta(minutes=10),
    heartbeat_timeout=HEARTBEAT_TIMEOUT,
    retry_policy_max_attempts=2,
)
async def vision_store_result(
    entry: dict[str, Any],
    workdir: str,
    local_path: str,
    vision: dict[str, Any],
    apply_changes: bool,
    connector: str = "",
) -> dict[str, Any]:
    """Stores a vision result: sidecar plus PDF metadata flag.

    With apply_changes=true: sidecar <name>.txt next to the PDF and a
    processing flag in the PDF metadata; with apply_changes=false (dry run)
    only reports what would be written. Cleans up the workdir.
    """
    nc = file_client(connector)
    try:
        folder = os.path.dirname(entry["path"])
        stem = entry["name"].rsplit(".", 1)[0]
        sidecar = f"{folder}/{stem}.txt"
        report: dict[str, Any] = {
            "path": entry["path"],
            "action": "planned" if not apply_changes else "vision_pass",
            "pages": vision.get("pages", []),
            "model": vision.get("model"),
            "confidence": vision.get("confidence"),
            "low_confidence": vision.get("low_confidence"),
            "text": vision.get("text", ""),
            "sidecar": sidecar,
        }
        if not apply_changes:
            return report
        taken = await list_names(nc, folder)
        sidecar = f"{folder}/{unique_filename(os.path.basename(sidecar), taken)}"
        await nc_call(
            nc,
            "files_create",
            {
                "path": sidecar,
                "content": vision.get("text", ""),
                "confirm": True,
                "confirm_shared": True,
            },
        )
        report["sidecar"] = sidecar
        # Processing flag in the PDF metadata (model, confidence, date, pages);
        # the searchable text layer is untouched.
        flag = {
            "model": vision.get("model"),
            "confidence": vision.get("confidence"),
            "date": datetime.now().astimezone().isoformat(timespec="seconds"),
            "pages": vision.get("pages", []),
        }
        out_path = os.path.join(workdir, "flagged.pdf")
        await asyncio.to_thread(_write_metadata_flag, local_path, out_path, flag)
        checkout = await nc_call(
            nc,
            "files_checkout",
            {"path": entry["path"], "confirm": True, "confirm_shared": True},
        )
        await run_with_heartbeat(
            http_put,
            pick_url(checkout, "upload_url", "uploadUrl"),
            out_path,
            detail={"phase": "upload", "file": entry["name"]},
        )
        report["metadata_flag"] = flag
        return report
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _write_metadata_flag(pdf_path: str, out_path: str, flag: dict[str, Any]) -> None:
    import pikepdf

    with pikepdf.open(pdf_path) as pdf:
        pdf.docinfo["/VisionOcrPass"] = json.dumps(flag, ensure_ascii=False)
        pdf.save(out_path)


@workflows.workflow.define(
    name="pdf-vision",
    workflow_display_name="Vision OCR fallback",
    workflow_description=(
        "Vision fallback for scanned PDFs: renders pages and reads them with a "
        "vision-capable model; stores the text as a sidecar <name>.txt plus a "
        "processing flag in the PDF metadata (model, confidence, date). The "
        "searchable text layer is never replaced. Dry run via apply_changes=false."
    ),
)
@uses_connectors(*file_connectors.values())
class PdfVisionWorkflow:
    """Vision OCR fallback: renders flagged pages and reads them with a vision model (flow: module docstring)."""

    @workflows.workflow.entrypoint
    async def run(self, input: PdfVisionInput) -> dict[str, Any]:
        """Runs the vision pass over the target file/folder."""
        connector = input.connector
        listing = await list_targets(input.target_path, input.recursive, connector)
        result: dict[str, Any] = {
            "target": input.target_path,
            "kind": listing["kind"],
            "applied": input.apply_changes,
            "actions": [],
        }
        if listing.get("truncated"):
            result["note"] = "target list truncated"
        files = listing["files"]
        if not files:
            result["note"] = "no PDFs found"
            return result

        semaphore = asyncio.Semaphore(MAX_CONCURRENT_OCR)

        async def handle(file: dict[str, Any]) -> dict[str, Any]:
            async with semaphore:
                loaded = await vision_load_file(
                    file, input.pages, input.force, connector
                )
                if loaded.get("action") == "skipped":
                    return loaded
                try:
                    vision = await vision_ocr_pass(
                        loaded["local_path"], loaded["render_pages"], input.model
                    )
                    if vision.get("error"):
                        return {
                            "path": loaded["path"],
                            "action": "error",
                            "error": vision["error"],
                        }
                    return await vision_store_result(
                        loaded,
                        loaded["workdir"],
                        loaded["local_path"],
                        vision,
                        input.apply_changes,
                        connector,
                    )
                except Exception as exc:
                    shutil.rmtree(loaded.get("workdir", ""), ignore_errors=True)
                    return {
                        "path": loaded["path"],
                        "action": "error",
                        "error": str(exc) or exc.__class__.__name__,
                    }

        result["actions"] = list(await asyncio.gather(*(handle(f) for f in files)))
        return result
