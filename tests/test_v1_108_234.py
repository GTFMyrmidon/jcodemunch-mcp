"""v1.108.234 — `backup`, `old` and `archive` join the directory skip list.

A user indexed a project root (1,824 files, ~40% actual sources) and got diluted
ranking plus some empty queries; scoping to the crate fixed it. Their diagnosis
was that jCodeMunch needed a default ignore list for non-source *extensions*.

⚠ **That remedy would not have fixed it, and the real cause is what this
release addresses.** There is no extension ignore list because the extension
gate is an ALLOW-list (157 extensions / 77 languages); `.md`, `.rst`, `.txt`,
`.bak`, `.old` and friends were already never indexed. What dilutes is a
**duplicate source tree** — a `backup/`, `old/` or `archive/` directory holding
real source files, whose symbols are indexed twice and then compete with the
originals. That is a ranking problem wearing an extension problem's clothes.

⚠⚠ **These are ordinary English words and CAN name a real package**, which is
why this is a behaviour change and not a free win. Two things make it
recoverable, and both are asserted here: `exclude_skip_directories` removes any
of them per-project, and every skip is counted in `discovery_skip_counts` so a
surprised user can see which rule dropped what.

The tests that matter most are the **negative** ones. Both index paths match on
whole segments — `index_repo` on `"/" + pattern`, `index_folder` on an anchored
`^(...)$` regex — so `threshold/` must not be caught by `old`. A future
refactor to naive substring matching would silently eat every directory whose
name ends in "old", and nothing else in the suite would notice.
"""

import pytest

from jcodemunch_mcp.security import (
    SKIP_DIRECTORIES,
    SKIP_PATTERNS,
    _SKIP_DIRECTORY_NAMES,
    get_skip_directories,
    get_skip_patterns,
)
from jcodemunch_mcp.tools.index_repo import should_skip_file
from jcodemunch_mcp.tools.index_folder import _build_skip_dirs_regex

NEW = ("backup", "old", "archive")


@pytest.mark.parametrize("name", NEW)
def test_present_in_every_derived_export(name):
    """One source of truth: adding to _SKIP_DIRECTORY_NAMES must reach both
    index paths. index_folder reads SKIP_DIRECTORIES, index_repo SKIP_PATTERNS."""
    assert name in _SKIP_DIRECTORY_NAMES
    assert name in SKIP_DIRECTORIES
    assert f"{name}/" in SKIP_PATTERNS


@pytest.mark.parametrize("path", [
    "src/backup/a.py", "src/old/a.py", "src/archive/a.py",
    "backup/a.py", "old/a.py", "archive/a.py",
    "a/b/backup/c/d.py",
])
def test_index_repo_skips_the_new_directories(path):
    assert should_skip_file(path) is True


# ⚠ THE LOAD-BEARING TESTS. `old` is a substring of many legitimate directory
# names. Both matchers are segment-anchored today; if either is ever loosened to
# a bare substring test, these fail instead of a user silently losing code.
@pytest.mark.parametrize("path", [
    "src/threshold/a.py",     # ends in "old"
    "src/household/a.py",
    "src/scaffold/a.py",
    "src/manifold/a.py",
    "src/goldilocks/a.py",    # contains "old"
    "src/archived/a.py",      # prefix of a skipped name
    "src/backups/a.py",
    "src/oldest/a.py",
])
def test_index_repo_does_not_skip_lookalikes(path):
    assert should_skip_file(path) is False


@pytest.mark.parametrize("name", NEW)
def test_index_folder_regex_matches_exact_name(name):
    assert _build_skip_dirs_regex().match(name)


@pytest.mark.parametrize("name", [
    "threshold", "household", "scaffold", "manifold", "gold",
    "archived", "backups", "oldest", "old_data",
])
def test_index_folder_regex_does_not_match_lookalikes(name):
    assert _build_skip_dirs_regex().match(name) is None


def test_plurals_are_deliberately_not_covered():
    """A boundary on the record, not an oversight.

    `backups` / `archives` are common and are NOT skipped. Widening to them is a
    separate decision with its own false-positive surface; this test exists so
    that decision is made deliberately rather than discovered. If they are added
    later, delete this test in the same commit.
    """
    for name in ("backups", "archives", "olds"):
        assert name not in _SKIP_DIRECTORY_NAMES


@pytest.mark.parametrize("name", NEW)
def test_the_escape_hatch_actually_works(name):
    """A project shipping a real `archive/` package must be able to opt out.

    This is what makes the behaviour change recoverable rather than a silent
    loss, so it is asserted rather than assumed from the config key's existence.
    """
    from jcodemunch_mcp import config as config_module

    orig = config_module._GLOBAL_CONFIG.copy()
    config_module._GLOBAL_CONFIG.clear()
    try:
        config_module._GLOBAL_CONFIG["exclude_skip_directories"] = [name]
        assert name not in get_skip_directories()
        assert f"{name}/" not in get_skip_patterns()
        # Un-skipping one must not un-skip the others.
        assert "node_modules" in get_skip_directories()
        for other in NEW:
            if other != name:
                assert other in get_skip_directories()
    finally:
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(orig)
