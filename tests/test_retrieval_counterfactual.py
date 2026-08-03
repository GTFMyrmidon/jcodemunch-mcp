"""Retrieval counterfactuals — why was this action not proposed?

The load-bearing test is ``TestFidelity``. An explanation that disagrees with
what ``route`` actually did is worse than no explanation: it sends a reader to
tune a stage that was never consulted. So the reconstruction is checked against
``classify_intent`` + ``search_catalog`` on **every** benchmark query, hits and
misses alike, rather than being spot-checked on the interesting ones.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jcodemunch_mcp import counter as _counter
from jcodemunch_mcp.investigator import (
    CATALOG_ABSENT,
    EMPTY_QUERY,
    GATES,
    NO_LEXICAL_OVERLAP,
    RANKED_BELOW_CUTOFF,
    RETURNED,
    RULE_PREEMPTED,
    explain_misses,
    explain_route,
)

RESULTS = Path(__file__).resolve().parents[1] / "benchmarks" / "route_recall" / "results.json"


@pytest.fixture(scope="module")
def catalog():
    from jcodemunch_mcp.server import _catalog_names, _catalog_rows

    return _catalog_rows(), _catalog_names()


@pytest.fixture(scope="module")
def per_query():
    if not RESULTS.exists():
        pytest.skip("route_recall results.json not present")
    return json.loads(RESULTS.read_text(encoding="utf-8"))["per_query"]


def _route_for_real(task: str, rows, names, limit=3) -> list[str]:
    """What `_handle_route` would recommend, reproduced from its own source.

    Kept in the test rather than in the module so the module cannot 'agree with
    itself' — this is the independent side of the comparison.
    """
    recs = [r["action"] for r in _counter.classify_intent(task, names)]
    if not recs:
        recs = [r["action"] for r in _counter.search_catalog(rows, task, limit)]
    return recs


class TestFidelity:
    def test_agrees_with_route_on_every_benchmark_query(self, catalog, per_query):
        rows, names = catalog
        disagreements = []
        for entry in per_query:
            task = entry.get("query", "")
            real = _route_for_real(task, rows, names)
            for target in entry.get("targets") or []:
                exp = explain_route(
                    task, target, catalog_rows=rows, catalog_names=names
                )
                said_returned = exp["outcome"] == RETURNED
                really_returned = target in real
                if said_returned != really_returned:
                    disagreements.append(
                        (task, target, exp["outcome"], real)
                    )
        assert not disagreements, (
            "the explainer contradicts the front door it claims to explain:\n"
            + "\n".join(map(str, disagreements[:5]))
        )

    def test_reported_rank_matches_the_real_rank(self, catalog, per_query):
        rows, names = catalog
        for entry in per_query:
            task = entry.get("query", "")
            real = _route_for_real(task, rows, names)
            for target in entry.get("targets") or []:
                exp = explain_route(task, target, catalog_rows=rows, catalog_names=names)
                if exp["outcome"] == RETURNED:
                    assert exp["rank"] == real.index(target) + 1, task

    def test_every_miss_lands_on_a_named_gate(self, catalog, per_query):
        """The report's falsifier, run: it predicted this would be worth having
        only if misses do NOT reduce to irreducible user ambiguity."""
        rows, names = catalog
        out = explain_misses(per_query, catalog_rows=rows, catalog_names=names)
        assert out["explained"] > 0
        assert set(out["by_gate"]) <= set(GATES)
        assert RETURNED not in out["by_gate"], (
            "a query the harness recorded as a miss cannot explain as returned"
        )


class TestGates:
    def test_an_action_outside_the_catalog_cannot_be_ranked(self, catalog):
        rows, _names = catalog
        exp = explain_route(
            "find the thing", "no_such_action", catalog_rows=rows,
            catalog_names={"search_symbols"},
        )
        assert exp["outcome"] == CATALOG_ABSENT
        assert exp["first_excluding_gate"] == CATALOG_ABSENT
        assert "surface" in exp["experiment"]

    def test_an_all_stopword_query_has_nothing_to_rank(self, catalog):
        rows, names = catalog
        exp = explain_route(
            "what is it for me", "get_symbol_source",
            catalog_rows=rows, catalog_names=names,
        )
        assert exp["outcome"] in (EMPTY_QUERY, NO_LEXICAL_OVERLAP, RULE_PREEMPTED)

    def test_rule_preemption_reports_never_scored(self, catalog):
        """The gate that did not exist before this module.

        `route` runs the lexical fallback ONLY when no rule matched, so an
        action a rule did not name was never scored — and calling that
        'ranked below cutoff' points a reader at weights nothing consulted.

        ⚠ Constructed rather than borrowed from a live miss. The first version
        of this test used "what breaks if I change the POST orders endpoint",
        which v1.108.220 FIXED — so the test broke for the best possible reason
        and had to be rewritten anyway. A gate test must demonstrate the gate,
        not depend on a defect surviving.
        """
        rows, names = catalog
        task = "who calls this"          # fires the callers rule
        assert _counter.classify_intent(task, names), "fixture needs a live rule"
        exp = explain_route(
            task, "get_file_tree", catalog_rows=rows, catalog_names=names
        )
        assert exp["outcome"] == RULE_PREEMPTED
        assert exp["scored"] is False
        assert exp["preempted_by"] in names
        assert "never scored" in exp["experiment"]
        assert "rank" not in exp, "a never-scored action has no rank to report"

    def test_zero_overlap_is_reported_as_unrankable_not_as_a_low_rank(self, catalog):
        rows, names = catalog
        # Constructed: tokens that appear in no action name or description, and
        # trip no curated rule. Borrowing a live miss makes the test rot the
        # moment the miss is fixed.
        task = "zzqqx wwvvk yylmn"
        assert not _counter.classify_intent(task, names)
        exp = explain_route(
            task, "get_symbol_source", catalog_rows=rows, catalog_names=names,
        )
        assert exp["outcome"] == NO_LEXICAL_OVERLAP
        assert exp["score"] == 0.0
        assert exp["rank"] is None
        assert all(c["points"] == 0.0 for c in exp["contributions"]), (
            "zero score with a scoring token would mean the account is wrong"
        )
        assert "no reweighting can rescue it" in exp["experiment"]

    def test_a_hit_names_no_gate(self, catalog):
        rows, names = catalog
        exp = explain_route(
            "where is the function that validates auth tokens", "search_symbols",
            catalog_rows=rows, catalog_names=names,
        )
        assert exp["outcome"] == RETURNED
        assert exp["first_excluding_gate"] is None
        assert "experiment" not in exp


class TestContributions:
    def test_zero_contribution_tokens_are_listed(self, catalog):
        """Dropping the zero rows is what makes a ranking look inexplicable."""
        rows, names = catalog
        # A query mixing a token the target scores on with tokens it does not,
        # and tripping no rule, so the lexical branch is the one under test.
        task = "decorator zzqqx wwvvk"
        assert not _counter.classify_intent(task, names)
        exp = explain_route(
            task, "get_decorator_census", catalog_rows=rows, catalog_names=names,
        )
        assert exp["contributions"]
        assert any(c["points"] == 0.0 for c in exp["contributions"])
        assert {c["token"] for c in exp["contributions"]} == set(exp["query_tokens"])

    def test_contributions_sum_to_the_reported_score(self, catalog):
        """The account must reconcile with `score_action`, or it is fiction."""
        rows, names = catalog
        for task, target in [
            ("show me the call graph for this function", "get_call_hierarchy"),
            ("what imports this file", "find_importers"),
            ("find dead code in the repo", "find_dead_code"),
        ]:
            exp = explain_route(task, target, catalog_rows=rows, catalog_names=names)
            if "contributions" not in exp:
                continue
            total = round(sum(c["points"] for c in exp["contributions"]), 3)
            reported = exp.get("score")
            if reported is not None:
                assert abs(total - reported) < 0.01, (task, total, reported)

    def test_a_loss_shows_who_won_and_why(self, catalog, per_query):
        rows, names = catalog
        out = explain_misses(per_query, catalog_rows=rows, catalog_names=names)
        losses = [e for e in out["explanations"] if e["outcome"] == RANKED_BELOW_CUTOFF]
        if not losses:
            pytest.skip("no ranked_below_cutoff misses in the current artifact")
        for e in losses:
            assert e["rank"] > e["cutoff"]
            assert e["beaten_by"]["score"] >= e["score"]
            assert e["beaten_by"]["contributions"]


class TestCharter:
    def test_not_registered_as_an_mcp_tool(self):
        """Item 3's moratorium. Adding a 92nd action to explain why the 91st is
        hard to reach would be self-refuting."""
        from jcodemunch_mcp.server import _catalog_names

        names = _catalog_names()
        for leaked in ("explain_route", "explain_misses", "retrieval_counterfactual"):
            assert leaked not in names

    def test_explaining_dispatches_nothing(self, catalog, monkeypatch):
        """Read-only: it scores, it does not call the action it is reasoning
        about."""
        import jcodemunch_mcp.server as server

        rows, names = catalog
        called = []
        monkeypatch.setattr(
            server, "call_tool", lambda *a, **k: called.append(a), raising=False
        )
        explain_route(
            "find dead code", "find_dead_code", catalog_rows=rows, catalog_names=names
        )
        assert called == []
