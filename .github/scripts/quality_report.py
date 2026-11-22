"""Publish the CI quality report of this workflow run.

On a pull request: creates or updates exactly one PR comment carrying the marker
`<!--ci-quality-report-->` (GITHUB_TOKEN, `pull-requests: write`), so the report of the
newest commit replaces the previous one instead of stacking comments. On a push to main: appends the
same report to the job step summary. The log files under `.ci-reports/` are written by the CI
steps; the step outcomes arrive as environment variables. The GitHub API is reached with `urllib`
so the script needs only the runner's stock python3. Publishing must never be the reason a run
looks red: any problem while publishing is printed to the job log and swallowed.
"""

from __future__ import annotations

import json
import os
import pathlib
import urllib.request

MARKER = "<!--ci-quality-report-->"
LOG_DIR = pathlib.Path(".ci-reports")
MAX_LOG_LINES = 2000

# (outcome env var, section title, log file name)
SECTIONS = [
    ("APT", "system tools for OCRmyPDF", "apt.log"),
    ("SYNC", "uv sync from the committed lock", "sync.log"),
    ("RUFF", "ruff lint (gate)", "ruff.log"),
    ("RUFF_FIXDIFF", "ruff autofix diff (onboarding helper)", "ruff-fixdiff.log"),
    ("RUFF_FORMAT", "ruff formatting (gate)", "ruff-format.log"),
    ("SEMGREP", "semgrep workflow rules (gate)", "semgrep.log"),
    ("MYPY", "mypy strict (gate)", "mypy.log"),
    ("WORKER_TESTS", "worker tests + coverage, floor 55% (gate)", "worker-tests.log"),
    ("CLI_TESTS", "cli tests (gate)", "cli-tests.log"),
    ("UTILS_TESTS", "utils tests (gate)", "utils-tests.log"),
]

ICONS = {"success": "✅", "failure": "❌", "skipped": "⏭️"}
STATUSES = {"success": "passed", "failure": "FAILED", "skipped": "skipped"}


def read_tail(name: str) -> str:
    """The last lines of a step log, in a fenced block, ready to embed."""
    path = LOG_DIR / name
    if not path.is_file():
        return "_no output recorded_"
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) > MAX_LOG_LINES:
        lines = [
            f"_... {len(lines) - MAX_LOG_LINES} earlier lines cut ..._",
            *lines[-MAX_LOG_LINES:],
        ]
    if not lines:
        return "_empty_"
    return "```\n" + "\n".join(lines) + "\n```"


def versions() -> str:
    """The tool versions line for the report, empty when versions.log is absent."""
    path = LOG_DIR / "versions.log"
    if not path.is_file():
        return ""
    parts = [
        line.strip()
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip()
    ]
    return "\n**Tools:** " + " · ".join(parts) + "\n" if parts else ""


def build_body() -> str:
    """One markdown report: the outcome of every gate, then each log in a foldable section."""
    env = os.environ
    lines = [MARKER, "## CI quality report", ""]
    failed: list[str] = []
    for key, title, _log in SECTIONS:
        outcome = env.get(key)
        lines.append(
            f"{ICONS.get(outcome, '❓')} **{title}** - {STATUSES.get(outcome, 'not reached')}"
        )
        if outcome == "failure":
            failed.append(title)
    if failed:
        lines += ["", "⚠️ **Gates failed. Do not merge.**"]
    lines += ["", versions()]
    for _key, title, log in SECTIONS:
        lines += [
            f"<details><summary>{title}</summary>",
            "",
            read_tail(log),
            "",
            "</details>",
            "",
        ]
    return "\n".join(lines)


def api_headers(token: str) -> dict[str, str]:
    """Standard GitHub API headers for the authenticated GITHUB_TOKEN."""
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
    }


def upsert_comment(repository: str, pr_number: int, body: str) -> None:
    """Create or update the marker PR comment on GitHub; print instead when unconfigured."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if not repository or not token:
        print("REPOSITORY or GITHUB_TOKEN missing; report printed to the log instead")
        print(body)
        return
    headers = api_headers(token)
    comments_url = f"https://api.github.com/repos/{repository}/issues/{pr_number}/comments?per_page=100"
    existing_id: int | None = None
    with urllib.request.urlopen(
        urllib.request.Request(comments_url, headers=headers)
    ) as response:
        for comment in json.load(response):
            if MARKER in (comment.get("body") or ""):
                existing_id = comment["id"]
                break
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/issues/comments/{existing_id}"
        if existing_id
        else comments_url,
        data=json.dumps({"body": body}).encode("utf-8"),
        headers=headers,
        method="PATCH" if existing_id else "POST",
    )
    with urllib.request.urlopen(request) as response:
        response.read()
    print("Quality report published to the PR")


def main() -> None:
    """Publish the report to the PR or the job summary; publishing never fails the run."""
    body = build_body()
    pr_number = os.environ.get("PR_NUMBER", "")
    try:
        if pr_number.isdigit():
            upsert_comment(os.environ.get("REPOSITORY", ""), int(pr_number), body)
            return
        summary = os.environ.get("GITHUB_STEP_SUMMARY", "")
        if summary and pathlib.Path(summary).parent.is_dir():
            with open(summary, "a", encoding="utf-8") as handle:
                handle.write(body + "\n")
        else:
            print(body)
    except (
        Exception
    ) as error:  # publishing is best-effort; the report goes to the job log
        print(f"Quality report could not be published ({error!r}); report follows:")
        print(body)


if __name__ == "__main__":
    main()
