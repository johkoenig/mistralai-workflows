"""Manual duplex merging: directly via pairs input or via a JSON control file.

Variant A (direct, no file): the pairs are given in the start input, e.g.:

    {
      "pairs": [
        {"front": "/<scanner inbox>/2026-01-15 Example A Front.pdf",
         "back":  "/<scanner inbox>/2026-01-15 Example A Back.pdf",
         "title": "2026-01-15 Invoice A"}
      ],
      "apply_changes": true
    }

Results and errors appear in the run result; no file is read or written
back. "title" is optional, otherwise LLM naming (in the document's language).

Variant B (control file): the input is the path of a control file in
Nextcloud, e.g. /<scanner inbox>/control-file.json:

    {
      "pairs": [
        {
          "front": "/<scanner inbox>/2026-01-15 Example A Front.pdf",
          "back":  "/<scanner inbox>/2026-01-15 Example A Back.pdf",
          "title": "2026-01-15 Invoice A"          <- optional, otherwise LLM naming
        }
      ]
    }

For each not-yet-processed entry (without "status"): load both PDFs,
check the page count, interleave front pages in scan order with back pages in
reverse order, name the target file (explicit title or LLM from the combined
OCR text) and upload it next to the sources, back the source files up under
.originals/pdf-merge-duplex/<target name>/, then write the status back into the control file ("done" with
result or "error" with message; variant B only). Repeated runs (variant B)
process only entries without status. The workflow is started manually only
(Studio or chat), never triggered automatically.

Long activities (reading, merging, uploading) send activity heartbeats
and are retried automatically on worker restart.

connector: name of the file connector from FILE_CONNECTOR_NAMES (empty = default);
with several active Nextcloud connectors the caller selects the target connector.
"""

from __future__ import annotations

import asyncio
import json
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
    BACK_FILE_NAME,
    DEFAULT_FILE_CONNECTOR,
    FILE_CONNECTOR_NAMES,
    FRONT_FILE_NAME,
    build_filename,
    ensure_folder,
    extract_texts,
    file_client,
    file_connectors,
    http_get,
    http_put,
    load_naming_rules,
    merge_pdf_files,
    merge_texts,
    name_document,
    nc_call,
    originals_dir,
    pick_url,
    unique_filename,
)

logger = logging.getLogger("worker.pdf_merge_duplex")


def read_control_file(path: str) -> Any:
    """Reads a control file synchronously; in activity context via to_thread."""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def write_control_file(path: str, data: dict[str, Any]) -> None:
    """Writes a control file synchronously; in activity context via to_thread."""
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)


MIN_TITLE_CONFIDENCE = 0.5
HEARTBEAT_TIMEOUT = timedelta(seconds=120)


class PdfMergeDuplexInput(pydantic.BaseModel):
    """Start input: either pairs inline (A) or the path of a control file (B)."""

    # Exactly one of the two variants: pairs (inline) or control_file.
    control_file: str = ""  # variant B: path of a control file in Nextcloud
    pairs: list[dict[str, Any]] | None = (
        None  # variant A: pairs directly in the start input
    )
    apply_changes: bool = True
    model: str = "mistral-small-latest"
    connector: str = ""


def entry_error(entry: Any) -> str | None:
    """Validates a control-file entry; None means ok, otherwise an error message."""
    if not isinstance(entry, dict):
        return "entry is not an object"
    front = entry.get("front")
    back = entry.get("back")
    if not isinstance(front, str) or not front.strip():
        return 'path "front" missing'
    if not isinstance(back, str) or not back.strip():
        return 'path "back" missing'
    if not front.lower().endswith(".pdf") or not back.lower().endswith(".pdf"):
        return "front and back must be PDFs"
    return None


def pending_indices(pairs: list[Any]) -> list[int]:
    """Indices of entries without status (done/error count as processed)."""
    return [
        i
        for i, entry in enumerate(pairs)
        if not (isinstance(entry, dict) and entry.get("status"))
    ]


@workflows.activity(
    name="merge-load-control-file",
    start_to_close_timeout=timedelta(minutes=5),
    retry_policy_max_attempts=2,
)
async def load_control_file(control_file: str, connector: str = "") -> dict[str, Any]:
    """Loads the control file from Nextcloud and returns it as JSON."""
    nc = file_client(connector)
    workdir = tempfile.mkdtemp(prefix="steuer-")
    try:
        checkout = await nc_call(
            nc,
            "files_checkout",
            {"path": control_file, "confirm": True, "confirm_shared": True},
        )
        local = os.path.join(workdir, "steuerdatei.json")
        await run_with_heartbeat(
            http_get,
            pick_url(checkout, "download_url", "downloadUrl"),
            local,
            detail={"phase": "download", "datei": os.path.basename(control_file)},
        )
        return await asyncio.to_thread(read_control_file, local)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@workflows.activity(
    name="merge-save-control-file",
    start_to_close_timeout=timedelta(minutes=5),
    retry_policy_max_attempts=1,
)
async def save_control_file(
    control_file: str, data: dict[str, Any], connector: str = ""
) -> dict[str, Any]:
    """Writes the updated control file back to Nextcloud."""
    nc = file_client(connector)
    workdir = tempfile.mkdtemp(prefix="steuer-out-")
    try:
        local = os.path.join(workdir, "steuerdatei.json")
        await asyncio.to_thread(write_control_file, local, data)
        replacement = await nc_call(
            nc,
            "files_checkout",
            {"path": control_file, "confirm": True, "confirm_shared": True},
        )
        await run_with_heartbeat(
            http_put,
            pick_url(replacement, "upload_url", "uploadUrl"),
            local,
            detail={"phase": "upload", "datei": os.path.basename(control_file)},
        )
        return {"control_file": control_file}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@workflows.activity(
    name="merge-list-folder-names",
    start_to_close_timeout=timedelta(minutes=5),
    retry_policy_max_attempts=2,
)
async def list_folder_names(folder_path: str, connector: str = "") -> list[str]:
    """File names (without folder) of the first level of folder_path."""
    nc = file_client(connector)
    result = await nc_call(nc, "files_list", {"path": folder_path})
    entries = (
        result
        if isinstance(result, list)
        else next((v for v in result.values() if isinstance(v, list)), [])
    )
    return [
        cast("str", e.get("name"))
        for e in entries
        if isinstance(e, dict) and not e.get("isDir") and e.get("name")
    ]


@workflows.activity(
    name="merge-read-pair-texts",
    start_to_close_timeout=timedelta(minutes=15),
    heartbeat_timeout=HEARTBEAT_TIMEOUT,
    retry_policy_max_attempts=2,
)
async def read_pair_texts(
    front_path: str, back_path: str, connector: str = ""
) -> dict[str, Any]:
    """Loads both halves of a pair and returns combined text and page count."""
    nc = file_client(connector)
    workdir = tempfile.mkdtemp(prefix="pair-")
    try:
        texts = []
        pages = []
        for path in (front_path, back_path):
            checkout = await nc_call(
                nc,
                "files_checkout",
                {"path": path, "confirm": True, "confirm_shared": True},
            )
            local = os.path.join(workdir, os.path.basename(path))
            await run_with_heartbeat(
                http_get,
                pick_url(checkout, "download_url", "downloadUrl"),
                local,
                detail={"phase": "download", "file": os.path.basename(path)},
            )
            page_texts, page_count = extract_texts(local)
            texts.append("\n".join(page_texts))
            pages.append(page_count)
        if pages[0] != pages[1]:
            raise ValueError(
                "page count mismatch: front "
                + str(pages[0])
                + ", back "
                + str(pages[1])
            )
        combined = merge_texts(texts[0], texts[1])
        return {
            "combined_text": combined,
            "has_text": bool(combined.strip()),
            "pages": pages[0],
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@workflows.activity(
    name="merge-apply-pair",
    start_to_close_timeout=timedelta(minutes=45),
    heartbeat_timeout=HEARTBEAT_TIMEOUT,
    retry_policy_max_attempts=2,
)
async def apply_pair(
    front_path: str,
    back_path: str,
    target_path: str,
    archive_dir: str,
    connector: str = "",
) -> dict[str, Any]:
    """Merges a pair, uploads the target and archives the sources."""
    nc = file_client(connector)
    workdir = tempfile.mkdtemp(prefix="mergepair-")
    try:
        locals_: list[str] = []
        for path in (front_path, back_path):
            checkout = await nc_call(
                nc,
                "files_checkout",
                {"path": path, "confirm": True, "confirm_shared": True},
            )
            local = os.path.join(workdir, os.path.basename(path))
            await run_with_heartbeat(
                http_get,
                pick_url(checkout, "download_url", "downloadUrl"),
                local,
                detail={"phase": "download", "file": os.path.basename(path)},
            )
            locals_.append(local)

        merged = os.path.join(workdir, "merged.pdf")
        merged_pages = merge_pdf_files(locals_[0], locals_[1], merged)
        merged_size = os.path.getsize(merged)

        upload = await nc_call(
            nc,
            "files_upload",
            {
                "path": target_path,
                "size": merged_size,
                "confirm": True,
                "confirm_shared": True,
            },
        )
        await run_with_heartbeat(
            http_put,
            pick_url(upload, "upload_url", "uploadUrl"),
            merged,
            detail={"phase": "upload", "file": os.path.basename(target_path)},
        )

        # Create the backup folder (including .originals/pdf-merge-duplex)
        # idempotently; an already existing folder is fine.
        await ensure_folder(nc, archive_dir)
        await nc_call(
            nc,
            "files_move",
            {
                "from": front_path,
                "to": archive_dir + "/" + FRONT_FILE_NAME,
                "confirm": True,
                "confirm_shared": True,
            },
        )
        await nc_call(
            nc,
            "files_move",
            {
                "from": back_path,
                "to": archive_dir + "/" + BACK_FILE_NAME,
                "confirm": True,
                "confirm_shared": True,
            },
        )
        return {
            "target": target_path,
            "archive_dir": archive_dir,
            "pages": merged_pages,
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@workflows.workflow.define(
    name="pdf-merge-duplex",
    workflow_display_name="Merge PDF duplex manually",
    workflow_description=(
        "Interleaves front/back pairs alternately into one PDF - directly via "
        "pairs start input or via a JSON control file in Nextcloud. "
        "Manual start only."
    ),
    on_behalf_of=True,
    execution_timeout=timedelta(hours=1),
)
@uses_connectors(*file_connectors.values())
class PdfMergeDuplexWorkflow:
    """Merge duplex pairs: pairs inline or via control file (module docstring)."""

    @workflows.workflow.entrypoint
    async def run(self, input: PdfMergeDuplexInput) -> dict[str, Any]:
        """Processes all pairs without status and writes the control file back (variant B)."""
        if input.pairs is None and not input.control_file:
            return {
                "applied": input.apply_changes,
                "error": "provide either 'pairs' in the start input or 'control_file'",
            }
        connector = input.connector or DEFAULT_FILE_CONNECTOR
        if connector not in file_connectors:
            return {
                "applied": input.apply_changes,
                "error": (
                    f"unknown connector {input.connector!r}; available "
                    f"(FILE_CONNECTOR_NAMES): {FILE_CONNECTOR_NAMES}"
                ),
            }
        if input.pairs is not None:
            data = {"pairs": input.pairs}
        else:
            data = await load_control_file(input.control_file, connector)
        pairs = data.get("pairs")
        if not isinstance(pairs, list):
            return {
                "control_file": input.control_file,
                "applied": input.apply_changes,
                "error": 'control file without a "pairs" list',
            }

        results: list[dict[str, Any]] = []
        for index in pending_indices(pairs):
            entry = pairs[index]
            front = entry.get("front") if isinstance(entry, dict) else None
            back = entry.get("back") if isinstance(entry, dict) else None
            try:
                error = entry_error(entry)
                if error:
                    raise ValueError(error)
                front_str = cast("str", front)
                back_str = cast("str", back)
                folder = os.path.dirname(front_str)
                existing = set(await list_folder_names(folder, connector))
                infos = await read_pair_texts(front_str, back_str, connector)

                title = entry.get("title")
                naming: dict[str, Any]
                if isinstance(title, str) and title.strip():
                    naming = {"date": None, "title": title.strip(), "confidence": 1.0}
                elif infos["has_text"]:
                    rules = (await load_naming_rules(folder, connector))["rules"]
                    naming = await name_document(
                        infos["combined_text"], input.model, rules
                    )
                    if (
                        naming["confidence"] < MIN_TITLE_CONFIDENCE
                        or not naming["title"]
                    ):
                        raise ValueError(
                            "naming insecure; please provide 'title' (pairs entry or control file)"
                        )
                else:
                    raise ValueError(
                        "no text layer; please run pdf-scan-inbox-triage first "
                        "or provide 'title' (pairs entry or control file)"
                    )

                target_name = unique_filename(
                    build_filename(naming["date"], naming["title"]), existing
                )
                target_path = folder + "/" + target_name

                if not input.apply_changes:
                    results.append(
                        {
                            "index": index,
                            "front": front,
                            "back": back,
                            "action": "planned",
                            "to": target_path,
                        }
                    )
                    continue

                archive_dir = (
                    originals_dir(folder, "pdf-merge-duplex")
                    + "/"
                    + target_name[: -len(".pdf")]
                )
                merged = await apply_pair(
                    front_str, back_str, target_path, archive_dir, connector
                )
                entry["status"] = "done"
                entry["result"] = {
                    "to": merged["target"],
                    "archive": merged["archive_dir"],
                    "pages": merged["pages"],
                }
                results.append(
                    {
                        "index": index,
                        "front": front,
                        "back": back,
                        "action": "merged",
                        "to": merged["target"],
                        "archive": merged["archive_dir"],
                        "pages": merged["pages"],
                    }
                )
            except Exception as exc:
                message = str(exc) or exc.__class__.__name__
                if isinstance(entry, dict):
                    entry["status"] = "error"
                    entry["error"] = message
                results.append(
                    {
                        "index": index,
                        "front": front,
                        "back": back,
                        "action": "error",
                        "error": message,
                    }
                )

        if input.apply_changes and results and input.control_file:
            await save_control_file(input.control_file, data, connector)

        return {
            "source": input.control_file or "pairs",
            "control_file": input.control_file,
            "applied": input.apply_changes,
            "processed": len(results),
            "actions": results,
        }
