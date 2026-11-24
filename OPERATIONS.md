> Language: English · [Deutsche Version](OPERATIONS.de.md)

# Operations

Deployment and operating rules for `mistralai-workflows`. Workflow usage: [README.md](README.md) and [WORKFLOWS.md](WORKFLOWS.md); architecture: [ARCHITECTURE.md](ARCHITECTURE.md).

## Deployment

One repository, one managed deployment. The concrete parameters of the `mistralai-workflows` deployment:

| Parameter | Value |
|---|---|
| Deployment name | `mistralai-workflows` |
| GitHub repository | `https://github.com/johkoenig/mistralai-workflows` |
| Build context | `.` |
| Dockerfile | `deploy/docker/Dockerfile.worker` |
| Hardened | `true` (mandatory, because all workflows use `on_behalf_of=True`) |
| Secrets | `scan_inbox_folder` bound as environment variable `SCAN_INBOX_FOLDER`; `file_connector_names` bound as `FILE_CONNECTOR_NAMES` (comma-separated connector names, e.g. `<your-connector-name>`; default `nextcloud`) |

The worker image system-installs `ghostscript`, `tesseract-ocr-all`, and `jbig2dec` (package list: `deploy/docker/worker-packages/workflows.txt`).

Discovery after every start: the worker automatically registers all workflow modules from `apps/worker/src/worker/workflows/` (recursively, including `workflows/steps/`) via `discover_all_workflows_in_package`. In normal operation, the four workflows `pdf-scanner-processor`, `pdf-ocr`, `pdf-alternating-merge`, `pdf-split` and all activities from [WORKFLOWS.md](WORKFLOWS.md) must appear in the logs, without OOM or import errors.

## Quality gates (CI)

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) is the single gate. It runs on every PR and on every push to `main`, and every check is **blocking**: `uv sync --all-packages --frozen`, ruff lint, ruff format, semgrep (capability workflow rules), strict mypy, the worker/cli/utils test suites, and a worker-coverage floor of **55%** (`--cov-fail-under=55`; current level 58%, to be ratcheted upward deliberately). Tool versions are pinned in the workflow (ruff 0.17.6, mypy 2.4.1, semgrep 1.176.0) so runs are reproducible. The outcome of every step lands as one upserted PR comment (marker `<!--ci-quality-report-->`) and, on main, in the job summary.

## Update process

1. Create a branch from `main`, push the changes (code with tests, keep the docs up to date).
2. Create a pull request; the CI gate must be green before the merge - also for self-approved PRs (AGENTS.md). Merge into `main`.
3. Restart the deployment with the merge commit SHA (parameters as above).
4. Wait for readiness, then check the worker logs: all activities registered, no errors.
5. Verification run: recommended a dry run (`apply_changes: false`) on a real document; only then a real run.

## Logs

- Runtime logs in the Studio (Deployment → Logs) or via MCP `get_workflow_deployment_logs`.
- `worker/ocr.py` logs start, end, duration, and errors of every OCR run; progress of long runs is visible there.
- Heartbeats (every 30 s on OCR/download/upload) detect worker crashes within 2 minutes and trigger activity retries (2 attempts per activity).

## Operating conventions

- **Dry run first**: always check new or changed workflows with `apply_changes: false` against a real document first.
- **Manual starts only**: no schedule, no webhook; rationale in ARCHITECTURE.md. Heavy processing only after a completed scan job.
- **Originals are kept**: every workflow secures originals by default under `<folder>/.originals/<workflow>/` (split: `<stem>/` subfolder, alternating merge: `<target name>/` subfolder, OCR and pdf-scanner-processor: the file itself). Clean up by hand once the results are verified; the workflows never delete.

## Naming rules (AGENTS.md)

Folder-specific naming rules live as `AGENTS.md` in the respective folder or a parent folder — the nearest one wins (e.g. an AGENTS.md in the scanner inbox or above it). Every AI-naming workflow (`pdf-scanner-processor`, `pdf-alternating-merge` without an explicit `title`, `pdf-split`) loads them via the `load-naming-rules` activity (max. 4000 characters) and passes them to the model with priority over the base prompt. Without an AGENTS.md the LLM picks suitable names itself. The project defines no naming schema of its own, and AGENTS.md content is private — it is never reflected in this repository. Changes to AGENTS.md take effect from the next run, without a redeploy.

## Known limitations

- **No moves between team stores**: the Nextcloud connector supports `files_move` only within one file area. Files that need to be sorted from the scanner inbox into another area are copied via `files_copy`; the originals in the scanner inbox have to be deleted by hand afterwards. A later step (an extension of pdf-scanner-processor) can move the sorting into the worker directly.
- **No deletion via the connector**: `files_delete` does not exist. Whatever must be removed for good (duplicate parts, consumed originals) is cleaned up by the user by hand.
- **OCR limits**: `ocrmypdf -j 1` per file and `MAX_CONCURRENT_OCR = 2` in parallel (memory protection of the managed instance); at most 200 files per pdf-ocr run. Large batches are better processed locally.
- **OCR on handwritten content** (e.g. handwritten numbers on receipts) is unreliable; folder-specific AGENTS.md rules can catch such cases.
