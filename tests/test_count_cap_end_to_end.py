"""The file-COUNT cap, tested where the USER stands.

Sibling of `test_size_cap_end_to_end.py`, and it exists for the same reason that
one does. `max_file_size` took three releases to fix because every fix proved a
LAYER worked and nobody asked the user's question. `max_folder_files` then broke
in a fourth way that none of those layers could see (#416, @domis86):

    `index_folder` resolved the cap from GLOBAL config only and handed the
    result to the walk as an explicit `max_files=` argument. The walk's own
    repo-aware resolution treats a non-None argument as a caller override, so
    the correct value was computed and then discarded on every entry point.

`config --check` reported `max_folder_files 15000 [project]` the whole time,
because the config layer was never the broken part. Only an end-to-end index
could see it.

So, as in the sibling file: the matrix is (every documented route) x (every
discovery entry point), and the assertion is about what got indexed, not about
which resolver was called with what.

⚠ The cap is set DOWNWARD here, not upward as the reporter set it. Proving a
raised cap end to end would need a project of 2,001 files, and a lowered cap
falsifies the same statement — "the project file is not consulted" — for a
fraction of the runtime. `test_the_default_indexes_everything_under_it` is the
control that keeps a lowered cap from passing because caps stopped working.
"""
from __future__ import annotations

import pytest

from jcodemunch_mcp import config as config_module
from jcodemunch_mcp.tools.index_folder import index_folder

FILE_COUNT = 6
CAP = 2

MODULES = [f"mod_{i}.py" for i in range(FILE_COUNT)]


def _make_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    for i, name in enumerate(MODULES):
        (project / name).write_text(
            f"def symbol_{i}():\n    return {i}\n", encoding="utf-8"
        )
    return project


def _index(project, tmp_path, paths=None):
    return index_folder(
        str(project),
        use_ai_summaries=False,
        storage_path=str(tmp_path / "store"),
        incremental=False,
        context_providers=False,
        paths=paths,
    )


ROUTES = ["env_var", "global_config", "project_config"]


@pytest.fixture
def route(request, monkeypatch, tmp_path):
    """Set the cap the way the docs say to, then undo it."""
    which = request.param
    project = _make_project(tmp_path)

    if which == "env_var":
        # Read at load_config() time, so a bare os.environ poke without a reload
        # proves nothing. This is also the reporter's working WORKAROUND, which
        # makes it the one route that must not regress while fixing the others.
        monkeypatch.setenv("JCODEMUNCH_MAX_FOLDER_FILES", str(CAP))
        config_module.load_config()
    elif which == "global_config":
        monkeypatch.setitem(config_module._GLOBAL_CONFIG, "max_folder_files", CAP)
    elif which == "project_config":
        # The route in the reporter's issue, and the only one that was broken.
        (project / ".jcodemunch.jsonc").write_text(
            '{"max_folder_files": %d}' % CAP, encoding="utf-8"
        )
    else:  # pragma: no cover
        raise AssertionError(f"unknown route {which}")

    yield project

    config_module.invalidate_project_config_cache(str(project))
    if which == "env_var":
        monkeypatch.delenv("JCODEMUNCH_MAX_FOLDER_FILES", raising=False)
        config_module.load_config()


@pytest.mark.parametrize("route", ROUTES, indirect=True)
@pytest.mark.parametrize(
    "paths", [None, MODULES], ids=["full-walk", "explicit-paths"]
)
def test_the_cap_a_user_set_is_the_cap_the_walk_uses(route, tmp_path, paths):
    """I set max_folder_files by a documented route. Did the index obey it?"""
    result = _index(route, tmp_path, paths=paths)

    assert result["success"] is True, result
    assert result["file_count"] == CAP, (
        f"the cap was set to {CAP} by a documented route but "
        f"{result.get('file_count')} of {FILE_COUNT} files were indexed; the "
        "route is being resolved and then discarded"
    )


@pytest.mark.parametrize("route", ROUTES, indirect=True)
def test_a_truncated_index_says_so(route, tmp_path):
    """The cap holding is half of it; a silently short index is the other half.

    #416's reporter could only tell the cap had bitten by counting files. The
    truncation block (#366) is what makes it legible, and it is computed from
    the same `max_files` the fix corrected, so a route that misresolves the cap
    also misreports the truncation.
    """
    result = _index(route, tmp_path)

    assert result.get("truncated") is True, result
    assert result.get("files_indexed") == CAP, result
    assert result.get("files_discovered") == FILE_COUNT, result
    assert result.get("files_skipped_cap") == FILE_COUNT - CAP, result


def test_the_default_indexes_everything_under_it(tmp_path):
    """Non-vacuity. Without this, every assertion above would still pass if the
    cap were being ignored in the other direction."""
    project = _make_project(tmp_path)
    result = _index(project, tmp_path)

    assert result["success"] is True, result
    assert result["file_count"] == FILE_COUNT, result
    assert not result.get("truncated"), result
