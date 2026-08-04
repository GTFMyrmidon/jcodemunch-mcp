"""Tests for the offloadable-work criterion (retrieval/offload.py).

The load-bearing tests here are the negative ones. A criterion that labels
generously is worse than no criterion at all, because a false ``offloadable``
routes real work to a model that will confabulate over whatever gap we failed
to notice. Every test that asserts a disqualifier FIRES is protecting that.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jcodemunch_mcp.retrieval import offload
from jcodemunch_mcp.retrieval.offload import (
    CRITERION_VERSION,
    MODE_IDENTITY,
    MODE_RANKED,
    NOT_EVALUATED,
    NOT_OFFLOADABLE,
    OFFLOADABLE,
    EvidenceShape,
    classify,
    shape_from_response,
)


def _clean(**overrides) -> EvidenceShape:
    """A shape that classifies as offloadable, so a test can spoil one field."""
    base = dict(
        unit_count=3,
        container_count=1,
        bodies_present=True,
        truncated=False,
        retrieval_mode=MODE_IDENTITY,
        freshness="fresh",
        coverage_complete=True,
        verdict_state="ok",
        has_absence_claim=False,
        graph_traversal=False,
        verify_with={"tool": "check_references", "args": {"identifier": "x"}},
    )
    base.update(overrides)
    return EvidenceShape(**base)


# --- the control ----------------------------------------------------------


def test_clean_shape_is_offloadable():
    v = classify(_clean())
    assert v["offloadable"] == OFFLOADABLE
    assert offload.Q_EXTRACTIVE in v["qualifiers"]
    assert offload.Q_SELF_CONTAINED in v["qualifiers"]
    assert offload.Q_SINGLE_CONTAINER in v["qualifiers"]
    assert offload.Q_IDENTITY_LOOKUP in v["qualifiers"]
    assert v["disqualifiers"] == []
    assert v["criterion_version"] == CRITERION_VERSION


# --- tri-state discipline -------------------------------------------------


def test_no_payload_is_not_evaluated_not_negative():
    """The distinction the whole design rests on.

    A classifier that was never given a payload must not render as a negative
    verdict. Same reason an unmeasured signal must never score as zero.
    """
    v = classify(EvidenceShape())
    assert v["offloadable"] == NOT_EVALUATED
    assert v["disqualifiers"] == []


def test_not_evaluated_is_distinguishable_from_not_offloadable():
    assert NOT_EVALUATED != NOT_OFFLOADABLE
    empty = classify(EvidenceShape())["offloadable"]
    spoiled = classify(_clean(freshness="stale"))["offloadable"]
    assert empty != spoiled


def test_empty_result_set_is_not_offloadable():
    v = classify(_clean(unit_count=0))
    assert v["offloadable"] == NOT_OFFLOADABLE


def test_empty_result_set_with_absence_claim_names_it():
    v = classify(_clean(unit_count=0, has_absence_claim=True))
    assert v["offloadable"] == NOT_OFFLOADABLE
    assert offload.D_ABSENCE_CLAIM in v["disqualifiers"]


# --- evidence integrity disqualifiers -------------------------------------


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({"freshness": "stale"}, offload.D_STALE_INDEX),
        ({"freshness": "unknown"}, offload.D_TRI_STATE_UNKNOWN),
        ({"freshness": None}, offload.D_TRI_STATE_UNKNOWN),
        (
            {"coverage_complete": False, "retrieval_mode": MODE_RANKED, "rank_margin": 0.9},
            offload.D_INCOMPLETE_COVERAGE,
        ),
        (
            {"coverage_complete": None, "retrieval_mode": MODE_RANKED, "rank_margin": 0.9},
            offload.D_TRI_STATE_UNKNOWN,
        ),
        ({"verdict_state": "degraded"}, offload.D_DEGRADED_VERDICT),
        ({"has_absence_claim": True}, offload.D_ABSENCE_CLAIM),
        ({"bodies_present": False}, offload.D_NO_BODIES),
        ({"bodies_present": None}, offload.D_TRI_STATE_UNKNOWN),
        ({"truncated": True}, offload.D_TRUNCATED),
        ({"graph_traversal": True}, offload.D_GRAPH_TRAVERSAL),
        ({"container_count": 9}, offload.D_CROSS_CONTAINER_SYNTHESIS),
    ],
)
def test_each_disqualifier_fires_alone(overrides, expected):
    v = classify(_clean(**overrides))
    assert v["offloadable"] == NOT_OFFLOADABLE
    assert expected in v["disqualifiers"]


def test_unrecognised_freshness_reading_fails_closed():
    """A future freshness vocabulary must not silently read as acceptable."""
    v = classify(_clean(freshness="probably_fine"))
    assert v["offloadable"] == NOT_OFFLOADABLE
    assert offload.D_TRI_STATE_UNKNOWN in v["disqualifiers"]


def test_not_tracked_is_acceptable_freshness():
    """A repo outside git cannot be stale relative to a HEAD it does not have."""
    v = classify(_clean(freshness="not_tracked"))
    assert v["offloadable"] == OFFLOADABLE


# --- the identity / ranked distinction ------------------------------------


def test_identity_lookup_needs_no_rank_margin():
    """N/A is not Unknown.

    A caller that named the subject by id has no ranking to be ambiguous
    about. Scoring its absent margin as Unknown would disqualify the single
    cleanest case the criterion has.
    """
    v = classify(_clean(retrieval_mode=MODE_IDENTITY, rank_margin=None))
    assert v["offloadable"] == OFFLOADABLE
    assert offload.Q_IDENTITY_LOOKUP in v["qualifiers"]


def test_ranked_retrieval_without_a_margin_fails_closed():
    v = classify(_clean(retrieval_mode=MODE_RANKED, rank_margin=None))
    assert v["offloadable"] == NOT_OFFLOADABLE
    assert offload.D_TRI_STATE_UNKNOWN in v["disqualifiers"]


def test_narrow_rank_margin_disqualifies():
    v = classify(_clean(retrieval_mode=MODE_RANKED, rank_margin=0.01))
    assert v["offloadable"] == NOT_OFFLOADABLE
    assert offload.D_NARROW_RANK_MARGIN in v["disqualifiers"]


def test_wide_rank_margin_qualifies():
    v = classify(_clean(retrieval_mode=MODE_RANKED, rank_margin=0.9))
    assert v["offloadable"] == OFFLOADABLE
    assert offload.Q_WIDE_RANK_MARGIN in v["qualifiers"]


def test_unstated_mode_fails_closed():
    """Without a mode we cannot tell N/A from Unknown, so we must refuse."""
    v = classify(_clean(retrieval_mode=None))
    assert v["offloadable"] == NOT_OFFLOADABLE
    assert offload.D_TRI_STATE_UNKNOWN in v["disqualifiers"]


def test_margin_exactly_at_threshold_qualifies():
    v = classify(
        _clean(retrieval_mode=MODE_RANKED, rank_margin=offload.MIN_RANK_MARGIN)
    )
    assert v["offloadable"] == OFFLOADABLE


# --- coverage applicability -----------------------------------------------
#
# The criterion's first MEASURED defect. Requiring corpus coverage on every
# payload disqualified 100% of both harness arms on TRI_STATE_UNKNOWN, because
# ``verdict.coverage`` ships only on the absence-capable tools. These tests pin
# the fix in both directions, because loosening it to "never check coverage"
# would let an incomplete ranked scan label offloadable.


def test_identity_lookup_does_not_require_corpus_coverage():
    """Whether some other file went unindexed cannot make THIS body wrong."""
    v = classify(_clean(retrieval_mode=MODE_IDENTITY, coverage_complete=None))
    assert v["offloadable"] == OFFLOADABLE


def test_ranked_retrieval_does_require_corpus_coverage():
    """A ranked ordering implicitly claims these are the best in the corpus."""
    v = classify(
        _clean(retrieval_mode=MODE_RANKED, rank_margin=0.9, coverage_complete=None)
    )
    assert v["offloadable"] == NOT_OFFLOADABLE
    assert offload.D_TRI_STATE_UNKNOWN in v["disqualifiers"]


def test_absence_claim_requires_corpus_coverage_even_on_identity_mode():
    """An absence claim is a corpus claim no matter how it was reached."""
    shape = _clean(
        retrieval_mode=MODE_IDENTITY, coverage_complete=False, has_absence_claim=True
    )
    v = classify(shape)
    assert offload.D_INCOMPLETE_COVERAGE in v["disqualifiers"]


def test_incomplete_coverage_still_blocks_a_ranked_payload():
    v = classify(
        _clean(retrieval_mode=MODE_RANKED, rank_margin=0.9, coverage_complete=False)
    )
    assert offload.D_INCOMPLETE_COVERAGE in v["disqualifiers"]


# --- verdict hygiene ------------------------------------------------------


def test_repeated_unknowns_collapse_to_one_code():
    """Three unknowns are not three times as unknown.

    The count is not the signal; the presence of any is. A caller reading the
    list must not infer severity from its length.
    """
    v = classify(_clean(freshness=None, coverage_complete=None, bodies_present=None))
    assert v["disqualifiers"].count(offload.D_TRI_STATE_UNKNOWN) == 1


def test_negative_verdict_carries_no_qualifiers():
    """A partial positive reads as encouragement. It must not be published."""
    v = classify(_clean(freshness="stale"))
    assert v["offloadable"] == NOT_OFFLOADABLE
    assert v["qualifiers"] == []


def test_negative_verdict_carries_no_verify_hook():
    v = classify(_clean(graph_traversal=True))
    assert v["verify_with"] is None


def test_positive_verdict_carries_the_verify_hook():
    """The hook is what stops the label requiring trust in us."""
    v = classify(_clean())
    assert v["verify_with"]["tool"] == "check_references"


def test_multiple_disqualifiers_are_all_reported():
    v = classify(_clean(freshness="stale", graph_traversal=True, truncated=True))
    assert offload.D_STALE_INDEX in v["disqualifiers"]
    assert offload.D_GRAPH_TRAVERSAL in v["disqualifiers"]
    assert offload.D_TRUNCATED in v["disqualifiers"]


def test_shape_is_always_reported_even_on_refusal():
    v = classify(_clean(unit_count=7, container_count=4))
    assert v["shape"] == {"units": 7, "containers": 4}


# --- shape_from_response --------------------------------------------------


def _response(**meta_overrides) -> dict:
    verdict = {
        "state": "ok",
        "channels": {"index": "fresh"},
        "coverage": {"complete": True},
    }
    meta = {"verdict": verdict, "repo_freshness": "fresh"}
    meta.update(meta_overrides)
    return {
        "symbols": [
            {"id": "a.py::f#function", "file": "a.py", "source": "def f(): pass"},
            {"id": "a.py::g#function", "file": "a.py", "source": "def g(): pass"},
        ],
        "_meta": meta,
    }


def test_shape_from_response_reads_a_batch_payload():
    s = shape_from_response(_response(), retrieval_mode=MODE_IDENTITY)
    assert s.unit_count == 2
    assert s.container_count == 1
    assert s.bodies_present is True
    assert s.truncated is False
    assert s.freshness == "fresh"
    assert s.coverage_complete is True
    assert s.verdict_state == "ok"
    assert classify(s)["offloadable"] == OFFLOADABLE


def test_shape_from_response_reads_a_flat_single_symbol_payload():
    """get_symbol_source is shape-follows-input: one id returns a flat object."""
    flat = {
        "id": "a.py::f#function",
        "file": "a.py",
        "source": "def f(): pass",
        "_meta": {
            "repo_freshness": "fresh",
            "verdict": {"state": "ok", "coverage": {"complete": True}},
        },
    }
    s = shape_from_response(flat, retrieval_mode=MODE_IDENTITY)
    assert s.unit_count == 1
    assert s.bodies_present is True


def test_shape_from_response_leaves_unestablished_fields_none():
    """A default here would be indistinguishable from a measurement."""
    s = shape_from_response({"symbols": []}, retrieval_mode=MODE_IDENTITY)
    assert s.freshness is None
    assert s.coverage_complete is None
    assert s.bodies_present is None
    assert classify(s)["offloadable"] == NOT_OFFLOADABLE


def test_shape_from_response_detects_a_missing_body():
    r = _response()
    r["symbols"][1]["source"] = ""
    s = shape_from_response(r, retrieval_mode=MODE_IDENTITY)
    assert s.bodies_present is False
    assert offload.D_NO_BODIES in classify(s)["disqualifiers"]


def test_shape_from_response_detects_truncation():
    r = _response()
    r["symbols"][0]["source_truncated"] = True
    s = shape_from_response(r, retrieval_mode=MODE_IDENTITY)
    assert s.truncated is True
    assert offload.D_TRUNCATED in classify(s)["disqualifiers"]


def test_shape_from_response_counts_distinct_containers():
    r = _response()
    r["symbols"][1]["file"] = "b.py"
    s = shape_from_response(r, retrieval_mode=MODE_IDENTITY)
    assert s.container_count == 2


def test_shape_from_response_reads_per_entry_freshness_buckets():
    """One stale entry taints the payload; the aggregate must not flatten it."""
    r = _response()
    r["_meta"].pop("repo_freshness")
    r["_meta"]["freshness"] = {"fresh": 1, "stale_index": 1}
    s = shape_from_response(r, retrieval_mode=MODE_IDENTITY)
    assert s.freshness == "stale"


def test_shape_from_response_falls_back_to_the_verdict_index_channel():
    r = _response()
    r["_meta"].pop("repo_freshness")
    r["_meta"]["verdict"]["channels"]["index"] = "stale"
    s = shape_from_response(r, retrieval_mode=MODE_IDENTITY)
    assert s.freshness == "stale"


def test_shape_from_response_reads_the_rank_margin_from_confidence_components():
    """The margin is reused from compute_confidence, never re-derived."""
    r = _response(confidence_components={"gap": 0.8, "strength": 0.9})
    s = shape_from_response(r, retrieval_mode=MODE_RANKED)
    assert s.rank_margin == 0.8
    assert classify(s)["offloadable"] == OFFLOADABLE


def test_a_degraded_response_never_labels_offloadable():
    r = _response()
    r["_meta"]["verdict"]["state"] = "degraded"
    s = shape_from_response(r, retrieval_mode=MODE_IDENTITY)
    assert classify(s)["offloadable"] == NOT_OFFLOADABLE


# --- the harness falsifier is non-vacuous ---------------------------------
#
# The benchmark's falsifier reported 0 false-offloadables on its first clean
# run. A falsifier that has never fired is indistinguishable from one that
# cannot fire, so these prove it can.


def _bench():
    import importlib.util

    path = (
        Path(__file__).resolve().parents[1]
        / "benchmarks"
        / "offload"
        / "run_offload_criterion.py"
    )
    spec = importlib.util.spec_from_file_location("_offload_bench", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _falsifier():
    return _bench()._ground_truth_present


class _FakeIndex:
    def __init__(self, sym):
        self._sym = sym

    def get_symbol(self, sid):
        return self._sym if self._sym and self._sym.get("id") == sid else None


BODY = "def f():\n    pass"


def test_falsifier_passes_a_genuinely_complete_body():
    check = _falsifier()
    sym = {"id": "a.py::f#function", "line": 1, "end_line": 2, "byte_length": len(BODY)}
    ok, reason = check({"source": BODY}, "a.py::f#function", _FakeIndex(sym))
    assert ok, reason


@pytest.mark.parametrize(
    "payload,label",
    [
        ({}, "missing source field"),
        ({"source": ""}, "empty body"),
        ({"source": "   \n  "}, "whitespace-only body"),
        ({"source": "def f():"}, "body shorter than the indexed byte extent"),
    ],
)
def test_falsifier_catches_a_bad_payload(payload, label):
    check = _falsifier()
    sym = {"id": "a.py::f#function", "line": 1, "end_line": 4, "byte_length": len(BODY)}
    ok, reason = check(payload, "a.py::f#function", _FakeIndex(sym))
    assert not ok, f"falsifier missed: {label}"
    assert reason


def test_falsifier_does_not_flag_an_overshooting_end_line():
    """The false positive that the FastAPI run surfaced.

    jCodeMunch's TOML extractor sets ``end_line`` to the first line of the NEXT
    table, so a complete body reads as short against a line-derived
    expectation. The byte extent carries no such assumption. Regression-pinned
    with the real shape from fastapi/pyproject.toml at a64dfbbd.
    """
    check = _falsifier()
    body = '[build-system]\r\nrequires = ["pdm-backend"]\r\nbuild-backend = "pdm.backend"\r\n\r\n'
    sym = {
        "id": "pyproject.toml::build-system#type",
        "line": 1,
        "end_line": 5,  # points at `[project]`, not at this table's last line
        "byte_length": len(body.encode("utf-8")),
    }
    ok, reason = check({"source": body}, sym["id"], _FakeIndex(sym))
    assert ok, f"complete TOML body wrongly falsified: {reason}"


def test_falsifier_still_catches_truncation_without_a_byte_length():
    """The line fallback must stay live for indexes carrying no byte extent."""
    check = _falsifier()
    sym = {"id": "a.py::f#function", "line": 1, "end_line": 6}
    ok, reason = check({"source": "def f():"}, "a.py::f#function", _FakeIndex(sym))
    assert not ok
    assert "no byte_length" in reason


def test_falsifier_catches_a_symbol_missing_from_the_index():
    check = _falsifier()
    ok, reason = check({"source": "x = 1"}, "gone.py::x#constant", _FakeIndex(None))
    assert not ok
    assert "vanished" in reason


# --- suite-parity guard ---------------------------------------------------


def test_vocabulary_is_domain_neutral():
    """jdoc and jdata carry this identical contract or none of them do.

    A code naming symbols or files here would have to be re-cut for sections
    or columns, and under the 1.x no-removal contract we would live with both.
    """
    codes = [
        v
        for k, v in vars(offload).items()
        if (k.startswith("D_") or k.startswith("Q_")) and isinstance(v, str)
    ]
    assert codes, "no reason codes found: the introspection is wrong"
    banned = ("SYMBOL", "FILE", "SECTION", "DOCUMENT", "COLUMN", "DATASET", "CODE")
    for code in codes:
        for word in banned:
            assert word not in code, f"{code} is domain-specific: contains {word}"


def test_shape_field_names_are_domain_neutral():
    fields = set(EvidenceShape.__dataclass_fields__)
    assert "unit_count" in fields and "container_count" in fields
    for banned in ("symbol_count", "file_count", "section_count", "column_count"):
        assert banned not in fields


# --- the ranked arm's query derivation ------------------------------------
#
# The fixed query list this replaced was jCodeMunch's own vocabulary, so on any
# other corpus it partly measured whether those words happened to exist there.
# These pin the properties that make the derived list a measurement instead.


class _NamedIndex:
    """Minimal stand-in: the derivation reads only `.symbols[*]["name"]`."""

    def __init__(self, names):
        self.symbols = [{"id": f"f.py::{n}#{i}", "name": n} for i, n in enumerate(names)]


def _corpus():
    # 3 names carried once, 1 name carried six times.
    return _NamedIndex(["alpha", "beta", "gamma"] + ["shared"] * 6)


def test_derived_queries_split_into_labelled_classes():
    mod = _bench()
    got = mod._derive_queries(_corpus(), per_class=2)
    by_class = {}
    for q in got:
        by_class.setdefault(q["class"], []).append(q["query"])

    assert set(by_class[mod.CLASS_UNIQUE]) <= {"alpha", "beta", "gamma"}
    assert by_class[mod.CLASS_AMBIGUOUS] == ["shared"]
    assert by_class[mod.CLASS_ABSENT] == list(mod.ABSENT_QUERIES)


def test_derived_queries_are_deterministic():
    """No RNG, for the reason _sample has none: a list that differs per run
    cannot separate a criterion change from a query change."""
    mod = _bench()
    first = mod._derive_queries(_corpus(), per_class=2)
    second = mod._derive_queries(_corpus(), per_class=2)
    assert first == second


def test_derived_queries_come_from_the_corpus_under_test():
    """The whole point: the vocabulary must follow the corpus, not the tool."""
    mod = _bench()
    other = _NamedIndex(["zeta_only_here"] + ["dup"] * 6)
    derived = {
        q["query"] for q in mod._derive_queries(other, per_class=4)
        if q["class"] != mod.CLASS_ABSENT
    }
    assert derived == {"zeta_only_here", "dup"}
    assert "compute_confidence" not in derived


def test_ambiguous_class_respects_its_threshold():
    mod = _bench()
    below = _NamedIndex(["x"] * (mod.AMBIGUOUS_MIN_OWNERS - 1) + ["solo"])
    classes = {q["class"] for q in mod._derive_queries(below, per_class=4)}
    assert mod.CLASS_AMBIGUOUS not in classes


def test_absent_controls_cannot_match_a_real_corpus():
    """They are the ranked arm's non-vacuity check, so they must stay absent.

    If a control ever became a real symbol name the check would silently stop
    checking anything.
    """
    mod = _bench()
    for q in mod.ABSENT_QUERIES:
        assert q.startswith("zzq_no_such_")


def test_empty_corpus_still_yields_the_controls():
    """Degrade, do not fail: an unusable corpus must not abort the arm."""
    mod = _bench()
    got = mod._derive_queries(_NamedIndex([]), per_class=4)
    assert [q["class"] for q in got] == [mod.CLASS_ABSENT] * len(mod.ABSENT_QUERIES)


def test_self_corpus_detection_is_by_path_not_name():
    mod = _bench()
    root = Path(__file__).resolve().parents[1]
    assert mod._is_self_corpus(str(root))
    assert not mod._is_self_corpus(str(root / "benchmarks"))
    assert not mod._is_self_corpus(None)


# --- the wiring on get_symbol_source --------------------------------------
#
# Off by default, never mutates the answer, never raises, and degrades to
# silence when the criterion module is absent. That last one is not
# hypothetical: the module is optional and is not present in every build.


def _gate_on(monkeypatch):
    monkeypatch.setenv(offload.ENV_GATE, "1")


def test_the_annotation_is_off_by_default(monkeypatch):
    monkeypatch.delenv(offload.ENV_GATE, raising=False)
    assert offload.is_enabled() is False
    r = {"source": "x", "_meta": {}}
    offload.annotate(r, retrieval_mode=MODE_IDENTITY)
    assert "offloadable" not in r["_meta"]


@pytest.mark.parametrize("val", ["1", "true", "TRUE", "yes", "on"])
def test_the_gate_accepts_the_usual_truthy_tokens(val):
    assert offload.is_enabled({offload.ENV_GATE: val}) is True


@pytest.mark.parametrize("val", ["", "0", "false", "no", "off", "maybe"])
def test_the_gate_rejects_everything_else(val):
    """Fail closed: an unrecognised token must not switch a feature on."""
    assert offload.is_enabled({offload.ENV_GATE: val}) is False


def test_annotate_adds_the_block_when_gated_on(monkeypatch):
    _gate_on(monkeypatch)
    r = {
        "source": "def f(): pass",
        "file": "a.py",
        "_meta": {"verdict": {"state": "ok", "channels": {"index": "fresh"}}},
    }
    offload.annotate(r, retrieval_mode=MODE_IDENTITY)
    assert r["_meta"]["offloadable"]["offloadable"] == OFFLOADABLE


def test_annotate_never_mutates_the_answer(monkeypatch):
    """It is an advisory note ON someone else's answer, not a rewrite of it."""
    _gate_on(monkeypatch)
    r = {
        "source": "def f(): pass",
        "file": "a.py",
        "name": "f",
        "_meta": {"verdict": {"state": "ok", "channels": {"index": "fresh"}}},
    }
    before = {k: v for k, v in r.items() if k != "_meta"}
    before_verdict = dict(r["_meta"]["verdict"])
    offload.annotate(r, retrieval_mode=MODE_IDENTITY)
    assert {k: v for k, v in r.items() if k != "_meta"} == before
    assert r["_meta"]["verdict"] == before_verdict


def test_annotate_never_raises_on_a_hostile_payload(monkeypatch):
    """A classifier defect must not be able to fail a retrieval that worked."""
    _gate_on(monkeypatch)
    for bad in ({"_meta": "not-a-dict", "source": "x"}, {"source": object()}, {}):
        offload.annotate(bad, retrieval_mode=MODE_IDENTITY)  # must not raise


def test_annotate_creates_meta_when_the_payload_has_none(monkeypatch):
    _gate_on(monkeypatch)
    r = {"source": "x", "file": "a.py"}
    offload.annotate(r, retrieval_mode=MODE_IDENTITY)
    assert "offloadable" in r["_meta"]


def test_not_evaluated_is_emitted_rather_than_silence(monkeypatch):
    """With the gate ON, a missing block is ambiguous with the gate being OFF."""
    _gate_on(monkeypatch)
    r = {"error": "nope", "_meta": {}}
    offload.not_evaluated(r, reason="error payload carries no evidence")
    block = r["_meta"]["offloadable"]
    assert block["offloadable"] == NOT_EVALUATED
    assert block["reason"] == "error payload carries no evidence"
    assert block["criterion_version"] == CRITERION_VERSION


def test_not_evaluated_stays_silent_when_the_gate_is_off(monkeypatch):
    monkeypatch.delenv(offload.ENV_GATE, raising=False)
    r = {"error": "nope", "_meta": {}}
    offload.not_evaluated(r, reason="x")
    assert "offloadable" not in r["_meta"]


def test_the_env_gate_name_is_pinned():
    """It is a user-facing switch; renaming it silently breaks an opt-in."""
    assert offload.ENV_GATE == "JCODEMUNCH_OFFLOADABLE"


def test_get_symbol_source_degrades_when_the_criterion_module_is_absent(monkeypatch):
    """⚠ NOT hypothetical: shipped builds do not contain the criterion module.

    `tools/get_symbol.py` is tracked and `retrieval/offload.py` is not, so the
    import must be allowed to FAIL and degrade to "no annotation". If it ever
    became a hard import, a build without the module would break symbol
    retrieval outright.
    """
    import sys

    from jcodemunch_mcp.tools import get_symbol

    # A `None` entry in sys.modules makes the import machinery raise
    # ImportError, which is exactly the absent-module case. Patching
    # `builtins.__import__` on the module NAME does not work here: the
    # statement is `from ..retrieval import offload`, so "offload" arrives in
    # `fromlist`, not in `name`, and such a patch silently never fires.
    from jcodemunch_mcp import retrieval

    monkeypatch.setenv(offload.ENV_GATE, "1")
    monkeypatch.setitem(sys.modules, "jcodemunch_mcp.retrieval.offload", None)
    # ⚠ The sys.modules sentinel alone is not enough once ANY earlier import
    # has bound `offload` as an attribute of the parent package: `from
    # ..retrieval import offload` then resolves by getattr and never consults
    # sys.modules. This test file imports offload at module scope, so that
    # binding always exists here. Both have to go.
    monkeypatch.delattr(retrieval, "offload", raising=False)
    assert get_symbol._offload() is None


def test_the_absent_module_probe_is_non_vacuous():
    """The test above only means something if the accessor normally succeeds."""
    from jcodemunch_mcp.tools import get_symbol

    assert get_symbol._offload() is not None


def test_the_annotator_is_reached_through_a_single_accessor():
    """One import point, so the absent-module path cannot be half-adopted.

    The same defect class as the three drifted `channels.index` expressions in
    1.108.240: a guard applied at two of three call sites is not a guard.
    """
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1]
        / "src" / "jcodemunch_mcp" / "tools" / "get_symbol.py"
    ).read_text(encoding="utf-8")
    assert src.count("from ..retrieval import offload") == 1
    assert "_mod = _offload()" in src
