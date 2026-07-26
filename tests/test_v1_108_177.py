"""An omitted match is not an absent match (v1.108.177).

Hardening items 1, 7, 8, 9, 10, 11 and 12 from the #377 review. Every one of
them is the same defect wearing a different coat: something other than the
corpus decided the response was empty, and the verdict read that emptiness as
proof the target does not exist.

    - the token-budget packer dropped every ranked match (item 1)
    - the context budget packed none of the candidates (item 8)
    - the scope selected no eligible file, so nothing was searched (item 9)
    - an eligible file could not be opened, so the sweep was partial (item 7)
    - a display preference deleted the fields the safety gates read (item 10)

The rule is the shipped one: disclose on every state, refuse only the absence
CLAIM, and do the refusing by downgrading to `degraded` so the existing "only
`absent` proves absence" check needs no second rule to keep in sync.
"""

from __future__ import annotations

import json

import pytest

from jcodemunch_mcp.encoding.schema_driven import UNIVERSAL_META_JSON
from jcodemunch_mcp.handoff import absence_refusal, clear_absences, note_absence
from jcodemunch_mcp.retrieval.verdict import build_verdict


# --- item 1: an empty response is not an empty search -------------------------

def test_a_packed_empty_response_does_not_prove_absence():
    v = build_verdict(result_count=0, matches_before_packing=7)["verdict"]
    assert v["state"] == "degraded"


def test_the_note_says_the_matches_existed():
    v = build_verdict(result_count=0, matches_before_packing=7)["verdict"]
    assert "7 match(es) were found" in v["note"]
    assert "Absence is NOT proven" in v["note"]


def test_a_genuinely_empty_search_is_still_absent():
    """Non-vacuity: the gate fires on omission, not on emptiness."""
    v = build_verdict(result_count=0, matches_before_packing=0)["verdict"]
    assert v["state"] == "absent"


def test_omission_is_disclosed_on_every_state_not_just_the_refused_one():
    v = build_verdict(result_count=3, matches_before_packing=10)["verdict"]
    assert v["state"] == "ok"
    assert v["omitted"] == {
        "matches_found": 10,
        "returned": 3,
        "by_response_budget": 7,
    }


def test_a_complete_response_carries_no_omission_block():
    v = build_verdict(result_count=3, matches_before_packing=3)["verdict"]
    assert "omitted" not in v


def test_a_producer_that_reports_nothing_behaves_exactly_as_before():
    """Zero blast radius for any caller that has not been taught to count."""
    before = build_verdict(result_count=0, scanned_symbols=5, scanned_files=1)
    after = build_verdict(
        result_count=0, scanned_symbols=5, scanned_files=1, matches_before_packing=None
    )
    assert before == after
    assert before["verdict"]["state"] == "absent"


def test_the_legacy_warning_stops_claiming_the_feature_does_not_exist():
    """`negative_evidence` renders as "Do not claim this feature exists" — a
    false statement when the matches were found and dropped by the packer."""
    r = build_verdict(result_count=0, matches_before_packing=7)
    assert r["negative_evidence"] is None
    assert build_verdict(result_count=0)["negative_evidence"] is not None


# --- items 7 and 9: a scan that did not run, or ran over part of its inputs ----

def test_an_empty_eligible_scope_proves_nothing():
    v = build_verdict(
        result_count=0,
        incomplete={"reason": "empty_scope", "note": "nothing was searched"},
    )["verdict"]
    assert v["state"] == "degraded"
    assert v["incomplete"]["reason"] == "empty_scope"
    assert v["note"] == "nothing was searched"


def test_unreadable_inputs_make_the_sweep_partial():
    v = build_verdict(
        result_count=0,
        incomplete={
            "reason": "unreadable_inputs",
            "files_unreadable": 3,
            "note": "3 of 10 eligible file(s) could not be read",
        },
    )["verdict"]
    assert v["state"] == "degraded"


def test_incompleteness_is_disclosed_even_when_matches_were_found():
    v = build_verdict(
        result_count=2, incomplete={"reason": "unreadable_inputs", "files_unreadable": 3}
    )["verdict"]
    assert v["state"] == "ok"
    assert v["incomplete"]["files_unreadable"] == 3


# --- the refusal names the cause ---------------------------------------------

def _record(**over):
    r = {
        "state": "absent",
        "channels": {"index": "fresh"},
        "truncated": False,
    }
    r.update(over)
    return r


def test_refusal_names_the_packer_rather_than_the_state():
    why = absence_refusal(
        _record(state="degraded", omitted={"matches_found": 7, "returned": 0})
    )
    assert why and "omitted to fit the response budget" in why
    assert "describes packing, not absence" in why


def test_refusal_names_an_empty_scope():
    why = absence_refusal(_record(state="degraded", incomplete={"reason": "empty_scope"}))
    assert why and "selected no eligible input" in why


def test_refusal_names_unreadable_inputs():
    why = absence_refusal(
        _record(
            state="degraded",
            incomplete={"reason": "unreadable_inputs", "files_unreadable": 3},
        )
    )
    assert why and "3 eligible input(s) could not be read" in why


def test_a_clean_scan_is_still_refused_by_nothing():
    assert absence_refusal(_record()) is None


def test_a_packed_empty_scan_cannot_be_cited(monkeypatch):
    clear_absences()
    v = build_verdict(result_count=0, matches_before_packing=7)["verdict"]
    ref, why = note_absence("search_symbols", "local/x", "needle", v)
    assert ref is None
    assert why and "omitted to fit the response budget" in why


def test_the_same_scan_without_the_packer_still_mints_a_ref():
    """Non-vacuity for the refusal above."""
    clear_absences()
    v = build_verdict(result_count=0)["verdict"]
    ref, why = note_absence("search_symbols", "local/x", "needle-clean", v)
    assert why is None and ref.startswith("absent:")


# --- item 12: two different operations cannot share one evidence id -----------

def test_a_literal_and_a_regex_search_are_different_scans():
    clear_absences()
    v = build_verdict(result_count=0)["verdict"]
    literal, _ = note_absence(
        "search_text", "local/x", "a|b", v, arguments={"is_regex": False}
    )
    regex, _ = note_absence(
        "search_text", "local/x", "a|b", v, arguments={"is_regex": True}
    )
    assert literal and regex and literal != regex


def test_semantic_and_lexical_searches_are_different_scans():
    clear_absences()
    v = build_verdict(result_count=0)["verdict"]
    lexical, _ = note_absence("search_symbols", "local/x", "q", v, arguments={})
    semantic, _ = note_absence(
        "search_symbols", "local/x", "q", v, arguments={"semantic": True}
    )
    assert lexical != semantic


# --- items 10 and 11: the carrier survives the transport ---------------------

def test_a_dispatcher_argument_is_not_a_caller_mistake():
    """`suppress_meta` is read by the dispatcher, so no tool schema declares it.

    v1.108.175 counted it as an ignored argument, which downgraded the verdict
    and cost a well-formed call its absence evidence. Found while wiring item 10.
    """
    from jcodemunch_mcp.tools._arg_contract import unrecognized_keys

    declared = ("repo", "query")
    assert unrecognized_keys({"repo": "r", "suppress_meta": True}, declared) == []
    assert unrecognized_keys({"repo": "r", "regex": True}, declared) == ["regex"]


def test_the_carrier_is_a_universal_meta_blob():
    assert "absence_evidence" in UNIVERSAL_META_JSON


def test_the_carrier_round_trips_through_a_schema_that_never_declared_it():
    from jcodemunch_mcp.encoding.schemas import search_text as st_schema

    payload = st_schema.encode(
        "search_text",
        {
            "result_count": 0,
            "results": [],
            "_meta": {
                "timing_ms": 1.0,
                "absence_evidence": {"ref": "absent:deadbeefcafe", "citable": True},
            },
        },
    )
    payload = payload[0] if isinstance(payload, tuple) else payload
    decoded = st_schema.decode(payload)
    assert decoded["_meta"]["absence_evidence"]["ref"] == "absent:deadbeefcafe"


# --- end to end, against the real dispatcher ---------------------------------

def _content(res):
    from mcp.types import CallToolResult

    return res.content if isinstance(res, CallToolResult) else res


@pytest.fixture
def dispatch_repo(small_index, monkeypatch):
    monkeypatch.setenv("CODE_INDEX_PATH", small_index["store"])
    return small_index


@pytest.fixture
def full_meta(monkeypatch):
    """Show the whole `_meta`.

    jcodemunch ships `meta_fields: []`, so the verdict is a display casualty by
    default — which is the reason item 10 matters and the reason the carrier
    exists. These tests want to read the verdict itself.
    """
    from jcodemunch_mcp import config as _config

    _real = _config.get

    def _get(key, default=None, **kw):
        if key == "meta_fields":
            return None
        return _real(key, default, **kw)

    monkeypatch.setattr(_config, "get", _get)
    return _get


@pytest.mark.asyncio
async def test_a_budget_too_small_to_carry_a_match_does_not_prove_absence(dispatch_repo, full_meta):
    """The live path: matching symbols exist, none fit, response is empty."""
    from jcodemunch_mcp import server

    out = await server.call_tool(
        "search_symbols",
        {
            "repo": dispatch_repo["repo"],
            "query": "add",
            "token_budget": 1,
            "detail_level": "full",
            "format": "json",
        },
    )
    body = json.loads(_content(out)[0].text)
    verdict = (body.get("_meta") or {}).get("verdict") or {}
    assert body["result_count"] == 0, "fixture must actually pack to empty"
    assert verdict["state"] == "degraded"
    assert verdict["omitted"]["matches_found"] >= 1
    assert "evidence_ref" not in verdict

    # Non-vacuity: the same scan, reported the way it was before this release
    # (post-packing count only), is `absent` and mints a citable ref.
    from jcodemunch_mcp.handoff import note_absence as _note

    old = build_verdict(
        result_count=0,
        scanned_symbols=verdict["scanned"]["symbols"],
        scanned_files=verdict["scanned"]["files"],
    )["verdict"]
    assert old["state"] == "absent"
    ref, why = _note("search_symbols", dispatch_repo["repo"], "add", old)
    assert why is None and ref.startswith("absent:")


@pytest.mark.asyncio
async def test_a_scope_that_selects_no_file_is_not_an_absence(dispatch_repo, full_meta):
    from jcodemunch_mcp import server

    out = await server.call_tool(
        "search_text",
        {
            "repo": dispatch_repo["repo"],
            "query": "MAX_RETRIES",
            "file_pattern": "*.nosuchextension",
            "format": "json",
        },
    )
    body = json.loads(_content(out)[0].text)
    verdict = (body.get("_meta") or {}).get("verdict") or {}
    assert body["result_count"] == 0
    assert verdict.get("state") == "degraded"
    assert verdict["incomplete"]["reason"] == "empty_scope"
    assert "evidence_ref" not in verdict


@pytest.mark.asyncio
async def test_a_real_absence_over_a_real_scope_still_mints_a_ref(dispatch_repo, full_meta):
    """Non-vacuity for the two tests above, on the live path."""
    from jcodemunch_mcp import server

    out = await server.call_tool(
        "search_text",
        {
            "repo": dispatch_repo["repo"],
            "query": "qxzvvwyjkkbb",
            "format": "json",
        },
    )
    body = json.loads(_content(out)[0].text)
    verdict = (body.get("_meta") or {}).get("verdict") or {}
    assert verdict.get("state") == "absent"
    assert verdict.get("evidence_ref", "").startswith("absent:")


@pytest.mark.asyncio
async def test_search_text_now_reports_index_freshness(dispatch_repo, full_meta):
    """It never passed freshness into the verdict, so the stale gate its sibling
    tools enforce could not fire on a stale-index absence."""
    from jcodemunch_mcp import server

    out = await server.call_tool(
        "search_text",
        {"repo": dispatch_repo["repo"], "query": "qxzvvwyjkkbb", "format": "json"},
    )
    body = json.loads(_content(out)[0].text)
    channels = ((body.get("_meta") or {}).get("verdict") or {}).get("channels") or {}
    # v1.108.180 widened this: the fixture is a plain folder, so the honest
    # answer is `not_tracked` rather than the `fresh` it used to claim.
    assert channels.get("index") in (
        "fresh", "stale", "partial", "rebuilding", "unknown", "not_tracked"
    )


@pytest.mark.asyncio
async def test_the_evidence_survives_a_display_preference_that_deletes_it(
    dispatch_repo, monkeypatch
):
    """Item 10. `meta_fields: []` used to run BEFORE the evidence was recorded,
    so a presentation setting silently decided whether a scan was citable."""
    from jcodemunch_mcp import server

    out = await server.call_tool(
        "search_text",
        {
            "repo": dispatch_repo["repo"],
            "query": "qxzvvwyjkkbb",
            "suppress_meta": True,
            "format": "json",
        },
    )
    body = json.loads(_content(out)[0].text)
    carrier = (body.get("_meta") or {}).get("absence_evidence") or {}
    assert carrier.get("citable") is True
    assert carrier["ref"].startswith("absent:")
