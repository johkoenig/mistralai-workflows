"""Advisory AI review of a pull request via the Mistral chat completions API.

Runs in the "AI review (Mistral)" workflow on pull_request events. The PR diff
(capped) plus the repository's AGENTS.md from the base branch are sent to
Mistral; the answer is posted as exactly one marker comment per PR (updated on
re-runs), so reviews never stack. The review is advisory only: the script never
submits an approving review and never fails the workflow run. Like
quality_report.py it uses only the standard library, so the runner's stock
python3 suffices. The workflow checks out the BASE commit, so code from the PR
itself is never executed.
"""

from __future__ import annotations

import base64
import json
import os
import pathlib
import urllib.error
import urllib.request

MARKER = "<!--ai-review-->"
MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"
DEFAULT_MODEL = "mistral-large-latest"
DEFAULT_DIFF_LIMIT = 120_000
TRUSTED_AUTHORS = {"OWNER", "MEMBER", "COLLABORATOR"}

SYSTEM_PROMPT = (
    "You are a strict but fair code reviewer for this repository. Review the "
    "pull request diff below against the repository rules in AGENTS.md. Answer "
    "with markdown only: a one-line verdict (LGTM / minor issues / needs "
    "changes), then findings as bullets with `file:line` references, most "
    "severe first. Cover correctness, tests, security (including prompt "
    "injection and secret handling) and documentation drift. If the diff is "
    "truncated, say so and review only what is visible. No emoji, no filler."
)


def github_headers(
    token: str, accept: str = "application/vnd.github+json"
) -> dict[str, str]:
    """Standard GitHub API headers for the authenticated GITHUB_TOKEN."""
    return {
        "Authorization": f"Bearer {token}",
        "Accept": accept,
        "Content-Type": "application/json",
    }


def github_get(url: str, headers: dict[str, str]) -> bytes:
    """GET a GitHub API URL and return the raw response body."""
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request) as response:
        return response.read()


def github_send(
    url: str, headers: dict[str, str], method: str, payload: dict[str, object]
) -> bytes:
    """Send a POST/PATCH request with a JSON body and return the response body."""
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers, method=method
    )
    with urllib.request.urlopen(request) as response:
        return response.read()


def fetch_diff(repository: str, pr_number: int, token: str, limit: int) -> str:
    """The PR diff via the GitHub API (never executes PR code), truncated to limit."""
    headers = github_headers(token, accept="application/vnd.github.v3.diff")
    url = f"https://api.github.com/repos/{repository}/pulls/{pr_number}"
    diff = github_get(url, headers).decode("utf-8", errors="replace")
    if len(diff) > limit:
        diff = diff[:limit] + "\n\n[... diff truncated ...]"
    return diff


def fetch_agents_md(repository: str, base_ref: str, token: str) -> str:
    """AGENTS.md from the base branch (empty when the file does not exist)."""
    headers = github_headers(token)
    url = f"https://api.github.com/repos/{repository}/contents/AGENTS.md?ref={base_ref}"
    try:
        data = json.loads(github_get(url, headers))
    except (urllib.error.HTTPError, json.JSONDecodeError):
        return ""
    content = data.get("content")
    if not isinstance(content, str):
        return ""
    return base64.b64decode(content).decode("utf-8", errors="replace")


def mistral_review(model: str, api_key: str, user_content: str) -> str:
    """One chat completion against the Mistral API; API errors become text."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.2,
        "max_tokens": 2048,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        data = json.loads(github_send(MISTRAL_URL, headers, "POST", payload))
    except (
        urllib.error.HTTPError,
        urllib.error.URLError,
        json.JSONDecodeError,
    ) as error:
        return f"_AI review could not be completed: {error!r}_"
    choices = data.get("choices") or []
    if not choices:
        return "_AI review returned no choices_"
    message = choices[0].get("message") or {}
    content = message.get("content")
    return content if isinstance(content, str) and content.strip() else "_empty reply_"


def upsert_comment(repository: str, pr_number: int, body: str, token: str) -> None:
    """Create or update the marker PR comment; print to the log when unconfigured."""
    headers = github_headers(token)
    comments_url = (
        f"https://api.github.com/repos/{repository}/issues/{pr_number}/comments"
        "?per_page=100"
    )
    existing_id: int | None = None
    for comment in json.loads(github_get(comments_url, headers)):
        if MARKER in (comment.get("body") or ""):
            existing_id = comment["id"]
            break
    if existing_id:
        github_send(
            f"https://api.github.com/repos/{repository}/issues/comments/{existing_id}",
            headers,
            "PATCH",
            {"body": body},
        )
    else:
        github_send(comments_url, headers, "POST", {"body": body})
    print("AI review published to the PR")


def main() -> None:
    """Review the PR from the event payload; skipping or errors never fail the run."""
    try:
        event_path = os.environ.get("GITHUB_EVENT_PATH", "")
        event = (
            json.loads(pathlib.Path(event_path).read_text(encoding="utf-8"))
            if event_path
            else {}
        )
        pr = event.get("pull_request") or {}
        pr_number = int(pr.get("number") or 0)
        repository = os.environ.get("GITHUB_REPOSITORY", "")
        token = os.environ.get("GITHUB_TOKEN", "")
        api_key = os.environ.get("MISTRAL_API_KEY", "")
        model = os.environ.get("AI_REVIEW_MODEL", DEFAULT_MODEL)
        diff_limit = int(os.environ.get("AI_REVIEW_DIFF_LIMIT", DEFAULT_DIFF_LIMIT))

        if not pr_number or not repository or not token:
            print("SKIP: not a pull_request event or missing GITHUB_REPOSITORY/TOKEN")
            return
        if pr.get("draft"):
            print("SKIP: draft PR")
            return
        if pr.get("author_association") not in TRUSTED_AUTHORS:
            print(
                f"SKIP: author_association {pr.get('author_association')!r} untrusted"
            )
            return
        if not api_key:
            print("SKIP: MISTRAL_API_KEY secret not set")
            return

        diff = fetch_diff(repository, pr_number, token, diff_limit)
        agents_md = fetch_agents_md(
            repository, pr.get("base", {}).get("ref", "main"), token
        )
        user_content = (
            f"PR #{pr_number}: {pr.get('title') or ''}\n\n{pr.get('body') or ''}\n\n"
            f"--- AGENTS.md (base branch) ---\n{agents_md}\n\n"
            f"--- diff ---\n{diff}"
        )
        review = mistral_review(model, api_key, user_content)
        body = (
            f"{MARKER}\n## AI review ({model})\n\n{review}\n\n"
            "_Advisory only - posted by `.github/workflows/ai-review.yml`; "
            "does not count as an approval._"
        )
        upsert_comment(repository, pr_number, body, token)
    except Exception as error:  # advisory review must never turn the run red
        print(f"AI review skipped after error: {error!r}")


if __name__ == "__main__":
    main()
