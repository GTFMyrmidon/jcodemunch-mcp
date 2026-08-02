"""`CLAUDE.md`'s `Current State` block names the three newest releases, in order.

That block is three long bullets maintained by hand, and every release rewrites
the top one and demotes the rest. The edit is string surgery on prose, so it
fails QUIETLY: a clobbered entry, a stale `Prior (...)` label, or an `Older
releases (X and earlier)` boundary that no longer lines up all read fine and are
all wrong. On 2026-08-02 a single release produced two of those three in one
sitting -- `1.108.212` was overwritten rather than demoted, and the boundary line
was left naming a version still in rotation.

Nothing downstream breaks when this drifts, which is exactly the problem: the
file is loaded into every session under this directory and is read as current
state. A wrong version history here misinforms the reader silently and forever.

Same argument as `test_lockfile_version_sync.py`: a convention that has already
failed twice needs a gate, not another checklist line.

Failure here means: fix the `Current State` bullets at the top of CLAUDE.md so
they name the three newest CHANGELOG releases, newest first, with the `Older
releases (X and earlier)` line naming the fourth.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROTATION_SIZE = 3  # CLAUDE.md maintenance practice: keep the 3 newest only


def _pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match, "no version found in pyproject.toml"
    return match.group(1)


def _changelog_versions() -> list[str]:
    """Released versions, newest first. `## [Unreleased]` is skipped by the
    pattern -- it carries no version and must never enter the rotation."""
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    return re.findall(r"^## \[(\d+\.\d+\.\d+)\]", text, re.MULTILINE)


def _claude_md() -> str:
    return (ROOT / "CLAUDE.md").read_text(encoding="utf-8")


def _rotation_versions() -> list[str]:
    """The versions CLAUDE.md's Current State claims, newest first."""
    text = _claude_md()
    current = re.search(r"^- \*\*Version:\*\*\s*(\d+\.\d+\.\d+)", text, re.MULTILINE)
    assert current, "no `- **Version:** X.Y.Z` line in CLAUDE.md"
    priors = re.findall(r"^- \*\*Prior \((\d+\.\d+\.\d+)\)", text, re.MULTILINE)
    return [current.group(1)] + priors


def _older_boundary() -> str:
    match = re.search(
        r"^- \*\*Older releases \((\d+\.\d+\.\d+) and earlier\)", _claude_md(), re.MULTILINE
    )
    assert match, "no `- **Older releases (X and earlier):**` line in CLAUDE.md"
    return match.group(1)


def test_current_state_names_the_shipping_version():
    rotation = _rotation_versions()
    pyproject = _pyproject_version()
    assert rotation[0] == pyproject, (
        f"CLAUDE.md leads with {rotation[0]} but pyproject.toml says {pyproject}. "
        f"The version bump did not reach the Current State block."
    )


def test_rotation_holds_exactly_the_three_newest_releases():
    rotation = _rotation_versions()
    expected = _changelog_versions()[:ROTATION_SIZE]
    assert rotation == expected, (
        f"CLAUDE.md Current State lists {rotation}, but the {ROTATION_SIZE} newest "
        f"CHANGELOG releases are {expected}. An entry was clobbered, demoted out of "
        f"order, or left behind."
    )


def test_rotation_has_no_duplicate_or_missing_entries():
    """A clobbered entry shows up as a short rotation, a duplicated label as a
    repeat. Both are silent on their own."""
    rotation = _rotation_versions()
    assert len(rotation) == ROTATION_SIZE, (
        f"expected {ROTATION_SIZE} entries (1 Version + {ROTATION_SIZE - 1} Prior), "
        f"found {len(rotation)}: {rotation}"
    )
    assert len(set(rotation)) == len(rotation), f"duplicate versions in rotation: {rotation}"


def test_older_releases_boundary_starts_below_the_rotation():
    """The boundary must name the FOURTH-newest release. Naming one still in
    rotation claims it is archived while it is quoted in full above."""
    changelog = _changelog_versions()
    rotation = _rotation_versions()
    boundary = _older_boundary()
    assert boundary not in rotation, (
        f"`Older releases ({boundary} and earlier)` names a version still in the "
        f"Current State rotation {rotation}."
    )
    if len(changelog) > ROTATION_SIZE:
        expected = changelog[ROTATION_SIZE]
        assert boundary == expected, (
            f"boundary says {boundary}; the release below the rotation is {expected}."
        )
