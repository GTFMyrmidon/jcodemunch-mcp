"""The subject has to hold still for the scan (v1.108.179).

#377 hardening item 6. A search can start against one state and finish after
the source or the index has changed: a concurrent edit, a watcher reindex, an
incremental save, a new generation published, or a long scan crossing a
rebuild. A citable current-state negative requires the reading taken before
retrieval and the reading taken after it to match.

Checked for a zero-result scan only. A scan that returned rows read them out of
a generation that really held them, and the freshness channels already disclose
that the tree moved underneath. It is the claim that NOTHING exists that cannot
survive its subject changing mid-read.
"""

from __future__ import annotations

import json

import pytest

from jcodemunch_mcp.handoff import absence_refusal, clear_absences, note_absence
from jcodemunch_mcp.retrieval import subject_state as subj
from jcodemunch_mcp.retrieval.verdict import build_verdict


class _Idx:
    """Minimal stand-in: capture() reads plain attributes and never raises."""

    def __init__(self, generation="g1"):
        self.indexed_at = generation
        self.git_head = "a" * 40
        self.source_root = None


# --- the around-retrieval comparison -----------------------------------------

def test_a_still_subject_reports_no_movement():
    idx = _Idx()
    before = subj.capture(idx)
    assert subj.moved_during_scan(before, idx, result_count=0) is None


def test_a_generation_published_mid_scan_is_movement():
    idx = _Idx()
    before = subj.capture(idx)
    idx.indexed_at = "g2"  # a new generation landed while we were reading
    why = subj.moved_during_scan(before, idx, result_count=0)
    assert why and "rebuilt" in why
    assert "while the scan was running" in why


def test_a_scan_that_returned_rows_is_not_checked():
    """Only the absence claim cannot survive the subject moving, and the fresh
    HEAD read is affordable precisely because it is scoped to that case."""
    idx = _Idx()
    before = subj.capture(idx)
    idx.indexed_at = "g2"
    assert subj.moved_during_scan(before, idx, result_count=5) is None


def test_no_before_reading_is_not_movement():
    assert subj.moved_during_scan(None, _Idx(), result_count=0) is None
    assert subj.moved_during_scan({}, _Idx(), result_count=0) is None


def test_the_two_contexts_read_as_different_sentences():
    """The same comparison serves the cached replay and the live scan, and a
    reader must be able to tell which one refused them."""
    a = subj.changed({"generation": "g1"}, {"generation": "g2"})
    b = subj.changed(
        {"generation": "g1"}, {"generation": "g2"}, when=subj.WHEN_SCANNING
    )
    assert a.endswith("after this scan was cached")
    assert b.endswith("while the scan was running")


def test_a_fresh_head_read_bypasses_the_ttl_cache(monkeypatch, tmp_path):
    """The HEAD lookup is TTL-cached, so a before/after comparison inside that
    window would otherwise compare one reading with itself."""
    from jcodemunch_mcp.retrieval import freshness

    calls = {"cached": 0, "uncached": 0}
    monkeypatch.setattr(
        freshness, "_git_head", lambda root: calls.__setitem__("cached", calls["cached"] + 1)
    )
    monkeypatch.setattr(
        freshness,
        "_git_head_uncached",
        lambda root: calls.__setitem__("uncached", calls["uncached"] + 1),
    )

    def Rooted():
        idx = _Idx()
        idx.source_root = str(tmp_path)
        return idx

    subj.capture(Rooted())
    assert (calls["cached"], calls["uncached"]) == (1, 0)
    subj.capture(Rooted(), fresh_head=True)
    assert (calls["cached"], calls["uncached"]) == (1, 1)


# --- what movement does to the verdict ---------------------------------------

def test_a_moved_subject_cannot_prove_absence():
    v = build_verdict(result_count=0, moved_during_scan="the index was rebuilt")["verdict"]
    assert v["state"] == "degraded"
    assert v["moved_during_scan"] == {"reason": "the index was rebuilt"}
    assert "re-run the search" in v["note"]


def test_a_still_subject_is_still_absent():
    """Non-vacuity: the gate fires on movement, not on emptiness."""
    assert build_verdict(result_count=0)["verdict"]["state"] == "absent"


def test_movement_is_disclosed_on_every_state():
    v = build_verdict(result_count=4, moved_during_scan="the index was rebuilt")["verdict"]
    assert v["state"] == "ok"
    assert v["moved_during_scan"]["reason"] == "the index was rebuilt"
    assert v["note"] == "Confident matches returned."


def test_the_false_warning_is_suppressed():
    """`negative_evidence` renders as "Do not claim this feature exists"."""
    assert build_verdict(result_count=0, moved_during_scan="x")["negative_evidence"] is None


def test_a_producer_that_reports_nothing_is_unchanged():
    before = build_verdict(result_count=0, scanned_symbols=9)
    after = build_verdict(result_count=0, scanned_symbols=9, moved_during_scan=None)
    assert before == after


# --- the refusal names the cause ---------------------------------------------

def test_the_refusal_names_the_movement():
    why = absence_refusal(
        {
            "state": "degraded",
            "channels": {"index": "fresh"},
            "moved_during_scan": {"reason": "the index was rebuilt while the scan ran"},
        }
    )
    assert why and "moved while the scan ran" in why
    assert "neither the state it started against" in why


def test_note_absence_refuses_a_scan_whose_subject_moved():
    clear_absences()
    v = build_verdict(result_count=0, moved_during_scan="the index was rebuilt")["verdict"]
    ref, why = note_absence("search_symbols", "local/x", "needle", v)
    assert ref is None
    assert why and "moved while the scan ran" in why


def test_a_still_scan_still_mints_a_ref():
    clear_absences()
    v = build_verdict(result_count=0)["verdict"]
    ref, why = note_absence("search_symbols", "local/x", "needle-still", v)
    assert why is None and ref.startswith("absent:")


# --- end to end, on the real retrieval paths ---------------------------------

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


@pytest.mark.parametrize(
    "tool,args",
    [
        ("search_symbols", {"query": "qxzvvwyjkkbb6"}),
        ("search_text", {"query": "qxzvvwyjkkbb6"}),
        ("get_ranked_context", {"query": "qxzvvwyjkkbb6", "token_budget": 500}),
    ],
)
@pytest.mark.asyncio
async def test_every_retrieval_tool_checks_around_its_scan(
    dispatch_repo, full_meta, monkeypatch, tool, args
):
    """A generation published mid-scan must refuse the absence in all three."""
    from jcodemunch_mcp import server

    real_capture = subj.capture
    calls = {"n": 0}

    def moving_capture(index, **kw):
        # First reading is the real one; the second (taken after the scan) sees
        # a different generation, which is what a watcher reindex looks like.
        calls["n"] += 1
        state = dict(real_capture(index, **kw))
        if calls["n"] > 1:
            state["generation"] = "a-newer-generation"
        return state

    monkeypatch.setattr(subj, "capture", moving_capture)

    out = await server.call_tool(
        tool, {"repo": dispatch_repo["repo"], "format": "json", **args}
    )
    body = json.loads(_content(out)[0].text)
    verdict = (body.get("_meta") or {}).get("verdict") or {}
    assert verdict.get("state") == "degraded", verdict
    assert verdict["moved_during_scan"]["reason"].endswith("while the scan was running")
    assert "evidence_ref" not in verdict


@pytest.mark.asyncio
async def test_ranked_contexts_no_candidate_path_now_carries_a_verdict(
    dispatch_repo, full_meta
):
    """That path asserted "Do not claim this feature exists" in prose with no
    verdict at all, so none of the absence gates could reach it."""
    from jcodemunch_mcp import server

    out = await server.call_tool(
        "get_ranked_context",
        {
            "repo": dispatch_repo["repo"],
            "query": "qxzvvwyjkkbb8",
            "token_budget": 500,
            "format": "json",
        },
    )
    body = json.loads(_content(out)[0].text)
    assert body["items_considered"] == 0, "must be the no-candidate path"
    verdict = body["_meta"]["verdict"]
    assert verdict["state"] == "absent"
    assert verdict["scanned"]["symbols"] > 0
    assert verdict["evidence_ref"].startswith("absent:")


@pytest.mark.asyncio
async def test_a_still_repo_still_mints_a_ref_on_the_live_path(
    dispatch_repo, full_meta
):
    """Non-vacuity for the three above, through the real dispatcher."""
    from jcodemunch_mcp import server

    out = await server.call_tool(
        "search_text",
        {"repo": dispatch_repo["repo"], "query": "qxzvvwyjkkbb7", "format": "json"},
    )
    body = json.loads(_content(out)[0].text)
    verdict = body["_meta"]["verdict"]
    assert verdict["state"] == "absent"
    assert "moved_during_scan" not in verdict
    assert verdict["evidence_ref"].startswith("absent:")
