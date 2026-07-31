"""`uv.lock` records the project's own version; CI runs `uv sync --locked`.

A version bump that does not touch the lock takes every CI job red at
`Install dependencies`, before a single test runs, for a reason that has nothing
to do with what the release contains. It has happened twice (v1.108.200,
v1.108.203), the second time with the trap already written down in full, which
is the argument for a gate rather than another checklist line.

Failure here means: edit the one `version = "..."` line under
`name = "jcodemunch-mcp"` in `uv.lock`. Do not run `uv lock` unless a dependency
actually changed -- an older local uv rewrites the whole file.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECT_NAME = "jcodemunch-mcp"


def _pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match, "no version found in pyproject.toml"
    return match.group(1)


def _lock_project_version() -> str:
    text = (ROOT / "uv.lock").read_text(encoding="utf-8")
    # The project's own entry is the one with an editable source; every other
    # package resolves to a registry, so this cannot match a dependency that
    # happens to share the name.
    match = re.search(
        r'name\s*=\s*"' + re.escape(PROJECT_NAME) + r'"\s*\n'
        r'version\s*=\s*"([^"]+)"\s*\n'
        r'source\s*=\s*\{\s*editable',
        text,
    )
    assert match, f"no editable {PROJECT_NAME} entry found in uv.lock"
    return match.group(1)


def test_lockfile_records_the_current_project_version():
    pyproject = _pyproject_version()
    lock = _lock_project_version()
    assert lock == pyproject, (
        f"uv.lock pins {PROJECT_NAME} at {lock} but pyproject.toml says {pyproject}. "
        f"CI's `uv sync --locked` will fail before any test runs. Edit the one "
        f"version line in uv.lock; only run `uv lock` if a dependency changed."
    )
