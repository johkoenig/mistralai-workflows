> Language: English · [Deutsche Version](README.de.md)

# mistralai-workflows: PDF scan processing as Mistral workflows

[![CI](https://img.shields.io/github/actions/workflow/status/johkoenig/mistralai-workflows/ci.yml?branch=main&label=CI)](https://github.com/johkoenig/mistralai-workflows/actions/workflows/ci.yml)

One repository, one managed deployment in your Mistral workspace, five workflows that run on your documents **in your own storage**. The deployment has two levels: one **orchestrator** drives the whole scan-inbox process end to end and calls the four single-purpose **steps** as child workflows — each step can also be started on its own. Nothing leaves the connected storage except the OCR/naming prompts sent to the Mistral API.

## Workflows

**Orchestrator**

| Workflow | What it does |
|---|---|
| `pdf-scanner-processor` | End-to-end over a scanner inbox folder: OCR of new scans, front/back detection + alternating merge (intermediate `ScanMerged<N>.pdf`), LLM naming per the nearest AGENTS.md, confidence-checked split per file |

**Steps** — each does exactly one task and is also startable on its own

| Workflow | What it does |
|---|---|
| `pdf-ocr` | OCR for a single PDF or a whole folder (optionally recursive) |
| `pdf-alternating-merge` | Merges duplex pairs (front/back pages interleaved) via a JSON control file or a direct `pairs` start input |
| `pdf-split` | Splits a PDF at given page numbers or LLM-planned split points (asks back when unsure) |
| `pdf-vision` | Vision OCR fallback for weak OCR results (sidecar `.txt` + PDF metadata flag; text layer untouched) |

All five are started **manually** (Studio, chat, or MCP `execute_workflow`) — never by schedule or webhook, so a run can never tear apart an incomplete scan job.

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
| `split_parts` | Orchestrator: run the split step on every unit before naming | `true` |
| `ocr_languages` | Orchestrator: OCR languages for the pdf-ocr child (scanner inbox: `deu`) | `deu+eng` |
| `vision_fallback` / `vision_model` | Orchestrator: route low-confidence OCR results through `pdf-vision`; model override (`PDF_VISION_MODEL` env default) | `true` / `""` |
| `apply_changes` | `false` = dry run: everything is read, OCRed and planned, nothing is written | `true` |
| `model` | LLM for naming, planning and the front/back check | `mistral-small-latest` |
| `connector` | File connector from `FILE_CONNECTOR_NAMES` that this call should target | default connector |

- `apply_changes: false` is always a **dry run**: everything is read, OCRed and planned, the result shows what would happen — nothing is written, not even status feedback into control files.

## Conventions

Built into the project (code and base prompt):

- The project itself defines **no file name schema** — names are chosen by the LLM. The project only sanitizes names technically (ASCII, umlauts transliterated, max. 80 characters) and never overwrites files (collisions get `_2`, `_3`, ...). Merged front/back pairs first get the intermediate name `ScanMerged<N>.pdf` (N = larger scan number of the pair) and are renamed to the final name afterwards.
- The LLM names and asks in the language of the document.
- **Originals are always backed up** under `<folder>/.originals/<workflow>/` — on by default in all five workflows (merge uses a subfolder per target; the others park the file directly). The workflows never delete anything; clean up `.originals/` by hand once you have verified the results.
- OCR, download and upload activities send heartbeats: a crashed worker is detected within ~2 minutes and the activity is retried automatically.

Naming conventions come from your `AGENTS.md` — **not part of this repository**:

- File name conventions (schema such as a date prefix, exceptions, qualifiers) live in an `AGENTS.md` in the respective folder **or a parent folder** — the nearest one wins. It is passed to the LLM with priority over the base prompt (max. 4000 characters). If no `AGENTS.md` exists anywhere up the tree, the LLM simply picks a suitable name itself. `AGENTS.md` content is private to your storage and is never reflected in this repository or its documentation.

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
