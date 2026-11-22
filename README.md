> Language: English · [Deutsche Version](README.de.md)

# mistralai-workflows

[![CI](https://img.shields.io/github/actions/workflow/status/johkoenig/mistralai-workflows/ci.yml?branch=main&label=CI)](https://github.com/johkoenig/mistralai-workflows/actions/workflows/ci.yml)

Mistral workflows for scanner and PDF automation on your own file storage (Nextcloud). One repository (`mistralai-workflows`), one managed deployment (`mistralai-workflows`), four workflows that run on your documents **in your own storage** — nothing leaves your Nextcloud except the OCR/naming prompts sent to the Mistral API.

| Workflow | What it does |
|---|---|
| `pdf-scan-inbox-triage` | OCR and LLM naming of new scans in a scanner inbox folder; detects duplex pairs and names them with front/back suffixes (but does not merge) |
| `pdf-merge-duplex` | Merges duplex pairs (front/back pages interleaved) via a JSON control file or a direct `pairs` start input; archives the sources under `merged/` |
| `pdf-split` | Splits a PDF into parts — at given page numbers or planned by an LLM (asks back on uncertainty); original secured under `.originals/pdf-split/` |
| `pdf-ocr` | Generic PDF OCR processor for a single PDF or a folder (optionally recursive); `keep_original` preserves the original as `<name>_orig.pdf` |

All workflows are started manually only (Studio, chat, or MCP `execute_workflow`) — never by schedule or webhook, so a run can never tear apart an incomplete scan job.

The complete workflow reference (inputs, result shapes, action glossary, examples) is in [WORKFLOWS.md](WORKFLOWS.md); deployment and operations in [OPERATIONS.md](OPERATIONS.md); architecture and conventions in [ARCHITECTURE.md](ARCHITECTURE.md); change history in [CHANGELOG.md](CHANGELOG.md).

## Prerequisites

- A file connector (e.g. Nextcloud) registered in your Mistral workspace with a valid credential. The worker learns the connector names from the environment variable `FILE_CONNECTOR_NAMES` (comma-separated, default `nextcloud`); the deployment binds the workspace secret `file_connector_names` as this variable. Per workflow call, the optional start-input field `connector` selects the target connector — with several connectors active, one call can target any of them.
- The workspace secret `scan_inbox_folder` (bound as `SCAN_INBOX_FOLDER`): default scanner inbox for `pdf-scan-inbox-triage`; a `folder_path` given at start overrides it.
- The Mistral GitHub App on this repository (for the managed deployment).
- The worker image system-installs `ghostscript`, `tesseract-ocr-all`, `jbig2dec` (see `deploy/docker/worker-packages/workflows.txt`).

## Starting a workflow

- **Studio**: Workflows → Start, or from a chat / via MCP `execute_workflow` — all four workflows support chat starts (`on_behalf_of`).
- **Example inputs**:

```json
{"folder_path": "/<scanner inbox>", "apply_changes": true, "model": "mistral-small-latest", "connector": ""}
```

```json
{"control_file": "/<scanner inbox>/control.json", "apply_changes": true, "model": "mistral-small-latest", "connector": ""}
```

```json
{"target_path": "/folder/or/file.pdf", "recursive": false, "force": false, "keep_original": false, "apply_changes": true, "connector": ""}
```

```json
{"target_path": "/<scanner inbox>/document.pdf", "split_at": [2, 4], "apply_changes": true, "connector": ""}
```

- `apply_changes: false` is always a **dry run**: everything is read, OCRed and planned, the result reports what would happen (`planned` actions) — but nothing is written, not even status feedback into control files.
- `model` selects the LLM used for naming/planning (default `mistral-small-latest`).

## Folder and file conventions

- AI-named files follow `YYYY-MM-DD short, descriptive title.pdf` (ASCII, umlauts transliterated); collisions get `_2`, `_3`, ... Files are never overwritten.
- **Naming rules per folder**: an `AGENTS.md` file in the respective folder (e.g. the scanner inbox) is read before every AI naming step and passed to the model with priority over the base prompt (max. 4000 characters). This is where you put your date schema, exceptions, and qualifiers.
- **Language**: the LLM titles, renames and asks in the language of the document (German document → `Vorderseite`/`Rueckseite`, otherwise `Front`/`Back`, and German titles).
- **Originals are always kept**: split → `.originals/pdf-split/<stem>/`, duplex merge → `merged/<target name>/` (with `FrontPages.pdf` / `BackPages_reversed.pdf`), OCR with `keep_original` → `<name>_orig.pdf`. The workflows never delete; clean up by hand once you have verified the results.

## Robustness

- OCR, download and upload activities send heartbeats every 30 s (`heartbeat_timeout` 120 s, 2 attempts per activity): if the worker dies mid-run (rollout, crash), this is detected within ~2 minutes and the activity is retried automatically instead of waiting silently until the 45-minute `start_to_close_timeout`.
- Every OCR run is logged (start, end, duration, errors) in the deployment runtime logs.
- Dry runs execute the OCR fully and therefore report real errors; nothing is written back.

## Privacy and data handling

- All file operations run through your workspace's file connector **on behalf of you** (`on_behalf_of=True`); the deployment therefore runs hardened (`hardened=true`).
- Documents are read and written only in your own storage via the connector. The only external calls are the Mistral API calls for OCR text extraction and LLM naming/planning.
- Secrets (`SCAN_INBOX_FOLDER`, `FILE_CONNECTOR_NAMES`) live as workspace secrets and are bound as environment variables — never in the repository or the image.

## License

This project is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0-only)** — see [LICENSE](LICENSE). The AGPL's "network use" clause (§13) applies: if you offer this software as a service over a network, you must make the corresponding source available to the network users.

## For developers

Repository structure, local setup, tests, and the CI quality gates are documented in [CONTRIBUTING.md](CONTRIBUTING.md); the binding working rules (gates, typing, lint, PR conventions) are in [AGENTS.md](AGENTS.md).
