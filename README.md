> Language: English · [Deutsche Version](README.de.md)

# mistralai-workflows: PDF scan processing as Mistral workflows — step workflows (OCR, alternating merge, split) + orchestrator

[![CI](https://img.shields.io/github/actions/workflow/status/johkoenig/mistralai-workflows/ci.yml?branch=main&label=CI)](https://github.com/johkoenig/mistralai-workflows/actions/workflows/ci.yml)

One repository, one managed deployment in your Mistral workspace, four workflows that run on your documents **in your own storage** — three single-purpose steps plus one orchestrator that drives the whole scan-inbox process end to end. Nothing leaves the connected storage except the OCR/naming prompts sent to the Mistral API.

| Workflow | What it does |
|---|---|
| `pdf-scanner-processor` | Orchestrator over a scanner inbox folder: OCR of new scans, front/back detection + alternating merge (`ScanMerged<N>.pdf`), LLM naming to `YYYY-MM-DD title.pdf`, confidence-checked split — built from the three steps below |
| `pdf-ocr` | OCR for a single PDF or a whole folder (optionally recursive) |
| `pdf-alternating-merge` | Merges duplex pairs (front/back pages interleaved) via a JSON control file or a direct `pairs` start input |
| `pdf-split` | Splits a PDF at given page numbers or LLM-planned split points (asks back when unsure) |

All four are started **manually** (Studio, chat, or MCP `execute_workflow`) — never by schedule or webhook, so a run can never tear apart an incomplete scan job. Within one orchestrator run, `pdf-scanner-processor` calls the three steps as child workflows.

## Quick start

1. **Deploy**: create a managed workflow deployment named `mistralai-workflows` from this repository — Dockerfile `deploy/docker/Dockerfile.worker`, build context `.`, `hardened=true` (the workflows run `on_behalf_of` you). The deployment parameters are listed in [OPERATIONS.md](OPERATIONS.md).
2. **Secrets**: workspace secret `scan_inbox_folder` (bound as env var `SCAN_INBOX_FOLDER`) = default scanner inbox for the `pdf-scanner-processor` workflow; workspace secret `file_connector_names` (bound as `FILE_CONNECTOR_NAMES`, comma-separated, default `nextcloud`) = your file connector(s).
3. **Call a workflow** in Studio/chat or via MCP `execute_workflow`. Example start inputs:

```json
{"folder_path": "/<scanner inbox>", "apply_changes": false, "merge_front_back": true, "split_parts": true}
```

```json
{"control_file": "/<scanner inbox>/steuerdatei.json", "apply_changes": true, "model": "mistral-small-latest"}
```

```json
{"target_path": "/folder/file.pdf", "recursive": false, "force": false, "apply_changes": true, "languages": "deu+eng"}
```

```json
{"target_path": "/folder/document.pdf", "split_at": [2, 4], "apply_changes": true}
```

- `apply_changes: false` is always a **dry run**: everything is read, OCRed and planned, the result shows what would happen — nothing is written, not even status feedback into control files.
- Every start input also accepts `connector` (name of one of the configured file connectors, empty = default) and `model` (LLM for naming/planning, default `mistral-small-latest`).
- Complete reference of inputs, result shapes and examples: [WORKFLOWS.md](WORKFLOWS.md).

## Conventions

- Files are named `YYYY-MM-DD short, descriptive title.pdf` (ASCII, umlauts transliterated); files are never overwritten (collisions get `_2`, `_3`, ...).
- Folder-specific naming rules (date schema, exceptions, qualifiers) live in an `AGENTS.md` file in the respective folder and are passed to the LLM with priority over the base prompt (max. 4000 characters).
- The LLM names, renames and asks in the language of the document. Merged front/back pairs first get the intermediate name `ScanMerged<N>.pdf` (N = larger scan number of the pair) and are renamed to the final title afterwards.
- **Originals are always backed up** under `<folder>/.originals/<workflow>/` — on by default in all four workflows (split and merge use a subfolder per file/target). The workflows never delete anything; clean up `.originals/` by hand once you have verified the results.
- OCR, download and upload activities send heartbeats: a crashed worker is detected within ~2 minutes and the activity is retried automatically.

## Privacy

All file operations run through your workspace's file connector **on behalf of you** (`on_behalf_of=True`, hence the hardened deployment). Documents are read and written only in your own storage; the only external calls are the Mistral API calls for OCR text extraction and LLM naming/planning. Secrets (`SCAN_INBOX_FOLDER`, `FILE_CONNECTOR_NAMES`) live as workspace secrets and are bound as environment variables — never in the repository or the image.

## License

GNU Affero General Public License v3.0 only (AGPL-3.0-only), see [LICENSE](LICENSE). The AGPL's §13 network-use clause applies: if you offer this software as a service over a network, you must make the corresponding source available to the network users.

## For developers

You want to extend the workflows or hack on this repo? The entry points are [CONTRIBUTING.md](CONTRIBUTING.md) (local setup, tests, CI gates), [AGENTS.md](AGENTS.md) (the binding working rules) and [ARCHITECTURE.md](ARCHITECTURE.md) (conventions and division of work). Deployment and day-to-day operations: [OPERATIONS.md](OPERATIONS.md). Change history: [CHANGELOG.md](CHANGELOG.md).
