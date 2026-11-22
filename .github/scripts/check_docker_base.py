"""Gate: the worker Docker image's Python base must match the project's Python policy.

CI runs on a runner whose interpreter comes from `.python-version`, so a base-image
bump in `deploy/docker/Dockerfile.worker` is otherwise invisible until the managed
deployment tries to build the image (the image pins `UV_NO_PYTHON_DOWNLOADS=1` and
`only-system`, so the base image's interpreter IS the runtime). This check fails the
CI run when any `FROM python:X.Y...` base violates `requires-python` in
pyproject.toml or drifts from `.python-version`. Stdlib only, like quality_report.py.
"""

from __future__ import annotations

import pathlib
import re
import sys
import tomllib

DOCKERFILE = pathlib.Path("deploy/docker/Dockerfile.worker")
PYTHON_VERSION_FILE = pathlib.Path(".python-version")
PYPROJECT = pathlib.Path("pyproject.toml")

FROM_PYTHON_RE = re.compile(r"^FROM\s+python:(\d+)\.(\d+)(?:[-\w.]*)?\s", re.MULTILINE)
CLAUSE_RE = re.compile(r"(>=|<=|==|!=|>|<)\s*(\d+(?:\.\d+)*)")


def parse_version(text: str) -> tuple[int, ...]:
    """'3.14' -> (3, 14); trailing zeros kept for tuple comparison."""
    return tuple(int(part) for part in text.strip().split("."))


def satisfies(version: tuple[int, ...], specifier: str) -> bool:
    """Evaluate a simple requires-python specifier ('>=3.12,<3.15') against a version."""
    padded = version + (0,) * (3 - len(version))
    for clause in specifier.split(","):
        match = CLAUSE_RE.fullmatch(clause.strip())
        if not match:
            raise ValueError(f"unsupported specifier clause: {clause!r}")
        operator, bound = match.group(1), parse_version(match.group(2))
        bound = bound + (0,) * (3 - len(bound))
        ok = {
            ">=": padded >= bound,
            "<=": padded <= bound,
            ">": padded > bound,
            "<": padded < bound,
            "==": padded == bound,
            "!=": padded != bound,
        }[operator]
        if not ok:
            return False
    return True


def docker_python_bases() -> list[tuple[int, int]]:
    """All (major, minor) of `FROM python:X.Y...` stages in the worker Dockerfile."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    return [(int(major), int(minor)) for major, minor in FROM_PYTHON_RE.findall(text)]


def main() -> int:
    """Check Dockerfile base, .python-version and requires-python for consistency."""
    requires_python = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"][
        "requires-python"
    ]
    bases = docker_python_bases()
    if not bases:
        print(f"FAIL: no 'FROM python:X.Y' base found in {DOCKERFILE}")
        return 1
    runner = parse_version(PYTHON_VERSION_FILE.read_text(encoding="utf-8"))
    problems: list[str] = []
    for base in bases:
        version = base + (0,)
        if not satisfies(version, requires_python):
            problems.append(
                f"Dockerfile base python:{base[0]}.{base[1]} violates "
                f"requires-python = {requires_python!r}"
            )
        if base != runner[:2]:
            problems.append(
                f"Dockerfile base python:{base[0]}.{base[1]} drifts from "
                f".python-version {runner[0]}.{runner[1]} (CI runner vs image)"
            )
    if not satisfies(runner, requires_python):
        problems.append(
            f".python-version {runner[0]}.{runner[1]} violates "
            f"requires-python = {requires_python!r}"
        )
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}")
        return 1
    print(
        f"OK: Dockerfile bases {[f'python:{b[0]}.{b[1]}' for b in bases]}, "
        f".python-version {runner[0]}.{runner[1]}, "
        f"requires-python {requires_python!r} - all consistent"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
