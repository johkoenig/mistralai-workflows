> Language: English · [Deutsche Version](WORKFLOWS.de.md)

# Workflow Reference

Complete reference of the four workflows of the `mistralai-workflows` deployment: purpose, start input, result shape, and examples. LLM-generated names, titles and follow-up questions follow the language of the document (the naming prompts instruct the model accordingly). Conventions and architecture are in [ARCHITECTURE.md](ARCHITECTURE.md), usage and deployment operations in [README.md](README.md) and [OPERATIONS.md](OPERATIONS.md). Every start input also accepts an optional `connector` field: the name of a file connector from `FILE_CONNECTOR_NAMES` (empty = default), so one call can target any of the configured connectors.

All workflows:

- are started manually only (Studio or chat, or MCP `execute_workflow`);
- use `apply_changes` (default `true`); `false` is the dry run: everything is read, planned and checked, but nothing is written (not even status feedback into control files);
- name files by the schema `YYYY-MM-DD short, descriptive title.pdf` (ASCII, umlauts transliterated), personal documents with `(first name)` at the end;
- read folder-specific naming rules from `<folder>/AGENTS.md` (max. 4000 characters; if the file is missing: only the base prompt), whenever the AI names something;
- never overwrite files (collisions get `_2`, `_3`, ...).

## Result and action glossary

Every run result contains `applied` at the top (true = written, false = planned/read only). Collecting workflows report an `actions` list, single-file workflows a single `action` field. Unified action vocabulary:

| Action | Meaning | Workflow |
|---|---|---|
| `planned` | dry run: this would happen | all |
| `planned_pair` | dry run: duplex pair would be named like this | triage |
| `renamed` | file renamed | triage |
| `renamed_pair` | duplex pair renamed | triage |
| `left_unchanged` | uncertain (confidence < 0.7 or no title), nothing changed | triage |
| `merged` | duplex pair merged | merge |
| `replaced` | PDF was replaced (via OCR) | ocr |
| `skipped` | file skipped (text layer present, `force` not set; backup file) | ocr |
| `split` | PDF was split | split |
| `no_split` | everything is one coherent document, no split | split |
| `clarification` | uncertain, follow-up question with `question` and `proposal`, nothing written | split |
| `error` | failure with `error` message | all |

## pdf-scan-inbox-triage

OCR and naming of new scans in the Nextcloud scanner inbox (top folder level only).

**Input**

```json
{"folder_path": "/<scanner inbox>", "apply_changes": true, "model": "mistral-small-latest", "connector": ""}
```
(The default folder in the original setup is the personal scanner inbox; the workspace secret `SCAN_INBOX_FOLDER` (environment variable in the deployment) applies when `folder_path` is omitted, and `model` selects the naming LLM.)

**Flow**: find `Scan.pdf` / `ScanNNNN.pdf` → analyze in parallel (OCR if there is no text layer; the result replaces the file in Nextcloud) → detect duplex pairs (same page count, size factor 0.5-2.0, text overlap >= 0.4) → name (`name-document` with the AGENTS.md rules of the inbox folder) → rename only. Pairs are not merged; they get a front/back suffix in the document's language (`Vorderseite`/`Rueckseite` for German documents, `Front`/`Back` otherwise); the merging is done by pdf-merge-duplex.

**Result**: `{folder, applied, actions[]}` with `action` per unit from `planned` / `planned_pair` / `renamed` / `renamed_pair` / `left_unchanged`.

## pdf-merge-duplex

Manual duplex merging, either directly via `pairs` in the start input or via a JSON control file (format: see ARCHITECTURE.md). Per entry: check the page count, interleave front/back pages alternately (back pages in reverse order), name, and upload next to the sources; the source files move to `merged/<target name>/`. With a control file, the workflow writes the status back per entry (`done`/`error`); repeated runs process only entries without status.

**Input (variant A, direct)**

```json
{"pairs": [{"front": "/folder/Doc Front.pdf", "back": "/folder/Doc Back.pdf", "title": "2026-01-15 Invoice A"}], "apply_changes": true, "model": "mistral-small-latest", "connector": ""}
```
(In the original setup the pairs reference the personal scanner inbox; the file names come from the triage workflow (front/back suffix in the document's language, e.g. `Front`/`Back` here).)

**Input (variant B, control file)**

```json
{"control_file": "/folder/steuerdatei.json", "apply_changes": true, "model": "mistral-small-latest"}
```
(`steuerdatei.json` is a German word for "control file"; the name is a convention, any filename works.)

An explicit `title` wins; otherwise the AI names (`name-document` with the AGENTS.md rules of the front page's folder). Uncertain naming marks the entry with an error.

**Result**: `{source, control_file, applied, processed, actions[]}` with `action` per entry from `planned` / `merged` / `error`.

## pdf-ocr

Generic OCR processor for a single PDF or a folder (`recursive: true` also subfolders). PDFs without a text layer are processed with OCRmyPDF (Tesseract `deu+eng`, deskew, automatic page rotation) and replaced; PDFs with a text layer are skipped (`force: true` forces reprocessing). `keep_original: true` secures the original as `<name>_orig.pdf`; existing `*_orig.pdf` are never touched. At most 200 files per run.

**Input**

```json
{"target_path": "/folder", "recursive": false, "force": false, "keep_original": false, "apply_changes": true, "languages": "deu+eng", "connector": ""}
```

**Result**: `{target, kind, applied, actions[]}` with `action` per file from `planned` / `replaced` / `skipped` / `error`; `note` reports limits and empty results.

## pdf-split

Splits a PDF into parts and names them; the parts stay in the folder of the original, the original is secured under `.originals/pdf-split/<stem>/`.

**Input**

```json
{"target_path": "/folder/document.pdf", "split_at": [2, 4], "plan_splits": true, "titles": null, "name_parts": true, "model": "mistral-small-latest", "apply_changes": true, "connector": ""}
```

- `split_at`: page numbers (1-based) to split AFTER; always wins over planning.
- `plan_splits` (default `true`): AI planning of the split points; `false` without `split_at` is an error.
- `titles`: explicit part names (count must match); disable AI naming.
- `name_parts` (default `true`): AI naming; `false` => `<stem> Teil-N.pdf`.

AI-named parts are called `<title>.pdf` (no stem prefix), named by the AGENTS.md rules of the folder.

**Result**: `{target, applied, action, pages, parts[], original?}`; `action` from `planned` / `split` / `no_split` / `clarification` / `error`, `parts` with `pages` (e.g. `"1-2"`), `count`, `to`.
