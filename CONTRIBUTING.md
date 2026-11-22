# Contributing / Development

> English only. Dev-facing files (this file, `AGENTS.md`, code, comments, docstrings) are English; only the `*.de.md` user docs have German mirrors. See [AGENTS.md](AGENTS.md) for the binding working rules.

User-facing documentation lives in [README.md](README.md) / [WORKFLOWS.md](WORKFLOWS.md) / [OPERATIONS.md](OPERATIONS.md) / [ARCHITECTURE.md](ARCHITECTURE.md); this file covers the development side.

## Repository structure

All workflows live as modules in the package `apps/worker/src/worker/workflows/`; the worker discovers and registers them automatically (`discover_all_workflows_in_package`). Shared logic lives in `apps/worker/src/worker/pdf_ops.py` (naming, duplex interleaving, file-connector transfer), the OCR pipeline in `apps/worker/src/worker/ocr.py`. A new workflow is a new module file plus a test file in `apps/worker/tests/`:

~~~
apps/worker/src/worker/pdf_ops.py                          # shared logic (naming, interleaving, transfer)
apps/worker/src/worker/ocr.py                              # reusable OCR pipeline (OCRmyPDF, rotate, deskew)
apps/worker/src/worker/workflows/pdf_scan_inbox_triage.py   # workflow + activities
apps/worker/src/worker/workflows/pdf_merge_duplex.py        # workflow + activities
apps/worker/src/worker/workflows/pdf_ocr.py                 # workflow + activities
apps/worker/src/worker/workflows/pdf_split.py              # workflow + activities
apps/worker/tests/test_pdf_scan_inbox_triage.py             # tests
apps/worker/tests/test_pdf_merge_duplex.py                  # tests
apps/worker/tests/test_pdf_ocr.py                           # tests
apps/worker/tests/test_pdf_split.py                        # tests
deploy/docker/worker-packages/workflows.txt                 # apt packages of the worker image
~~~

Root `pyproject.toml` is a uv workspace (`package = false`, members `apps/*`, `packages/py/*`).

## Local setup

~~~bash
uv sync --all-packages
cd apps/worker && uv run --all-packages python -m pytest tests -q   # tests
bunx nx run worker:semgrep                                          # workflow lint
~~~

A local dev worker (`mistral apps dev`) needs `MISTRAL_API_KEY` in `.env` (see `.env.example`; `.env` is gitignored).

## Quality gates (run before pushing)

CI (`.github/workflows/ci.yml`) is the gate and runs on every PR and every push to `main`; replicate it locally via `bash tools/uv.sh ...`:

| Gate | Command |
| --- | --- |
| Lock integrity | `sync --all-packages --frozen` |
| Lint | `tool run ruff check .` |
| Formatting | `tool run ruff@0.17.6 format --check .` |
| Workflow lint | `ulimit -Hs 2929688 && tool run semgrep@1.176.0 --config .agents/skills/capability-workflows/scripts/linting/rules --metrics=off apps/worker/src/worker/workflows` (ulimit cap required on Python 3.15, see AGENTS.md) |
| Type-check | `run --all-packages --frozen --with mypy==2.4.1 mypy apps/worker/src packages/py/cli/src packages/py/env/src packages/py/utils/src --strict --namespace-packages --show-error-codes` |
| Tests | worker: `run --directory apps/worker --frozen --with pytest-cov python -m pytest --cov=worker --cov-fail-under=55`, plus the `packages/py/cli` and `packages/py/utils` suites |

Rules that CI cannot enforce are in [AGENTS.md](AGENTS.md): no `type: ignore` (mypy strict), no per-line `noqa`, docstrings English (ruff `D`, google convention), every behavior change ships its test, every PR updates the docs (EN + DE mirrors) and both CHANGELOG files, branch names `<area>/<topic>`, PR titles `<area>: <summary>`, CI green before merge.

## Update process (deployment)

1. Branch from `main`, push, open a PR; merge only with green CI.
2. Restart the managed deployment with the merge commit SHA; wait for readiness and check the worker logs (all workflows registered, no errors).
3. Verify with a dry run (`apply_changes: false`) on a real document before a real run.

Details in [OPERATIONS.md](OPERATIONS.md).
