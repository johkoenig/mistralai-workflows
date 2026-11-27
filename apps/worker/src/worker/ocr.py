"""Reusable PDF OCR processing (OCRmyPDF/Tesseract).

Encapsulates the OCR pipeline as a single ocrmypdf call with individually
switchable preprocessing steps: automatic page rotation (--rotate-pages),
deskew (--deskew) and text recognition (-l deu+eng). Rotate and deskew run
as preprocessing within the same rasterization pass; separate calls would
cause quality loss through recompression and multiplied runtime.

Memory lock: ocrmypdf runs with -j 1 per file; parallelism comes from the
file level (MAX_CONCURRENT_OCR). Several concurrent runs with -j 4 have
repeatedly OOM-killed the managed instance.

Robustness: during the OCR run and during blocking downloads/uploads,
activity heartbeats are sent regularly, so a worker restart (rollout, crash)
is detected within heartbeat_timeout and the activity is retried, instead of
waiting silently until start_to_close_timeout. Every OCR run is logged with
duration and result.

This module has no Nextcloud and no workflow dependency. Callers are
pdf-scanner-processor (scan analysis) and pdf-ocr (generic single-PDF/folder
processor).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable
from typing import Any, cast

from mistralai.workflows import activity_heartbeat

from worker.pdf_ops import extract_texts

OCR_LANGUAGES = "deu+eng"
OCR_ROTATE_THRESHOLD = "5"
OCR_JOBS = "1"  # pages serially per file: parallelism comes from the file level; protects small instances from OOM
OCR_SUBPROCESS_TIMEOUT_S = 1800
OCR_ERROR_CHARS = 500
HEARTBEAT_INTERVAL_S = 30

logger = logging.getLogger("worker.ocr")


def read_text_info(pdf_path: str) -> dict[str, Any]:
    """Page count, page texts and whether a text layer exists at all."""
    pages, page_count = extract_texts(pdf_path)
    return {
        "pages": pages,
        "page_count": page_count,
        "has_text": any(page.strip() for page in pages),
    }


def render_pdf_pages(
    pdf_path: str, pages: list[int], max_px: int = 1540
) -> dict[int, bytes]:
    """Renders the given 1-based pages of a PDF to PNG bytes (pypdfium2).

    The longest edge is capped at max_px (vision models downscale to
    ~1540x1540 anyway), which keeps the base64 payload small. Used by the
    vision OCR fallback (pdf-vision / vision-ocr-pass).
    """
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf_path)
    try:
        out: dict[int, bytes] = {}
        for page_number in pages:
            index = page_number - 1
            if index < 0 or index >= len(doc):
                continue
            page = doc[index]
            width, height = page.get_size()
            scale = min(1.0, max_px / max(width, height)) if max(width, height) else 1.0
            bitmap = page.render(scale=scale)
            out[page_number] = _pil_to_png(bitmap)
        return out
    finally:
        doc.close()


def _pil_to_png(bitmap: Any) -> bytes:
    import io

    pil_image = bitmap.to_pil().convert("RGB")
    buffer = io.BytesIO()
    pil_image.save(buffer, format="PNG")
    return buffer.getvalue()


def build_ocr_command(
    input_path: str,
    output_path: str,
    languages: str = OCR_LANGUAGES,
    rotate: bool = True,
    deskew: bool = True,
    jobs: str = OCR_JOBS,
) -> list[str]:
    """Build the ocrmypdf invocation; rotate and deskew are options of the same pass."""
    command = ["ocrmypdf", "-l", languages, "--output-type", "pdf", "-j", jobs, "-q"]
    if rotate:
        command += ["--rotate-pages", "--rotate-pages-threshold", OCR_ROTATE_THRESHOLD]
    if deskew:
        command += ["--deskew"]
    command += [input_path, output_path]
    return command


async def run_with_heartbeat(
    blocking: Callable[..., Any],
    *args: Any,
    detail: dict[str, Any] | None = None,
    **kwargs: Any,
) -> Any:
    """Run a blocking call in a thread while heartbeating.

    If the worker process dies in between, the heartbeat stops and Temporal
    detects the task loss after heartbeat_timeout instead of only after
    start_to_close_timeout.
    """
    payload = detail or {}
    stop = asyncio.Event()

    async def beat() -> None:
        while not stop.is_set():
            activity_heartbeat(payload)
            try:
                await asyncio.wait_for(stop.wait(), HEARTBEAT_INTERVAL_S)
            except TimeoutError:
                pass

    beater = asyncio.create_task(beat())
    try:
        return await asyncio.to_thread(blocking, *args, **kwargs)
    finally:
        stop.set()
        beater.cancel()
        try:
            await beater
        except asyncio.CancelledError:
            pass


async def run_ocrmypdf(
    input_path: str,
    output_path: str,
    languages: str = OCR_LANGUAGES,
    rotate: bool = True,
    deskew: bool = True,
    jobs: str = OCR_JOBS,
    timeout_s: int = OCR_SUBPROCESS_TIMEOUT_S,
) -> dict[str, Any]:
    """OCRmyPDF run with heartbeats; returns {"ok": bool, "error": ...}."""
    command = build_ocr_command(
        input_path, output_path, languages, rotate, deskew, jobs
    )
    base = os.path.basename(input_path)
    logger.info(
        "ocr start: %s (languages=%s rotate=%s deskew=%s)",
        base,
        languages,
        rotate,
        deskew,
    )
    started = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        logger.error("ocr failed: ocrmypdf not installed (%s)", base)
        return {"ok": False, "error": "ocrmypdf is not installed in the worker image"}
    stderr_task = asyncio.create_task(cast("asyncio.StreamReader", proc.stderr).read())
    while True:
        try:
            await asyncio.wait_for(proc.wait(), HEARTBEAT_INTERVAL_S)
            break
        except TimeoutError:
            elapsed = time.monotonic() - started
            if elapsed >= timeout_s:
                proc.kill()
                await proc.wait()
                logger.error("ocr aborted: timeout after %ss (%s)", int(elapsed), base)
                return {"ok": False, "error": f"ocrmypdf timeout after {int(elapsed)}s"}
            activity_heartbeat(
                {"phase": "ocr", "file": base, "elapsed_s": int(elapsed)}
            )
    detail = " ".join((await stderr_task or b"").decode("utf-8", "replace").split())
    duration = time.monotonic() - started
    if proc.returncode != 0 or not os.path.exists(output_path):
        logger.error(
            "ocr failed: %s after %.1fs (rc=%s): %s",
            base,
            duration,
            proc.returncode,
            detail[:OCR_ERROR_CHARS],
        )
        return {
            "ok": False,
            "error": f"ocrmypdf exit code {proc.returncode}: {detail[:OCR_ERROR_CHARS]}",
        }
    logger.info(
        "ocr done: %s (%.1fs, %d bytes)", base, duration, os.path.getsize(output_path)
    )
    return {"ok": True, "error": None}
