> Language: English · [Deutsche Version](CHANGELOG.de.md)

# Changelog

## 2026-11: initial release

First public release of `mistralai-workflows` (license: AGPL-3.0-only, see [LICENSE](LICENSE)). Mistral workflows for scanner and PDF automation on Nextcloud — one managed deployment, four manually started workflows (`on_behalf_of`):

- `pdf-scan-inbox-triage` — OCR + LLM naming of new scans in a scanner inbox folder; duplex pairs are detected and named with a front/back suffix in the document's language, but not merged.
- `pdf-merge-duplex` — interleaves duplex pairs (front/back pages alternately, back pages reversed) via a JSON control file or a direct `pairs` input; source files are archived under `merged/`.
- `pdf-ocr` — generic PDF OCR processor for a single PDF or a folder (optionally recursive); `keep_original` preserves the original as `<name>_orig.pdf`.
- `pdf-split` — splits a PDF at given page numbers or LLM-planned split points (asks back on uncertainty); the original is secured under `.originals/pdf-split/`.

Highlights:

- The file connector is selectable per workflow call (`FILE_CONNECTOR_NAMES` env + `connector` start input) — no connector ID in the source.
- English codebase; the LLM names and answers in the document's language (German document → `Vorderseite`/`Rueckseite`, otherwise `Front`/`Back`).
- Originals are always kept (`.originals/<workflow>/`, `merged/`, `<name>_orig.pdf`); nothing is ever overwritten or deleted by the workflows.
- Dry runs via `apply_changes: false`; heartbeats on OCR/download/upload detect a crashed worker within ~2 minutes and retry the activity.
- AGPL §13 (network use): if this software is offered as a network service, the source is available at this repository.
- The documentation refers to the project consistently as `mistralai-workflows`.
- Bug fix: the LLM activities `name-document` and `plan-split` now send `top_p=1` together with `temperature=0.0`; the Chat API rejects greedy sampling with any other `top_p` (HTTP 400 `invalid_request_greedy_sampling`).

The git history was squashed to a single commit when the repository was recreated for publication; earlier PR references no longer resolve.
