"""Uncommitted work is represented, per scope (v1.108.181).

#377 hardening item 5. Git HEAD can sit perfectly still while the working tree
holds a modified file, a brand-new untracked implementation, a deletion, or a
rename into or out of scope. A zero-result scan has no returned file to hang
per-file freshness on, so the SCOPE needs a state of its own:

    clean / dirty_in_scope / dirty_outside_scope / unknown / not_applicable

Two restrictions keep it from becoming noise, and both are load-bearing:

  * work OUTSIDE the scanned scope does not invalidate a narrow proof, which is
    the reviewer's own distinction;
  * an edit the index has ALREADY re-read is not a gap, so a watcher-fresh
    corpus does not refuse every developer with unsaved work.
"""

from __future__ import annotations

import json

import pytest

from jcodemunch_mcp.handoff import absence_refusal, clear_absences, note_absence
from jcodemunch_mcp.retrieval import subject_state as subj
from jcodemunch_mcp.retrieval.verdict import build_verdict


class _Idx:
    def __init__(self, root=None, mtimes=None):
        self.source_root = str(root) if root else None
        self.file_mtimes = mtimes or {}
        self.indexed_at = "2026-07-26T00:00:00"
        self.git_head = "a" * 40


# --- scope membership --------------------------------------------------------

def test_no_scope_means_the_whole_repository():
    assert subj._in_scope("src/a.py", None) is True


def test_a_glob_matches_the_path_and_the_basename():
    assert subj._in_scope("src/deep/a.py", "*.py") is True
    assert subj._in_scope("src/deep/a.py", "src/deep/*.py") is True
    assert subj._in_scope("src/deep/a.py", "tests/*.py") is False


# --- porcelain parsing -------------------------------------------------------

def test_a_rename_reports_the_path_that_now_exists():
    """The new path is what a scan would have had to see."""
    assert subj._parse_porcelain(b"R  old/a.py -> new/b.py\n") == ("new/b.py",)


def test_untracked_and_modified_and_deleted_all_count():
    raw = b"?? new.py\n M mod.py\n D gone.py\n"
    assert subj._parse_porcelain(raw) == ("new.py", "mod.py", "gone.py")


def test_a_quoted_path_is_unquoted_so_it_compares_against_index_paths():
    assert subj._parse_porcelain(b'?? "src/has space.py"\n') == ("src/has space.py",)


def test_backslashes_are_normalised():
    assert subj._parse_porcelain(b"?? src\\win.py\n") == ("src/win.py",)


# --- the five states ---------------------------------------------------------

def _tree_state_v181(monkeypatch, dirty, *, scope=None, mtimes=None, root="/repo", freshness=None):
    monkeypatch.setattr(
        subj, "_working_tree_reading", lambda r: ("fp", tuple(dirty)) if dirty is not None else None
    )
    return subj.working_tree_state(
        _Idx(root=root, mtimes=mtimes), scope=scope, freshness=freshness
    )


def test_a_subject_without_revision_control_is_not_applicable(monkeypatch):
    s = _tree_state_v181(monkeypatch, ["a.py"], freshness="not_tracked")
    assert s == {"state": "not_applicable", "blocks": False}


def test_an_unreadable_tree_is_unknown(monkeypatch):
    assert _tree_state_v181(monkeypatch, None)["state"] == "unknown"


def test_nothing_uncommitted_is_clean(monkeypatch):
    s = _tree_state_v181(monkeypatch, [])
    assert s["state"] == "clean" and s["blocks"] is False


def test_work_outside_the_scope_does_not_invalidate_a_narrow_proof(monkeypatch):
    s = _tree_state_v181(monkeypatch, ["docs/x.md"], scope="src/*.py")
    assert s["state"] == "dirty_outside_scope"
    assert s["blocks"] is False
    assert s["files_dirty"] == 1


def test_work_inside_the_scope_that_the_index_never_saw_blocks(monkeypatch):
    s = _tree_state_v181(monkeypatch, ["src/new.py"], scope="src/*.py", mtimes={})
    assert s["state"] == "dirty_in_scope"
    assert s["files_not_in_index"] == 1
    assert s["blocks"] is True
    assert s["sample"] == ["src/new.py"]


def test_an_edit_the_index_has_already_read_does_not_block(tmp_path, monkeypatch):
    """A watcher-fresh corpus holds the edit. Refusing here would fire on every
    developer with unsaved work and teach people to ignore the signal."""
    f = tmp_path / "mod.py"
    f.write_text("x")
    mtimes = {"mod.py": f.stat().st_mtime_ns + 1_000_000_000}  # indexed after the edit
    s = _tree_state_v181(monkeypatch, ["mod.py"], mtimes=mtimes, root=tmp_path)
    assert s["state"] == "dirty_in_scope"
    assert s["files_not_in_index"] == 0
    assert s["blocks"] is False
    assert "sample" not in s


def test_a_file_newer_on_disk_than_in_the_index_blocks(tmp_path, monkeypatch):
    f = tmp_path / "mod.py"
    f.write_text("x")
    mtimes = {"mod.py": f.stat().st_mtime_ns - 1_000_000_000}  # indexed before the edit
    s = _tree_state_v181(monkeypatch, ["mod.py"], mtimes=mtimes, root=tmp_path)
    assert s["blocks"] is True


def test_a_deletion_under_the_index_blocks(tmp_path, monkeypatch):
    mtimes = {"gone.py": 1}
    s = _tree_state_v181(monkeypatch, ["gone.py"], mtimes=mtimes, root=tmp_path)
    assert s["files_not_in_index"] == 1


# --- what it does to the verdict ---------------------------------------------

def test_a_blocking_tree_cannot_prove_absence():
    v = build_verdict(
        result_count=0,
        working_tree={"state": "dirty_in_scope", "files_not_in_index": 2, "blocks": True},
    )["verdict"]
    assert v["state"] == "degraded"
    assert "2 uncommitted change(s)" in v["note"]
    assert "blocks" not in v["working_tree"]


def test_a_non_blocking_tree_is_disclosed_and_still_absent():
    v = build_verdict(
        result_count=0,
        working_tree={"state": "dirty_outside_scope", "files_dirty": 3, "blocks": False},
    )["verdict"]
    assert v["state"] == "absent"
    assert v["working_tree"]["state"] == "dirty_outside_scope"


def test_a_result_that_returned_rows_is_never_downgraded():
    v = build_verdict(
        result_count=4,
        working_tree={"state": "dirty_in_scope", "files_not_in_index": 2, "blocks": True},
    )["verdict"]
    assert v["state"] == "ok"


def test_the_false_warning_is_suppressed():
    r = build_verdict(
        result_count=0,
        working_tree={"state": "dirty_in_scope", "files_not_in_index": 1, "blocks": True},
    )
    assert r["negative_evidence"] is None


def test_a_producer_that_passes_nothing_is_unchanged():
    assert build_verdict(result_count=0) == build_verdict(result_count=0, working_tree=None)


# --- the refusal names the cause ---------------------------------------------

def test_the_refusal_counts_the_files_the_scan_could_not_read():
    why = absence_refusal(
        {
            "state": "degraded",
            "channels": {"index": "fresh"},
            "working_tree": {"state": "dirty_in_scope", "files_not_in_index": 3},
        }
    )
    assert why and "3 uncommitted change(s) inside the scanned scope" in why


def test_a_dirty_but_indexed_scope_is_not_refused():
    assert (
        absence_refusal(
            {
                "state": "absent",
                "channels": {"index": "fresh"},
                "working_tree": {"state": "dirty_in_scope", "files_not_in_index": 0},
            }
        )
        is None
    )


def test_note_absence_refuses_a_scan_over_unindexed_edits():
    clear_absences()
    v = build_verdict(
        result_count=0,
        working_tree={"state": "dirty_in_scope", "files_not_in_index": 1, "blocks": True},
    )["verdict"]
    ref, why = note_absence("search_text", "local/x", "needle", v)
    assert ref is None and "uncommitted change(s)" in why


def test_a_clean_scope_still_mints_a_ref():
    clear_absences()
    v = build_verdict(result_count=0, working_tree={"state": "clean", "blocks": False})[
        "verdict"
    ]
    ref, why = note_absence("search_text", "local/x", "needle-clean-tree", v)
    assert why is None and ref.startswith("absent:")


# --- end to end --------------------------------------------------------------

def _content(res):
    from mcp.types import CallToolResult

    return res.content if isinstance(res, CallToolResult) else res


def _pretend_git_backed(monkeypatch):
    """The fixture repo is a plain folder, which is honestly `not_applicable`.

    These cases are about a git-backed subject, so give it a revision.
    """
    from jcodemunch_mcp.retrieval.freshness import FreshnessProbe

    monkeypatch.setattr(FreshnessProbe, "repo_freshness", property(lambda self: "fresh"))


@pytest.fixture
def dispatch_repo(small_index, monkeypatch):
    monkeypatch.setenv("CODE_INDEX_PATH", small_index["store"])
    return small_index


@pytest.fixture
def full_meta(monkeypatch):
    from jcodemunch_mcp import config as _config

    _real = _config.get

    def _get(key, default=None, **kw):
        if key == "meta_fields":
            return None
        return _real(key, default, **kw)

    monkeypatch.setattr(_config, "get", _get)
    return _get


@pytest.mark.asyncio
async def test_an_unindexed_edit_in_scope_refuses_the_absence_live(
    dispatch_repo, full_meta, monkeypatch
):
    from jcodemunch_mcp import server

    subj._clear_tree_cache()
    _pretend_git_backed(monkeypatch)
    monkeypatch.setattr(
        subj, "_working_tree_reading", lambda root: ("fp", ("utils_new.py",))
    )

    body = json.loads(
        _content(
            await server.call_tool(
                "search_text",
                {
                    "repo": dispatch_repo["repo"],
                    "query": "qxzvvwyjkkbb11",
                    "format": "json",
                },
            )
        )[0].text
    )
    verdict = body["_meta"]["verdict"]
    assert verdict["working_tree"]["state"] == "dirty_in_scope"
    assert verdict["state"] == "degraded"
    assert "evidence_ref" not in verdict


@pytest.mark.asyncio
async def test_the_same_edit_outside_a_narrow_scope_still_mints_a_ref(
    dispatch_repo, full_meta, monkeypatch
):
    """Non-vacuity, and the reviewer's distinction: a narrow proof survives work
    that could not have affected it."""
    from jcodemunch_mcp import server

    subj._clear_tree_cache()
    _pretend_git_backed(monkeypatch)
    monkeypatch.setattr(
        subj, "_working_tree_reading", lambda root: ("fp", ("docs/notes.md",))
    )

    body = json.loads(
        _content(
            await server.call_tool(
                "search_text",
                {
                    "repo": dispatch_repo["repo"],
                    "query": "qxzvvwyjkkbb12",
                    "file_pattern": "*.py",
                    "format": "json",
                },
            )
        )[0].text
    )
    verdict = body["_meta"]["verdict"]
    assert verdict["working_tree"]["state"] == "dirty_outside_scope"
    assert verdict["state"] == "absent"
    assert verdict["evidence_ref"].startswith("absent:")


@pytest.mark.asyncio
async def test_search_symbols_and_ranked_context_carry_it_too(
    dispatch_repo, full_meta, monkeypatch
):
    from jcodemunch_mcp import server

    for tool, extra in (
        ("search_symbols", {}),
        ("get_ranked_context", {"token_budget": 500}),
    ):
        subj._clear_tree_cache()
        _pretend_git_backed(monkeypatch)
        monkeypatch.setattr(
            subj, "_working_tree_reading", lambda root: ("fp", ("utils_new.py",))
        )
        body = json.loads(
            _content(
                await server.call_tool(
                    tool,
                    {
                        "repo": dispatch_repo["repo"],
                        "query": "qxzvvwyjkkbb13",
                        "format": "json",
                        **extra,
                    },
                )
            )[0].text
        )
        verdict = body["_meta"]["verdict"]
        assert verdict["working_tree"]["state"] == "dirty_in_scope", tool
        assert verdict["state"] == "degraded", tool


@pytest.mark.asyncio
async def test_a_scan_that_returned_rows_never_pays_for_the_probe(
    dispatch_repo, full_meta, monkeypatch
):
    """Cost discipline: the subprocess is priced into absences only."""
    from jcodemunch_mcp import server

    subj._clear_tree_cache()
    calls = {"n": 0}

    def counting(root):
        calls["n"] += 1
        return ("fp", ("utils_new.py",))

    monkeypatch.setattr(subj, "_working_tree_reading", counting)
    await server.call_tool(
        "search_text",
        {"repo": dispatch_repo["repo"], "query": "MAX_RETRIES", "format": "json"},
    )
    assert calls["n"] == 0
