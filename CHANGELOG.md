> Language: English · [Deutsche Version](CHANGELOG.de.md)

# Changelog

## 0.2.0 (2026-11): two-level architecture — step workflows + orchestrator

The workflows are now a two-level system: three single-purpose step workflows plus one orchestrator that runs the whole scan-inbox process end to end.

- `pdf-scanner-processor` (new, replaces `pdf-scan-inbox-triage`) — orchestrator over a scanner inbox folder: parks the raw scans under `.originals/pdf-scanner-processor/`, runs OCR, detects front/back pairs (structural prefilter + LLM `front-back-check`, name-independent), merges each pair via `pdf-alternating-merge` as `ScanMerged<N>.pdf`, names the results via LLM, then runs `pdf-split` per file (only when confident). Steps run as child workflows with deterministic execution IDs; `apply_changes: false` stays a full dry run.
- Naming conventions are now fully AGENTS.md-driven and no longer part of the project: the base prompt is generic, and `load-naming-rules` reads the nearest `AGENTS.md` in the folder or a parent folder (without one, the LLM picks a suitable name itself). AGENTS.md content is private and is not reflected in this repository or its docs.
- `pdf-ocr`, `pdf-split`, `pdf-alternating-merge` (renamed from `pdf-merge-duplex`) — moved into `workflows/steps/`; each does exactly one task and can be triggered on its own as well as by the orchestrator.
- The word "manual" is removed from all Studio-facing workflow titles/descriptions; the manual-start convention remains documented in the repo docs.
- Note for existing deployments: after a redeploy, the Studio entries `pdf-scan-inbox-triage` and `pdf-merge-duplex` disappear; their replacements are `pdf-scanner-processor` and `pdf-alternating-merge`.

Product packages (`workflows-app`, `cli`, `env`, `utils`) are now at version 0.2.0.

## 2026-11: initial release

First public release of `mistralai-workflows` (license: AGPL-3.0-only, see [LICENSE](LICENSE)). Mistral workflows for scanner and PDF automation on Nextcloud — one managed deployment, four manually started workflows (`on_behalf_of`):

- `pdf-scan-inbox-triage` — OCR + LLM naming of new scans in a scanner inbox folder; duplex pairs are detected and named with a front/back suffix in the document's language, but not merged; every scan is parked under `.originals/pdf-scan-inbox-triage/` before it is modified.
- `pdf-merge-duplex` — interleaves duplex pairs (front/back pages alternately, back pages reversed) via a JSON control file or a direct `pairs` input; source files are backed up under `.originals/pdf-merge-duplex/`.
- `pdf-ocr` — generic PDF OCR processor for a single PDF or a folder (optionally recursive); `keep_original` (default on) secures the original under `.originals/pdf-ocr/`.
- `pdf-split` — splits a PDF at given page numbers or LLM-planned split points (asks back on uncertainty); the original is secured under `.originals/pdf-split/`.

Highlights:

- Dry run for every workflow via `apply_changes: false`: everything is read, OCRed and planned, the result shows what would happen — nothing is written.
- Originals are always secured under `<folder>/.originals/<workflow>/` (on by default in all four workflows, never overwritten); the workflows never delete — clean up by hand after verifying the results.
- Folder-specific naming rules via `AGENTS.md` in the folder, file connector selectable per call (`connector` start input), and the LLM names and answers in the document's language.
- Heartbeats on OCR/download/upload detect a crashed worker within ~2 minutes and retry the activity automatically.
- AGPL §13 (network use): if this software is offered as a network service, the source is available at this repository.
- Under the hood: worker image on Python 3.15, one blocking CI gate per PR, Dependabot weekly updates with an auto-merge queue (human approval + green CI still gate the merge).

The git history was squashed to a single commit when the repository was recreated for publication; earlier PR references no longer resolve.
