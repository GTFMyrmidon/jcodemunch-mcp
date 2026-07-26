"""A cached negative must prove it still describes the subject (v1.108.178).

#377 hardening item 3. The search result cache keyed on the index's
`indexed_at`, so it invalidated on a reindex and on nothing else:

    1. the query is absent against snapshot A
    2. the result and its `fresh` verdict are cached
    3. the source changes without the index being rebuilt
    4. the same query hits the cache
    5. the old fresh/absent verdict is replayed, and an absence proof is
       minted over a tree that no longer exists

A cached POSITIVE stays useful with disclosure; a cached NEGATIVE has to be
re-checked. Same shape as every other gate: disclose on every state, refuse only
the absence claim, express the refusal as a downgrade.
"""

from __future__ import annotations

import json

import pytest

from jcodemunch_mcp.handoff import absence_refusal, clear_absences, note_absence
from jcodemunch_mcp.retrieval import subject_state as subj


# --- what counts as the subject moving --------------------------------------

def test_nothing_moved_is_not_a_change():
    s = {"generation": "g1", "db_mtime_ns": 5, "live_sha": "abc", "tree": "t1"}
    assert subj.changed(s, dict(s)) is None


def test_a_rebuilt_index_is_a_change():
    why = subj.changed({"generation": "g1"}, {"generation": "g2"})
    assert why and "rebuilt" in why


def test_a_rewritten_index_is_a_change():
    why = subj.changed(
        {"generation": "g1", "db_mtime_ns": 5}, {"generation": "g1", "db_mtime_ns": 6}
    )
    assert why and "rewritten" in why


def test_a_moved_checkout_is_a_change_and_names_both_commits():
    why = subj.changed(
        {"live_sha": "a" * 40}, {"live_sha": "b" * 40}
    )
    assert why and "a" * 12 in why and "b" * 12 in why


def test_an_edited_working_tree_is_a_change():
    why = subj.changed({"tree": "t1"}, {"tree": "t2"})
    assert why and "working tree changed" in why


def test_unknown_is_never_reported_as_changed():
    """A non-git checkout knows none of this, and nothing changed there either.

    Treating unknown as changed would refuse every absence on such a repo.
    """
    assert subj.changed({"live_sha": None}, {"live_sha": "abc"}) is None
    assert subj.changed({"live_sha": "abc"}, {"live_sha": None}) is None
    assert subj.changed({"tree": None}, {"tree": "t2"}) is None
    assert subj.changed(None, {"live_sha": "abc"}) is None
    assert subj.changed({}, {}) is None


# --- what a failed revalidation does to the verdict --------------------------

def _absent_verdict():
    return {
        "state": "absent",
        "channels": {"index": "fresh", "lexical": "ok", "semantic": "off"},
        "note": "No match found after scanning the index.",
        "evidence_ref": "absent:deadbeefcafe",
    }


def test_the_absence_claim_is_refused_not_the_result():
    v = _absent_verdict()
    subj.revalidate_verdict(v, "the working tree changed after this scan was cached")
    assert v["state"] == "degraded"
    assert v["revalidated"]["stale_cache"] is True


def test_the_stale_token_is_stripped():
    """It was issued against the state that just failed to hold."""
    v = _absent_verdict()
    subj.revalidate_verdict(v, "the index was rewritten after this scan was cached")
    assert "evidence_ref" not in v


def test_a_cached_positive_keeps_serving_with_disclosure():
    v = {"state": "ok", "note": "Confident matches returned."}
    subj.revalidate_verdict(v, "the working tree changed after this scan was cached")
    assert v["state"] == "ok"
    assert v["note"] == "Confident matches returned."
    assert v["revalidated"]["reason"].startswith("the working tree changed")


def test_the_note_tells_the_agent_what_to_do():
    v = _absent_verdict()
    subj.revalidate_verdict(v, "the working tree changed after this scan was cached")
    assert "replayed from cache" in v["note"]
    assert "re-run the search" in v["note"]


# --- the refusal names the cause ---------------------------------------------

def test_a_revalidated_scan_cannot_be_cited_and_says_why():
    why = absence_refusal(
        {
            "state": "degraded",
            "channels": {"index": "fresh"},
            "revalidated": {"stale_cache": True, "reason": "the working tree changed"},
        }
    )
    assert why and "replayed from cache" in why
    assert "no longer holds" in why


def test_note_absence_refuses_a_revalidated_verdict():
    clear_absences()
    v = _absent_verdict()
    subj.revalidate_verdict(v, "the working tree changed after this scan was cached")
    ref, why = note_absence("search_symbols", "local/x", "needle", v)
    assert ref is None
    assert why and "replayed from cache" in why


def test_an_unrevalidated_absent_scan_still_mints_a_ref():
    """Non-vacuity for the refusal above."""
    clear_absences()
    v = _absent_verdict()
    v.pop("evidence_ref")
    ref, why = note_absence("search_symbols", "local/x", "needle-clean", v)
    assert why is None and ref.startswith("absent:")


# --- the working-tree probe --------------------------------------------------

def test_the_tree_fingerprint_is_none_outside_a_checkout(tmp_path):
    subj._clear_tree_cache()
    assert subj.working_tree_fingerprint(str(tmp_path)) is None


def test_the_tree_fingerprint_is_none_without_a_source_root():
    assert subj.working_tree_fingerprint(None) is None


def test_capture_never_raises_on_a_hand_built_index():
    class Bare:
        pass

    state = subj.capture(Bare(), include_tree=True)
    assert state["live_sha"] is None
    assert state["generation"] is None


def test_capture_skips_the_expensive_probe_for_a_positive():
    """A cached positive pays nothing: the tree key is not even collected."""

    class Bare:
        source_root = "/nonexistent"

    assert "tree" not in subj.capture(Bare(), include_tree=False)


# --- end to end, against the real cache --------------------------------------

def _content(res):
    from mcp.types import CallToolResult

    return res.content if isinstance(res, CallToolResult) else res


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
async def test_a_cached_absence_is_refused_once_the_subject_moves(
    dispatch_repo, full_meta, monkeypatch
):
    """The live defect: the same query, served from cache, over a moved tree."""
    from jcodemunch_mcp import server
    from jcodemunch_mcp.tools import search_symbols as ss

    args = {"repo": dispatch_repo["repo"], "query": "qxzvvwyjkkbb", "format": "json"}

    # The fixture repo is not a git checkout, so stand in for the tree probe:
    # what the production path reads is a fingerprint that changes when the
    # working tree does.
    tree = {"fp": "before-the-edit"}
    monkeypatch.setattr(subj, "working_tree_fingerprint", lambda root: tree["fp"])

    first = json.loads(_content(await server.call_tool("search_symbols", args))[0].text)
    fv = (first.get("_meta") or {}).get("verdict") or {}
    assert fv["state"] == "absent"
    assert fv["evidence_ref"].startswith("absent:"), "fixture must mint a ref"

    # The subject moves: no reindex, so `indexed_at` (the whole cache key) is
    # unchanged and the entry is still a hit.
    tree["fp"] = "after-the-edit"
    assert ss is not None  # the tool module holds no copy of the probe

    second = json.loads(_content(await server.call_tool("search_symbols", args))[0].text)
    sv = (second.get("_meta") or {}).get("verdict") or {}
    assert second["_meta"]["cache_hit"] is True, "must be the cached entry, not a re-scan"
    assert sv["state"] == "degraded"
    assert sv["revalidated"]["stale_cache"] is True
    assert "evidence_ref" not in sv


@pytest.mark.asyncio
async def test_an_unmoved_subject_replays_the_cached_answer_unchanged(
    dispatch_repo, full_meta
):
    """Non-vacuity + zero blast radius: nothing moved, nothing is downgraded."""
    from jcodemunch_mcp import server

    args = {"repo": dispatch_repo["repo"], "query": "qxzvvwyjkkbb2", "format": "json"}
    first = json.loads(_content(await server.call_tool("search_symbols", args))[0].text)
    second = json.loads(_content(await server.call_tool("search_symbols", args))[0].text)

    assert second["_meta"]["cache_hit"] is True
    sv = second["_meta"]["verdict"]
    assert sv["state"] == "absent"
    assert "revalidated" not in sv
    assert sv["evidence_ref"] == first["_meta"]["verdict"]["evidence_ref"]


@pytest.mark.asyncio
async def test_the_dispatchers_token_does_not_leak_into_the_cached_entry(
    dispatch_repo, full_meta
):
    """`_meta.verdict` was shared with the stored entry, so a write the
    dispatcher made after the tool returned landed in the cache."""
    from jcodemunch_mcp import server
    from jcodemunch_mcp.tools.search_symbols import _result_cache

    args = {"repo": dispatch_repo["repo"], "query": "qxzvvwyjkkbb3", "format": "json"}
    await server.call_tool("search_symbols", args)
    stored = [v for k, v in _result_cache.items() if k[2] == "qxzvvwyjkkbb3"]
    assert stored, "entry must be cached"
    assert "evidence_ref" not in (stored[0]["_meta"]["verdict"])
