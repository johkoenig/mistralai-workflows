> Language: English · [Deutsche Version](WORKFLOWS.de.md)

# Workflow Reference

Complete reference of the five workflows of the `mistralai-workflows` deployment: purpose, start input, result shape, and examples. The deployment is a two-level system: `pdf-scanner-processor` (orchestrator) drives the four single-purpose step workflows `pdf-ocr`, `pdf-alternating-merge`, `pdf-split` and `pdf-vision` as child workflows — each step can also be started on its own. LLM-generated names, titles and follow-up questions follow the language of the document (the naming prompts instruct the model accordingly). Conventions and architecture are in [ARCHITECTURE.md](ARCHITECTURE.md), usage and deployment operations in [README.md](README.md) and [OPERATIONS.md](OPERATIONS.md). Every start input also accepts an optional `connector` field: the name of a file connector from `FILE_CONNECTOR_NAMES` (empty = default), so one call can target any of the configured connectors.

All workflows:

- are started manually (Studio or chat, or MCP `execute_workflow`); the steps are additionally called by `pdf-scanner-processor` as child workflows;
- use `apply_changes` (default `true`); `false` is the dry run: everything is read, planned and checked, but nothing is written (not even status feedback into control files);
- let the LLM choose file names — the project itself defines no name schema, it only sanitizes technically (ASCII, umlauts transliterated, max. 80 characters);
- read the nearest naming rules from `AGENTS.md` (the folder itself or a parent folder; max. 4000 characters; if none exists: the LLM picks a suitable name itself), whenever the AI names something; those rules take precedence over the base prompt and are not part of the project;
- never overwrite files (collisions get `_2`, `_3`, ...);
- secure originals under `<folder>/.originals/<workflow>/` before anything is modified (`pdf-alternating-merge` creates one subfolder per merge target; all other workflows park the file directly); backups are on by default in every workflow, and `.originals` is never scanned or reprocessed.

## Result and action glossary

Every run result contains `applied` at the top (true = written, false = planned/read only). Collecting workflows report an `actions` list, single-file workflows a single `action` field. The orchestrator's `actions` entries additionally carry a `step` field (`backup`, `ocr`, `vision`, `front-back`, `merge`, `name`, `split`). Unified action vocabulary:

| Action | Meaning | Workflow |
|---|---|---|
| `planned` | dry run: this would happen | all |
| `renamed` | file renamed | orchestrator (step `name`) |
| `left_unchanged` | uncertain (confidence < 0.7 or no title), nothing changed | orchestrator (step `name`) |
| `pair` / `no_pair` | front/back decision of the LLM check | orchestrator (step `front-back`) |
| `merged` | duplex pair merged | merge step (also orchestrator step `merge`) |
| `deferred` | dry run: merged file is only planned, split would run on it after a real merge | orchestrator (step `split`) |
| `replaced` | PDF was replaced (via OCR) | ocr |
| `skipped` | file skipped (text layer present, `force` not set) | ocr |
| `vision_pass` | vision OCR fallback ran (sidecar txt + PDF metadata flag, text layer untouched) | vision (also orchestrator step `vision`) |
| `split` | PDF was split | split |
| `no_split` | everything is one coherent document, no split | split |
| `clarification` | uncertain, follow-up question with `question` and `proposal`, nothing written | split |
| `error` | failure with `error` message | all |

## pdf-scanner-processor

Orchestrator over a scanner inbox folder (top folder level only): OCR, front/back detection + alternating merge, LLM naming, and a confidence-checked split per file — end to end, built from the step workflows `pdf-ocr`, `pdf-alternating-merge`, `pdf-split` and `pdf-vision` (called as child workflows with deterministic execution IDs `{run}-{step}-{item}`).

**Input**

```json
{"folder_path": "/<scanner inbox>", "apply_changes": true, "model": "mistral-small-latest", "connector": "", "merge_front_back": true, "split_parts": true, "vision_fallback": true, "vision_model": "", "ocr_languages": "deu+eng"}
```
(The workspace secret `SCAN_INBOX_FOLDER` (environment variable in the deployment) applies when `folder_path` is omitted; `model` selects the LLM for the checks and naming; `merge_front_back: false` skips the pair detection/merge, `split_parts: false` skips the split step `vision_fallback: false` skips the vision fallback (step 2.5), `vision_model` overrides the `PDF_VISION_MODEL` env knob. `ocr_languages` sets the OCR languages for the pdf-ocr child (default `deu+eng`; scanner-inbox runs can use `deu` to roughly halve OCR time) — naming then runs on every unit directly.)

**Flow**: find `Scan.pdf` / `ScanNNNN.pdf` → park a copy of each raw scan under `.originals/pdf-scanner-processor/` → child `pdf-ocr` on the folder (`with_pages_text=true`; its own backups under `.originals/pdf-ocr/`; the result carries per-file page count, `has_text`, text snippet, per-page texts and the `ocr_quality` flag (`low_confidence` on weak OCR), so nothing is downloaded again) → vision fallback for `low_confidence` OCR results via child `pdf-vision` (step 2.5; vision text feeds detection/split/naming only, never the PDF text layer) → detect front/back candidates (consecutive scan order, same page count, size factor 0.5-2.0, both with text) and confirm each candidate with the LLM activity `front-back-check` (accept at confidence >= 0.7; the check is name-independent, so front and back may look completely different) → child `pdf-alternating-merge` per confirmed pair with the intermediate title `ScanMerged<N>.pdf` (N = larger scan number of the pair; sources archived under `.originals/pdf-alternating-merge/<target>/`) → split planning INLINE per unit via the `plan-split` activity (BEFORE naming; content-based, parts are named from the plan) — the `pdf-split` child is spawned only when a split is actually confirmed, then with fixed `split_at` + `titles` → name every unit that was NOT split (`name-document` with the nearest AGENTS.md rules — inbox folder or a parent folder; merged pairs via the combined text; split parts are reported as `named_by_split`) → rename only when confident (>= 0.7), otherwise `left_unchanged`.

**Result**: `{folder, applied, actions[]}`; each action carries `step` (`backup` / `ocr` / `front-back` / `merge` / `name` / `split`) plus the step-specific fields (`files`, `action`, `to`, `backup`, `confidence`, `result`, ...).

## pdf-alternating-merge

Alternating merge of duplex pairs, either directly via `pairs` in the start input or via a JSON control file (format: see ARCHITECTURE.md). Per entry: check the page count, interleave front/back pages alternately (back pages in reverse order), name, and upload next to the sources; the source files are backed up under `.originals/pdf-alternating-merge/<target name>/` (as `FrontPages.pdf` / `BackPages_reversed.pdf`). With a control file, the workflow writes the status back per entry (`done`/`error`); repeated runs process only entries without status. The orchestrator calls this step per detected pair with an explicit intermediate `title` (`ScanMerged<N>.pdf`).

**Input (variant A, direct)**

```json
{"pairs": [{"front": "/folder/Doc Front.pdf", "back": "/folder/Doc Back.pdf", "title": "2026-01-15 Invoice A"}], "apply_changes": true, "model": "mistral-small-latest", "connector": "", "naming_rules": ""}
```

**Input (variant B, control file)**

```json
{"control_file": "/folder/steuerdatei.json", "apply_changes": true, "model": "mistral-small-latest"}
```
(`steuerdatei.json` is a German word for "control file"; the name is a convention, any filename works.)

An explicit `title` wins; otherwise the AI names (`name-document` with the nearest AGENTS.md rules — the front page's folder or a parent folder). Uncertain naming marks the entry with an error.

**Result**: `{source, control_file, applied, processed, actions[]}` with `action` per entry from `planned` / `merged` / `error`.

## pdf-ocr

Generic OCR processor for a single PDF or a folder (`recursive: true` also subfolders, but never `.originals`). PDFs without a text layer are processed with OCRmyPDF (Tesseract `deu+eng`, deskew, automatic page rotation) and replaced; PDFs with a text layer are skipped (`force: true` forces reprocessing). `keep_original` (default `true`) moves the original to `.originals/pdf-ocr/<name>` before replacing it; if a backup already exists, the file is left with an error message, and a backup without a target file resumes an interrupted run. At most 200 files per run.
- `with_pages_text` (default `false`): every result action also carries `has_text`, a `text` snippet (max 6000 chars) and `pages_text` (per page, capped) - pipeline callers (pdf-scanner-processor) then need no second download. Off by default to keep folder-run results small. Files that were OCRed (`replaced` / `planned` with text) additionally carry `ocr_quality` (`confidence`, `low_confidence`, `metrics`) from the `assess-ocr-quality` heuristic - `low_confidence` flags weak OCR results (threshold `OCR_QUALITY_MIN_CONFIDENCE`, default `0.6`).

**Input**

```json
{"target_path": "/folder", "recursive": false, "force": false, "keep_original": true, "apply_changes": true, "languages": "deu+eng", "connector": "", "with_pages_text": false}
```

**Result**: `{target, kind, applied, actions[]}` with `action` per file from `planned` / `replaced` / `skipped` / `error`; `note` reports limits and empty results.

## pdf-split

Splits a PDF into parts and names them; the parts stay in the folder of the original, the original is secured under `.originals/pdf-split/<name>`.

**Input**

```json
{"target_path": "/folder/document.pdf", "split_at": [2, 4], "plan_splits": true, "titles": null, "name_parts": true, "model": "mistral-small-latest", "apply_changes": true, "connector": "", "naming_rules": ""}
```

- `split_at`: page numbers (1-based) AFTER which the PDF is cut — the given page stays in the previous part; always wins over planning. Example: `[2, 4]` on 8 pages yields parts 1-2, 3-4, 5-8. The same semantics are stated in the input schema (field description of `split_at`) and in the workflow description. A dry run (`apply_changes: false`) returns `action: "planned"` with the exact `pages` range of every part before anything is written.
- `plan_splits` (default `true`): AI planning of the split points; `false` without `split_at` is an error.
- `titles`: explicit part names (count must match); disable AI naming.
- `name_parts` (default `true`): AI naming; `false` => `<stem> Teil-N.pdf`.
- `naming_rules`: naming rules passed by a caller (e.g. the pdf-scanner-processor orchestrator); when empty, the workflow loads the nearest AGENTS.md itself.

AI planning heuristics (in the split prompt): a NEW document is assumed when the date changes, when a (running) page number changes (e.g. a new 'Seite 1 von 2' / 'Page 1 of 2' run starts), or when a new company name / letterhead appears at the top of a page — a changed company or contract partner almost always means a new document (e.g. invoice). Exception: a company name that appears only because a third-party service was billed or passed through on someone else's behalf ('Fremdleistung im Namen von XY' / 'on behalf of XY') is NOT a new document. The model also verifies every planned boundary before finalizing and removes (merges back) boundaries that would cut through one coherent document.

AI-named parts are called `<title>.pdf` (no stem prefix), named by the nearest AGENTS.md rules.

**Result**: `{target, applied, action, pages, parts[], original?}`; `action` from `planned` / `split` / `no_split` / `clarification` / `error`, `parts` with `pages` (e.g. `"1-2"`), `count`, `to`.

## pdf-vision

Vision OCR fallback for scanned PDFs (roadmap G): renders pages (pypdfium2, max edge 1540 px) and reads them with a vision-capable chat model (base64 PNG parts, max 8 images per request). The vision text feeds duplex detection, split planning and naming in the orchestrator - it never replaces the PDF's searchable text layer.

**Input**

```json
{"target_path": "/folder", "recursive": false, "pages": null, "force": false, "apply_changes": true, "model": "", "languages": "deu+eng", "connector": ""}
```

- `pages`: explicit 1-based page numbers to read; when empty, `force=true` reads all pages, otherwise only files flagged `low_confidence` by the OCR quality heuristic (assess-ocr-quality). Per file the page count is capped by `MAX_VISION_PAGES` (default 8).
- `model`: empty = `PDF_VISION_MODEL` env knob (default `mistral-small-latest`, vision-capable).
- `apply_changes=false` (dry run) plans everything and writes nothing.

**Result storage** (`apply_changes=true`): sidecar `<name>.txt` next to the PDF (never overwrites - collision gets a `_2` suffix) plus a processing flag in the PDF metadata (`/VisionOcrPass`: model, confidence, date, pages); the text layer is untouched.

**Result**: `{target, kind, applied, actions[]}`; `action` per file from `vision_pass` / `planned` / `skipped` / `error`, with `pages`, `model`, `confidence`, `low_confidence`, `text`, `sidecar`.
