> Language: English · [Deutsche Version](WORKFLOWS.de.md)

# Workflow Reference

Complete reference of the four workflows of the `mistralai-workflows` deployment: purpose, start input, result shape, and examples. The deployment is a two-level system: `pdf-scanner-processor` (orchestrator) drives the three single-purpose step workflows `pdf-ocr`, `pdf-alternating-merge` and `pdf-split` as child workflows — each step can also be started on its own. LLM-generated names, titles and follow-up questions follow the language of the document (the naming prompts instruct the model accordingly). Conventions and architecture are in [ARCHITECTURE.md](ARCHITECTURE.md), usage and deployment operations in [README.md](README.md) and [OPERATIONS.md](OPERATIONS.md). Every start input also accepts an optional `connector` field: the name of a file connector from `FILE_CONNECTOR_NAMES` (empty = default), so one call can target any of the configured connectors.

All workflows:

- are started manually (Studio or chat, or MCP `execute_workflow`); the steps are additionally called by `pdf-scanner-processor` as child workflows;
- use `apply_changes` (default `true`); `false` is the dry run: everything is read, planned and checked, but nothing is written (not even status feedback into control files);
- let the LLM choose file names — the project itself defines no name schema, it only sanitizes technically (ASCII, umlauts transliterated, max. 80 characters);
- read the nearest naming rules from `AGENTS.md` (the folder itself or a parent folder; max. 4000 characters; if none exists: the LLM picks a suitable name itself), whenever the AI names something; those rules take precedence over the base prompt and are not part of the project;
- never overwrite files (collisions get `_2`, `_3`, ...);
- secure originals under `<folder>/.originals/<workflow>/` before anything is modified (per-workflow subfolder where useful: `pdf-split` and `pdf-alternating-merge` create one per file/target, `pdf-ocr` and `pdf-scanner-processor` park the file directly); backups are on by default in every workflow, and `.originals` is never scanned or reprocessed.

## Result and action glossary

Every run result contains `applied` at the top (true = written, false = planned/read only). Collecting workflows report an `actions` list, single-file workflows a single `action` field. The orchestrator's `actions` entries additionally carry a `step` field (`backup`, `ocr`, `front-back`, `merge`, `name`, `split`). Unified action vocabulary:

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
| `split` | PDF was split | split |
| `no_split` | everything is one coherent document, no split | split |
| `clarification` | uncertain, follow-up question with `question` and `proposal`, nothing written | split |
| `error` | failure with `error` message | all |

## pdf-scanner-processor

Orchestrator over a scanner inbox folder (top folder level only): OCR, front/back detection + alternating merge, LLM naming, and a confidence-checked split per file — end to end, built from the step workflows `pdf-ocr`, `pdf-alternating-merge` and `pdf-split` (called as child workflows with deterministic execution IDs `{run}-{step}-{item}`).

**Input**

```json
{"folder_path": "/<scanner inbox>", "apply_changes": true, "model": "mistral-small-latest", "connector": "", "merge_front_back": true, "split_parts": true}
```
(The workspace secret `SCAN_INBOX_FOLDER` (environment variable in the deployment) applies when `folder_path` is omitted; `model` selects the LLM for the checks and naming; `merge_front_back: false` skips the pair detection/merge, `split_parts: false` skips the split step.)

**Flow**: find `Scan.pdf` / `ScanNNNN.pdf` → park a copy of each raw scan under `.originals/pdf-scanner-processor/` → child `pdf-ocr` on the folder (its own backups under `.originals/pdf-ocr/`) → analyze the scans (page count, text layer, text snippet) → detect front/back candidates (consecutive scan order, same page count, size factor 0.5-2.0, both with text) and confirm each candidate with the LLM activity `front-back-check` (accept at confidence >= 0.7; the check is name-independent, so front and back may look completely different) → child `pdf-alternating-merge` per confirmed pair with the intermediate title `ScanMerged<N>.pdf` (N = larger scan number of the pair; sources archived under `.originals/pdf-alternating-merge/<target>/`) → name every resulting file (`name-document` with the nearest AGENTS.md rules — inbox folder or a parent folder; merged pairs via the combined text) → rename only when confident (>= 0.7), otherwise `left_unchanged` → child `pdf-split` per resulting file (splits only when confident).

**Result**: `{folder, applied, actions[]}`; each action carries `step` (`backup` / `ocr` / `front-back` / `merge` / `name` / `split`) plus the step-specific fields (`files`, `action`, `to`, `backup`, `confidence`, `result`, ...).

## pdf-alternating-merge

Alternating merge of duplex pairs, either directly via `pairs` in the start input or via a JSON control file (format: see ARCHITECTURE.md). Per entry: check the page count, interleave front/back pages alternately (back pages in reverse order), name, and upload next to the sources; the source files are backed up under `.originals/pdf-alternating-merge/<target name>/` (as `FrontPages.pdf` / `BackPages_reversed.pdf`). With a control file, the workflow writes the status back per entry (`done`/`error`); repeated runs process only entries without status. The orchestrator calls this step per detected pair with an explicit intermediate `title` (`ScanMerged<N>.pdf`).

**Input (variant A, direct)**

```json
{"pairs": [{"front": "/folder/Doc Front.pdf", "back": "/folder/Doc Back.pdf", "title": "2026-01-15 Invoice A"}], "apply_changes": true, "model": "mistral-small-latest", "connector": ""}
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

**Input**

```json
{"target_path": "/folder", "recursive": false, "force": false, "keep_original": true, "apply_changes": true, "languages": "deu+eng", "connector": ""}
```

**Result**: `{target, kind, applied, actions[]}` with `action` per file from `planned` / `replaced` / `skipped` / `error`; `note` reports limits and empty results.

## pdf-split

Splits a PDF into parts and names them; the parts stay in the folder of the original, the original is secured under `.originals/pdf-split/<stem>/`.

**Input**

```json
{"target_path": "/folder/document.pdf", "split_at": [2, 4], "plan_splits": true, "titles": null, "name_parts": true, "model": "mistral-small-latest", "apply_changes": true, "connector": ""}
```

- `split_at`: page numbers (1-based) AFTER which the PDF is cut — the given page stays in the previous part; always wins over planning. Example: `[2, 4]` on 8 pages yields parts 1-2, 3-4, 5-8. The same semantics are stated in the input schema (field description of `split_at`) and in the workflow description. A dry run (`apply_changes: false`) returns `action: "planned"` with the exact `pages` range of every part before anything is written.
- `plan_splits` (default `true`): AI planning of the split points; `false` without `split_at` is an error.
- `titles`: explicit part names (count must match); disable AI naming.
- `name_parts` (default `true`): AI naming; `false` => `<stem> Teil-N.pdf`.

AI-named parts are called `<title>.pdf` (no stem prefix), named by the nearest AGENTS.md rules.

**Result**: `{target, applied, action, pages, parts[], original?}`; `action` from `planned` / `split` / `no_split` / `clarification` / `error`, `parts` with `pages` (e.g. `"1-2"`), `count`, `to`.
