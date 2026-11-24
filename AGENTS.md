# AGENTS.md — Working rules for this repository

> Language: English. User-facing documentation has German mirrors next to the English files (`README.de.md` etc.); code, commits and these rules are English-only.

## Who this is for

Every contributor — human or AI agent — that reads or changes this repository. Read this file before pushing anything. It is the contract that CI enforces where a machine can and you enforce where it cannot.

## Non-negotiables

- **CI is the gate.** [`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every PR and on every push to `main`.
- **Never merge before the PR's CI run is green.** While this is a private solo repo, self-approving your own PR is allowed — the green-CI rule holds regardless. Merging with a red or still-running CI is a rule violation, not a style choice.
- **Never commit secrets, tokens, API keys**, and never commit personal data that the disclosure rules in `OPERATIONS.md` neutralize (real scan-folder names, health-insurance details, personal paths).
- **Never push placeholder content** (`__CONTENT__`, `TODO-fill-me`, stub bodies). A push contains final file content, always. A half-done change stays on a local branch, not on the remote.
- **Deletion is the owner's job.** Agents delete branches and files only when the repository owner explicitly asks; otherwise list what has to go and let them delete.

## Quality gates

Every change must pass (CI runs exactly these):

| Gate | Command (run via `bash tools/uv.sh ...`) |
| --- | --- |
| Lock integrity | `sync --all-packages --frozen` |
| Docker base image | `run --frozen python .github/scripts/check_docker_base.py` (Dockerfile `FROM python:X.Y` must satisfy `requires-python` and match `.python-version`) |
| Lint | `tool run ruff check .` |
| Formatting | `tool run ruff@0.17.6 format --check .` *(blocking)* |
| Workflow-specific lint | `ulimit -Hs 2929688 && tool run semgrep@1.176.0 --config .agents/skills/capability-workflows/scripts/linting/rules --metrics=off apps/worker/src/worker/workflows` (the ulimit cap is required on Python 3.15: semgrep otherwise derives a ~6 EiB stack limit from the unlimited RLIMIT_STACK and semgrep-core segfaults, semgrep#11979) |
| Type-check | `run --all-packages --frozen --with mypy==2.4.1 mypy ...src dirs... --strict ...` *(blocking)* |
| Tests | `run --directory apps/worker --frozen --with pytest-cov python -m pytest --cov=worker --cov-fail-under=55` (coverage floor 55%, to be ratcheted upward with intent), plus the `packages/py/cli` and `packages/py/utils` test suites |

Run them locally before pushing. A red local run does not become a PR.

## Typing rules

- `mypy --strict` passes with **no `# type: ignore` in repository code**. A genuinely unavoidable ignore carries the error code (`# type: ignore[code]`) and one comment line naming the reason; any such occurrence is a reviewed exception, not a habit.
- Third-party stub gaps are solved centrally in the `[tool.mypy]` overrides of the root `pyproject.toml`, never by sprinkling ignores through the code.
- Workflow start inputs are Pydantic models; LLM JSON responses are parsed and validated before they flow on (`worker/pdf_ops.py` sets the pattern).


## Lint policy

- Ruff enforces docstrings: `extend-select = ["D"]` with the pydocstyle google convention; tests are exempted and that exemption is documented in the `tool.ruff` config. Every module, class, activity and public function carries a docstring. Docstrings are English (matching the code comments), one first line naming the responsibility, behavior details below if needed; never a rephrased signature. The only German files are the `*.de.md` documentation mirrors; LLM prompts instruct the model to name and answer in the document's language.

- Ruff runs with its default rule set. Exemptions are config, not code: the central `tool.ruff` section of the root `pyproject.toml` documents every exemption with its reason. Per-line `noqa` in code is out of style for the same reason as `# type: ignore`.
- `B008` (call in argument default) is exempt repo-wide: the workflows SDK injects tool clients through `Depends(...)` defaults, the established FastAPI idiom.
- `BLE001` (blind exception catch) is exempt for workflow activities, which must not raise: opaque SDK failures flow on as structured error results. A broad catch still binds and logs or reports the exception; `S110` stays active, so nothing is swallowed silently.
- The Mistral capability scaffold under `.agents` is vendored and excluded from repo linting.
## Testing rules

- Every bug fix and every behavior change ships its test in the same PR. A deliberate exception is stated and justified in the PR description.
- Tests fake I/O (connector calls, subprocesses, HTTP); no workflow test touches Nextcloud or the internet.

## Documentation duties (doc drift is a defect)

- Any new or changed workflow behavior: update `WORKFLOWS.md` **and** `WORKFLOWS.de.md` in the same PR.
- New activities, start inputs, states or vocabulary: follow the `ARCHITECTURE.md` vocabulary from the consistency run (PR #15) and update both language versions.
- Every PR adds an entry to `CHANGELOG.md` and `CHANGELOG.de.md`, following the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format (versioned sections, `Added`/`Changed`/`Removed`/`Fixed` subsections, link definitions at the bottom).
- Operational procedures change: update `OPERATIONS.md` and `OPERATIONS.de.md`.
- English files are the source of truth; the `.de` mirror is updated in the same commit, not later.

## Branching and PRs

- One PR per concern, small; branches are cut from `main` and merged back into `main`.
- Branch names: `<area>/<topic>` (`ci/`, `docs/`, `feat/`, `fix/`, `quality/`).
- PR titles: `<area>: <summary>` (`ci:`, `docs:`, `feat:`, `fix:`, `quality:`).
- The CI quality report lands as one comment per PR (replaced on every push). If a report-only step shows findings, the next commit addresses them; do not wave them through.
