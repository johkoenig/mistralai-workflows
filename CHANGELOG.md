> Language: English · [Deutsche Version](CHANGELOG.de.md)

# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html). License: AGPL-3.0-only, see [LICENSE](LICENSE).

Entries are one-liners; the pull request is referenced in parentheses at the end, e.g. (#24). GitHub links it automatically.

## [Unreleased]

### Added

### Changed

- Release and changelog process codified in `AGENTS.md`: content PRs, six standard categories, dedicated release PR, "Bump to Next Version" commit, annotated tags on `main`, no dev suffix. (#37)
- Changelog entries are one-liners with the PR referenced in parentheses; existing entries reworked accordingly. (#37)

### Deprecated

### Removed

### Fixed

### Security

## [0.2.1] - 2026-11-24

### Changed

- `pdf-scanner-processor` runs `pdf-split` before naming; only units that were not split are renamed. (#27)
- `pdf-split` parks the original directly under `.originals/pdf-split/<name>`. (#27)
- Changelogs follow Keep a Changelog with versioned sections and link definitions. (#25)
- Product packages (`workflows-app`, `cli`, `env`, `utils`) bumped to 0.2.1. (#27)

### Fixed

- `pdf-split` AI planning prompt carries explicit document-boundary heuristics (date, running page number, letterhead; third-party billing names excepted). (#28)

## [0.2.0] - 2026-11-24

Two-level architecture: three single-purpose step workflows plus one orchestrator for the whole scan-inbox process.

### Added

- `pdf-scanner-processor` orchestrator: backups, OCR, front/back detection, alternating merge, naming, split; child workflows with deterministic IDs; full dry run. (#24)
- Step workflows under `workflows/steps/`: `pdf-ocr`, `pdf-split`, `pdf-alternating-merge`, single-purpose and standalone triggerable. (#24)
- LLM activity `front-back-check`: name-independent content check for front/back candidates. (#24)
- `load-naming-rules` reads the nearest `AGENTS.md`; without one, the LLM picks a suitable name. (#24)
- Uniform originals backups under `.originals/<workflow>`. (#23)

### Changed

- `pdf-merge-duplex` renamed to `pdf-alternating-merge`. (#24)
- Naming conventions moved out of the project; AGENTS.md rules take precedence. (#24)
- Studio-facing titles and descriptions no longer contain "manual". (#24)
- README reworked: orchestrator/steps split, options table, conventions and AGPL §13 license note. (#24)
- Product packages bumped to 0.2.0. (#24)

### Removed

- `pdf-scan-inbox-triage`, replaced by `pdf-scanner-processor` (old Studio entries disappear on redeploy). (#24)
- Lexical text-similarity pair detection and front/back name suffixes. (#24)

## [0.1.0] - 2026-11-22

First public release of `mistralai-workflows` — Mistral workflows for scanner and PDF automation on Nextcloud: `pdf-scan-inbox-triage`, `pdf-merge-duplex`, `pdf-ocr`, `pdf-split` in one managed deployment.

### Added

- Full dry run for every workflow via `apply_changes: false`.
- Originals always secured under `.originals/<workflow>/`, never overwritten; workflows never delete.
- Folder-specific naming via `AGENTS.md`, selectable file connector, LLM answers in the document's language.
- Heartbeats detect a crashed worker within ~2 minutes and retry automatically.
- Worker image on Python 3.15, one blocking CI gate per PR, Dependabot with auto-merge queue.
- AGPL §13: the source is available at this repository for network use.

The git history was squashed when the repository was recreated for publication; earlier PR references no longer resolve.

[Unreleased]: https://github.com/johkoenig/mistralai-workflows/compare/dbc1d2c9...HEAD
[0.2.1]: https://github.com/johkoenig/mistralai-workflows/pull/27
[0.2.0]: https://github.com/johkoenig/mistralai-workflows/compare/a2db23a...d2e3984
[0.1.0]: https://github.com/johkoenig/mistralai-workflows/tree/a2db23a
