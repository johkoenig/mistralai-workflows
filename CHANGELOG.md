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
- `pdf-split`: the cut-after semantics of `split_at` are now stated in the input schema (field description), the workflow description, and WORKFLOWS.md — `split_at` lists the page numbers AFTER which to split; the given page stays in the previous part (example: `[2, 4]` on 8 pages yields parts 1-2, 3-4, 5-8).
- Added `.github/dependabot.yml` (weekly updates for `uv`, `bun`, `github-actions`, and the worker `docker` image) and `SECURITY.md` (private vulnerability reporting via GitHub security advisories).
- New CI gate `check_docker_base.py`: every `FROM python:X.Y` base in `deploy/docker/Dockerfile.worker` must satisfy `requires-python` in pyproject.toml and match `.python-version` — a base-image bump incompatible with the project's Python policy now fails CI instead of breaking the deployment build. Dependabot ignores semver-major bumps of the `python` base image; a Python major upgrade must bump Dockerfile + `requires-python` + `.python-version` in one deliberate PR.
- Dependabot auto-rebases its PRs (`rebase-strategy: "auto"`), and every PR targeting `main` is queued for auto-merge on open/update (`.github/workflows/pr-enable-autocomplete.yaml`, no actor restriction): after the required human approval and green CI it merges by itself. Auto-merge is only a queue — the ruleset's approval/check requirements still gate the actual merge. Requires "Allow auto-merge" in the repo settings. The queue command uses `gh pr merge --auto --merge` (gh requires an explicit merge method non-interactively) and logs a warning instead of failing silently when queueing is not possible. The queue job runs with `contents: write` + `pull-requests: write` — the `enablePullRequestAutoMerge` mutation behind `--auto` is rejected with "Resource not accessible by integration" when `contents` is read-only.

The git history was squashed to a single commit when the repository was recreated for publication; earlier PR references no longer resolve.
