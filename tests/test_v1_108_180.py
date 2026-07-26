"""Unknown freshness stops collapsing into fresh (v1.108.180).

#377 hardening item 4. `FreshnessProbe.repo_is_stale` is a Boolean, and a
Boolean has nowhere to put "I could not find out": it returned False both when
the SHAs matched and when either of them was missing, and the verdict rendered
False as `channels.index: "fresh"`. An index whose freshness was never
established was claiming current-snapshot equivalence, and every absence gate
downstream trusted that claim.

Four states now, and only one of them is proof:

    fresh        the indexed revision equals the live one
    stale        they differ
    unknown      the subject HAS a revision we could not read
    not_tracked  the subject has no revision at all

`unknown` and `not_tracked` get opposite treatment on purpose: a capability we
have, failing, cannot back an absence claim; a capability the subject does not
support is disclosed rather than refused, the call jDataMunch made in v1.26.0.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from jcodemunch_mcp.handoff import absence_refusal, clear_absences, note_absence
from jcodemunch_mcp.retrieval.freshness import FreshnessProbe, _clear_head_cache
from jcodemunch_mcp.retrieval.verdict import build_verdict


def _probe(tmp_path, *, index_sha="a" * 40, root=True):
    return FreshnessProbe(
        source_root=str(tmp_path) if root else None,
        indexed_at="2026-07-26T00:00:00",
        index_sha=index_sha,
    )


@pytest.fixture
def git_repo(tmp_path):
    """A real checkout with one commit — the only way to prove `fresh` is real."""
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    import os

    env = {**os.environ, **env}
    (tmp_path / "f.txt").write_text("hello")
    for cmd in (
        ["git", "init", "-q"],
        ["git", "add", "."],
        ["git", "commit", "-qm", "one"],
    ):
        subprocess.run(cmd, cwd=tmp_path, check=True, capture_output=True, env=env)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True
    ).stdout.strip()
    _clear_head_cache()
    return tmp_path, sha


# --- the four states ---------------------------------------------------------

def test_a_matching_revision_is_fresh(git_repo):
    root, sha = git_repo
    assert FreshnessProbe(str(root), "2026-07-26", sha).repo_freshness == "fresh"


def test_a_differing_revision_is_stale(git_repo):
    root, _ = git_repo
    assert FreshnessProbe(str(root), "2026-07-26", "b" * 40).repo_freshness == "stale"


def test_a_folder_with_no_revision_is_not_tracked(tmp_path):
    """A plain indexed folder. There is nothing to compare and never will be."""
    assert _probe(tmp_path).repo_freshness == "not_tracked"


def test_a_git_repo_whose_sha_we_never_stored_is_unknown(git_repo):
    root, _ = git_repo
    assert FreshnessProbe(str(root), "2026-07-26", None).repo_freshness == "unknown"


def test_a_subdirectory_of_a_checkout_is_still_tracked(git_repo):
    """A monorepo subdir index is tracked by the checkout above it. Calling it
    not_tracked would understate what we can actually prove about it."""
    root, sha = git_repo
    sub = root / "pkg"
    sub.mkdir()
    _clear_head_cache()
    assert FreshnessProbe(str(sub), "2026-07-26", sha).repo_freshness == "fresh"


def test_a_missing_source_root_is_unknown(tmp_path):
    assert (
        FreshnessProbe(str(tmp_path / "gone"), "2026-07-26", "a" * 40).repo_freshness
        == "unknown"
    )


def test_no_source_root_at_all_is_unknown():
    assert FreshnessProbe(None, "2026-07-26", "a" * 40).repo_freshness == "unknown"


def test_the_boolean_still_answers_the_question_it_was_asked(tmp_path, git_repo):
    """`repo_is_stale` keeps its meaning; it just stops being the whole story."""
    root, sha = git_repo
    assert FreshnessProbe(str(root), "2026-07-26", "b" * 40).repo_is_stale is True
    assert FreshnessProbe(str(root), "2026-07-26", sha).repo_is_stale is False
    assert _probe(None, root=False).repo_is_stale is False  # unknown is not stale


# --- what the verdict does with them -----------------------------------------

def test_unknown_is_reported_as_unknown_not_fresh():
    v = build_verdict(result_count=3, freshness="unknown")["verdict"]
    assert v["channels"]["index"] == "unknown"


def test_not_tracked_is_reported_as_not_tracked_not_fresh():
    v = build_verdict(result_count=3, freshness="not_tracked")["verdict"]
    assert v["channels"]["index"] == "not_tracked"


def test_unknown_freshness_cannot_prove_absence():
    v = build_verdict(result_count=0, freshness="unknown")["verdict"]
    assert v["state"] == "degraded"
    assert "could not be established" in v["note"]


def test_not_tracked_still_proves_absence():
    """Disclosed, not refused. Refusing would strip absence evidence from every
    plain-folder index, where the limitation is permanent and already stated."""
    v = build_verdict(result_count=0, freshness="not_tracked")["verdict"]
    assert v["state"] == "absent"
    assert v["channels"]["index"] == "not_tracked"


def test_unknown_does_not_downgrade_a_result_that_returned_rows():
    """Disclose on every state, refuse only the absence claim."""
    v = build_verdict(result_count=5, freshness="unknown")["verdict"]
    assert v["state"] == "ok"
    assert v["note"] == "Confident matches returned."


def test_the_false_warning_is_suppressed_for_unknown():
    assert build_verdict(result_count=0, freshness="unknown")["negative_evidence"] is None


def test_stale_passed_as_a_state_behaves_like_the_boolean():
    v = build_verdict(result_count=0, freshness="stale")["verdict"]
    assert v["channels"]["index"] == "stale"
    assert v["state"] == "absent"  # refused later, by absence_refusal, as before


def test_a_producer_that_passes_nothing_is_byte_identical():
    """Zero blast radius for any caller not yet taught the richer state."""
    assert build_verdict(result_count=0, index_stale=False) == build_verdict(
        result_count=0, index_stale=False, freshness=None
    )
    assert (
        build_verdict(result_count=0, index_stale=False)["verdict"]["channels"]["index"]
        == "fresh"
    )


def test_garbage_from_a_producer_is_ignored_not_propagated():
    v = build_verdict(result_count=0, freshness="probably-fine")["verdict"]
    assert v["channels"]["index"] == "fresh"


def test_a_rebuild_still_outranks_the_new_states():
    v = build_verdict(result_count=0, freshness="unknown", index_changed=True)["verdict"]
    assert v["channels"]["index"] == "rebuilding"


# --- the refusal names the cause ---------------------------------------------

def test_the_refusal_names_the_failed_capability():
    why = absence_refusal({"state": "degraded", "channels": {"index": "unknown"}})
    assert why and "freshness could not be established" in why


def test_a_not_tracked_scan_is_not_refused():
    assert absence_refusal({"state": "absent", "channels": {"index": "not_tracked"}}) is None


def test_note_absence_refuses_an_unknown_freshness_scan():
    clear_absences()
    v = build_verdict(result_count=0, freshness="unknown")["verdict"]
    ref, why = note_absence("search_symbols", "local/x", "needle", v)
    assert ref is None
    assert why and "freshness could not be established" in why


def test_a_not_tracked_scan_still_mints_a_ref():
    clear_absences()
    v = build_verdict(result_count=0, freshness="not_tracked")["verdict"]
    ref, why = note_absence("search_symbols", "local/x", "needle-nt", v)
    assert why is None and ref.startswith("absent:")


# --- end to end --------------------------------------------------------------

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


@pytest.mark.parametrize("tool", ["search_symbols", "search_text", "get_ranked_context"])
@pytest.mark.asyncio
async def test_a_plain_folder_index_stops_claiming_freshness(
    dispatch_repo, full_meta, tool
):
    """The fixture is an indexed folder with no git. It reported `fresh`."""
    from jcodemunch_mcp import server

    args = {"repo": dispatch_repo["repo"], "query": "qxzvvwyjkkbb9", "format": "json"}
    if tool == "get_ranked_context":
        args["token_budget"] = 500
    body = json.loads(_content(await server.call_tool(tool, args))[0].text)
    verdict = (body.get("_meta") or {}).get("verdict") or {}
    assert verdict["channels"]["index"] == "not_tracked"
    # Disclosed, not refused: the absence is still citable.
    assert verdict["state"] == "absent"
    assert verdict["evidence_ref"].startswith("absent:")


@pytest.mark.asyncio
async def test_an_unreadable_revision_refuses_the_absence_live(
    dispatch_repo, full_meta, monkeypatch
):
    """A subject that HAS a revision we cannot read: refused, not disclosed."""
    from jcodemunch_mcp import server
    from jcodemunch_mcp.retrieval import freshness as fr

    monkeypatch.setattr(FreshnessProbe, "repo_freshness", property(lambda self: "unknown"))
    assert fr is not None

    body = json.loads(
        _content(
            await server.call_tool(
                "search_text",
                {
                    "repo": dispatch_repo["repo"],
                    "query": "qxzvvwyjkkbb10",
                    "format": "json",
                },
            )
        )[0].text
    )
    verdict = body["_meta"]["verdict"]
    assert verdict["channels"]["index"] == "unknown"
    assert verdict["state"] == "degraded"
    assert "evidence_ref" not in verdict
