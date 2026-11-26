> Language: English · [Deutsche Version](CHANGELOG.de.md)

# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html). License: AGPL-3.0-only, see [LICENSE](LICENSE).

## [Unreleased]

## [0.2.1] - 2026-11-24

### Changed

- `pdf-scanner-processor` now runs `pdf-split` **before** naming (Closes #26): splitting is content-based, not name-based, and the split step names its parts via the LLM itself — naming the pre-split file first was a wasted LLM call. Only units that were NOT split are renamed afterwards (`name-document`, nearest AGENTS.md rules, confidence >= 0.7, otherwise `left_unchanged`); split parts are reported as `named_by_split` in the result actions.
- `pdf-split` parks the original directly under `.originals/pdf-split/<name>` — no per-file `<stem>/` subfolder anymore (a split has exactly one source file, so the subfolder always contained exactly one file). Only `pdf-alternating-merge` still uses a subfolder (per merge target).
- `CHANGELOG.md` / `CHANGELOG.de.md` now follow the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format: versioned sections with `Added` / `Changed` / `Removed` / `Fixed` subsections and link definitions at the bottom.
- Product packages (`workflows-app`, `cli`, `env`, `utils`) bumped to 0.2.1.

### Fixed

- `pdf-split` AI planning prompt now carries explicit document-boundary heuristics: a changed date, a changed (running) page number (new 'Seite 1 von 2' / 'Page 1 of 2' run) or a new company letterhead at the top of a page indicate a new document — except a company name appearing only via third-party billing ('Fremdleistung im Namen von XY' / 'on behalf of XY'). The model must also verify each planned boundary before finalizing and remove (merge back) boundaries that would cut through one coherent document.

## [0.2.0] - 2026-11-24

Two-level architecture: three single-purpose step workflows plus one orchestrator that runs the whole scan-inbox process end to end.

### Added

- `pdf-scanner-processor` — orchestrator over a scanner inbox folder: parks the raw scans under `.originals/pdf-scanner-processor/`, runs OCR, detects front/back pairs (structural prefilter + LLM activity `front-back-check`, name-independent), merges each pair via `pdf-alternating-merge` as `ScanMerged<N>.pdf`, names the results via LLM, then runs `pdf-split` per file (only when confident). Steps run as child workflows with deterministic execution IDs; `apply_changes: false` stays a full dry run.
- Step workflows in `apps/worker/src/worker/workflows/steps/`: `pdf-ocr`, `pdf-split`, `pdf-alternating-merge` — each does exactly one task and can be triggered on its own as well as by the orchestrator.
- LLM activity `front-back-check`: name-independent content check for front/back candidates (acceptance at confidence >= 0.7).
- `load-naming-rules` now reads the nearest `AGENTS.md` in the folder or a parent folder; without one, the LLM picks a suitable name itself.

### Changed

- `pdf-merge-duplex` renamed to `pdf-alternating-merge` (module, class, workflow name; Studio entry "Alternating merge PDF duplex").
- Naming conventions are no longer part of the project: the base prompt is generic and AGENTS.md rules take precedence. AGENTS.md content is private and is not reflected in this repository or its docs.
- The word "manual" is removed from all Studio-facing workflow titles/descriptions; the manual-start convention remains documented in the repo docs.
- README reworked: one-line H1, orchestrator/steps split in the overview, human-readable options table instead of JSON start inputs, conventions split into project-built-in vs. AGENTS.md, license note (a link to this repository satisfies the AGPL §13 source-availability obligation), developer docs as a table.
- Product packages (`workflows-app`, `cli`, `env`, `utils`) bumped to 0.2.0.

### Removed

- `pdf-scan-inbox-triage` — replaced by `pdf-scanner-processor`. Note for existing deployments: after a redeploy, the Studio entries `pdf-scan-inbox-triage` and `pdf-merge-duplex` disappear; their replacements are `pdf-scanner-processor` and `pdf-alternating-merge`.
- Lexical text-similarity pair detection and the front/back name suffixes of the old triage workflow.

## [0.1.0] - 2026-11-22

First public release of `mistralai-workflows`. Mistral workflows for scanner and PDF automation on Nextcloud — one managed deployment, four manually started workflows (`on_behalf_of`):

### Added

- `pdf-scan-inbox-triage` — OCR + LLM naming of new scans in a scanner inbox folder; duplex pairs detected and named with a front/back suffix in the document's language, but not merged; every scan parked under `.originals/pdf-scan-inbox-triage/` before it is modified.
- `pdf-merge-duplex` — interleaves duplex pairs (front/back pages alternately, back pages reversed) via a JSON control file or a direct `pairs` input; source files backed up under `.originals/pdf-merge-duplex/`.
- `pdf-ocr` — generic PDF OCR processor for a single PDF or a folder (optionally recursive); `keep_original` (default on) secures the original under `.originals/pdf-ocr/`.
- `pdf-split` — splits a PDF at given page numbers or LLM-planned split points (asks back on uncertainty); the original is secured under `.originals/pdf-split/`.
- Dry run for every workflow via `apply_changes: false`: everything is read, OCRed and planned, the result shows what would happen — nothing is written.
- Originals are always secured under `<folder>/.originals/<workflow>/` (on by default in all four workflows, never overwritten); the workflows never delete — clean up by hand after verifying the results.
- Folder-specific naming rules via `AGENTS.md` in the folder, file connector selectable per call (`connector` start input), and the LLM names and answers in the document's language.
- Heartbeats on OCR/download/upload detect a crashed worker within ~2 minutes and retry the activity automatically.
- AGPL §13 (network use): if this software is offered as a network service, the source is available at this repository.
- Under the hood: worker image on Python 3.15, one blocking CI gate per PR, Dependabot weekly updates with an auto-merge queue (human approval + green CI still gate the merge).

The git history was squashed to a single commit when the repository was recreated for publication; earlier PR references no longer resolve.

[Unreleased]: https://github.com/johkoenig/mistralai-workflows/compare/d2e3984...HEAD
[0.2.1]: https://github.com/johkoenig/mistralai-workflows/pull/27
[0.2.0]: https://github.com/johkoenig/mistralai-workflows/compare/a2db23a...d2e3984
[0.1.0]: https://github.com/johkoenig/mistralai-workflows/tree/a2db23a
