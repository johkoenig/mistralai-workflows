# Roadmap

Planned improvements for the pdf-scanner-processor pipeline and its step workflows.
Status: draft, 2026-11-27. Items move to CHANGELOG.md when released.

## v0.3.0 - Speed (points A + B)

Goal: cut wall-clock time per run by removing redundant I/O and sequential waits.

- **A. Pass text metadata through the pipeline** instead of re-downloading:
  - pdf-ocr step returns per file: page count, has_text flag, text snippet
  - remove the analyze-scan-file download for OCR'd files
  - run split planning inline via the existing plan_split activity; spawn the
    pdf-split child workflow only when a split is actually confirmed
- **B. Parallelize sequential loops** in the orchestrator:
  - name_document LLM calls (currently a plain for-loop)
  - backup_scan_file park operations (use the existing semaphore pattern)

## v0.3.1 - Configuration (points C + D + E)

- **C. Pass naming rules to child workflows**: optional rules field in child
  inputs; children keep standalone loading as fallback
- **D. Expose OCR languages as orchestrator input** (default stays deu+eng;
  scanner inbox runs with deu)
- **E. MAX_CONCURRENT_OCR as environment variable** (current hardcoded 2)

## v0.4.0 - Quality (points F + G)

- **F. OCR quality heuristic** after OCR: alphanumeric ratio, word length
  distribution -> low_confidence flag in the run report (no vision call yet)
- **G. Vision fallback as a standalone step workflow `pdf-vision`**:
  - new activities assess_ocr_quality and vision_ocr_pass (pdf_ops.py)
  - renders flagged pages (pypdfium2) and reads them with a vision model
  - orchestrator step 2.5 routes low-confidence files, capped by
    max_vision_pages
  - vision text feeds duplex detection, split planning and naming only; it
    never replaces the PDF text layer
  - result stored as sidecar <name>.txt plus a processing flag in PDF
    metadata (model, confidence, date)
  - standalone: target_path (single file or folder), pages, force,
    apply_changes (dry-run), model, languages
  - env knobs: model name, vision threshold, page cap

## Non-goals

- No monolith refactor: keep child workflow architecture, deterministic IDs,
  replayability and STEPS-map testing
- No vision-based PDF text layer: chat vision returns text without
  coordinates or orientation; the searchable text layer stays Tesseract's
    job
- No post-vision rotation: pages are already rotated/deskewed in the OCR
  pass (ocrmypdf --rotate-pages --deskew); vision models read rotated images
  natively
