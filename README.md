> Language: English · [Deutsche Version](README.de.md)

# mistralai-workflows: PDF scan processing as Mistral workflows

[![CI](https://img.shields.io/github/actions/workflow/status/johkoenig/mistralai-workflows/ci.yml?branch=main&label=CI)](https://github.com/johkoenig/mistralai-workflows/actions/workflows/ci.yml)

One repository, one managed deployment in your Mistral workspace, four workflows that run on your documents **in your own storage**. The deployment has two levels: one **orchestrator** drives the whole scan-inbox process end to end and calls the three single-purpose **steps** as child workflows — each step can also be started on its own. Nothing leaves the connected storage except the OCR/naming prompts sent to the Mistral API.

## Workflows

**Orchestrator**

| Workflow | What it does |
|---|---|
| `pdf-scanner-processor` | End-to-end over a scanner inbox folder: OCR of new scans, front/back detection + alternating merge (intermediate `ScanMerged<N>.pdf`), LLM naming to `YYYY-MM-DD title.pdf`, confidence-checked split per file |

**Steps** — each does exactly one task and is also startable on its own

| Workflow | What it does |
|---|---|
| `pdf-ocr` | OCR for a single PDF or a whole folder (optionally recursive) |
| `pdf-alternating-merge` | Merges duplex pairs (front/back pages interleaved) via a JSON control file or a direct `pairs` start input |
| `pdf-split` | Splits a PDF at given page numbers or LLM-planned split points (asks back when unsure) |

All four are started **manually** (Studio, chat, or MCP `execute_workflow`) — never by schedule or webhook, so a run can never tear apart an incomplete scan job.

## Quick start

1. **Deploy**: create a managed workflow deployment named `mistralai-workflows` from this repository — Dockerfile `deploy/docker/Dockerfile.worker`, build context `.`, `hardened=true` (the workflows run `on_behalf_of` you). The deployment parameters are listed in [OPERATIONS.md](OPERATIONS.md).
2. **Secrets**: workspace secret `scan_inbox_folder` (bound as env var `SCAN_INBOX_FOLDER`) = default scanner inbox for the `pdf-scanner-processor` workflow; workspace secret `file_connector_names` (bound as `FILE_CONNECTOR_NAMES`, comma-separated, default `nextcloud`) = your file connector(s).
3. **Call a workflow** in Studio/chat or via MCP `execute_workflow`. The most important start options at a glance (complete reference with JSON examples and result shapes: [WORKFLOWS.md](WORKFLOWS.md)):

| Option | Meaning | Default |
|---|---|---|
| `folder_path` | Scanner inbox folder (`pdf-scanner-processor`) | value of the `SCAN_INBOX_FOLDER` secret |
| `target_path` | PDF file or folder (`pdf-ocr`, `pdf-split`) | required |
| `pairs` / `control_file` | Duplex pairs for `pdf-alternating-merge`: inline list or path of a JSON control file | one of the two is required |
| `split_at` | Explicit split points for `pdf-split`: page numbers (1-based) after which the PDF is cut | LLM-planned split points |
| `merge_front_back` | Orchestrator: detect and merge front/back pairs | `true` |
| `split_parts` | Orchestrator: run the split step on every resulting file | `true` |
| `apply_changes` | `false` = dry run: everything is read, OCRed and planned, nothing is written | `true` |
| `model` | LLM for naming, planning and the front/back check | `mistral-small-latest` |
| `connector` | File connector from `FILE_CONNECTOR_NAMES` that this call should target | default connector |

- `apply_changes: false` is always a **dry run**: everything is read, OCRed and planned, the result shows what would happen — nothing is written, not even status feedback into control files.

## Conventions

Built into the project (base prompt and code):

- Files are named `YYYY-MM-DD short, descriptive title.pdf` (ASCII, umlauts transliterated, max. 80 characters of title; without a reliable date just the title) — this date schema is part of the project's base prompt. Merged front/back pairs first get the intermediate name `ScanMerged<N>.pdf` (N = larger scan number of the pair) and are renamed to the final title afterwards.
- Files are never overwritten (collisions get `_2`, `_3`, ...).
- The LLM names and asks in the language of the document.
- **Originals are always backed up** under `<folder>/.originals/<workflow>/` — on by default in all four workflows (split and merge use a subfolder per file/target). The workflows never delete anything; clean up `.originals/` by hand once you have verified the results.
- OCR, download and upload activities send heartbeats: a crashed worker is detected within ~2 minutes and the activity is retried automatically.

From your folder's `AGENTS.md` (folder-specific, **not part of this repository**):

- Folder-specific naming rules — date-schema details, exceptions, qualifiers, e.g. personal documents with `(first name)` at the end or a `#xxxx` marker for health-insurance receipts — live in an `AGENTS.md` file in the respective folder and are passed to the LLM with priority over the base prompt (max. 4000 characters). If the file is missing, only the base prompt applies.

## Privacy

All file operations run through your workspace's file connector **on behalf of you** (`on_behalf_of=True`, hence the hardened deployment). Documents are read and written only in your own storage; the only external calls are the Mistral API calls for OCR text extraction and LLM naming/planning. Secrets (`SCAN_INBOX_FOLDER`, `FILE_CONNECTOR_NAMES`) live as workspace secrets and are bound as environment variables — never in the repository or the image.

## License

GNU Affero General Public License v3.0 only (AGPL-3.0-only), see [LICENSE](LICENSE). The AGPL's §13 network-use clause applies: if you offer this software as a service over a network, you must make the corresponding source available to the network users. A link to this repository is sufficient for that: the complete Corresponding Source is publicly available here and can be obtained free of charge.

## For developers

| File | Contents |
|---|---|
| [CONTRIBUTING.md](CONTRIBUTING.md) | Local setup, tests, CI gates |
| [AGENTS.md](AGENTS.md) | The binding working rules |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Conventions and division of work |
| [OPERATIONS.md](OPERATIONS.md) | Deployment and day-to-day operations |
| [CHANGELOG.md](CHANGELOG.md) | Change history |
