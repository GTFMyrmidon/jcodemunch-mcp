"""Both fusion exits record the ledger features they were measuring (v1.108.187).

1.108.186 fixed the `semantic_used` column on `_get_ranked_context_fusion` and
reported two adjacent gaps rather than folding them in. This is the first of them.

That exit passed no `extract_ledger_features` payload at all, so `top1_score`,
`top2_score` and `identity_hit` were recorded as their defaults on every row it ever
wrote — and three of regret's six signals read those columns. `search_symbols_fusion`
had the narrower half of the same defect: it passed the features but built their input
without an `identity` key, so `identity_hit` was False there too regardless of what
the identity channel found. Both fusion producers are fixed here, because shipping one
honest and one false would have guaranteed a third release on the same field.

**The two halves are NOT equally repairable, and that asymmetry is the point.** For
get_ranked_context the pre-fix rows are exactly identifiable — a row that returned
symbols while recording no `top1_score` can only have come from the exit that passed
no features. For `search_symbols_fusion` there is no such discriminator: pre-fix
`identity_hit` is always 0, and 0 is also an honest post-fix answer. No version or
timestamp column exists to bound it by, so its history is unseparable and the recency
window is the only remedy. A heuristic there would be worse than the gap.

Helper names are prefixed ``_r187``; the replay fixture queries ``analyze_perf``,
``build_identity_channel``, ``attach_confidence`` and ``_State``, so no helper here
carries any of those names.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from jcodemunch_mcp import config as _r187_config
from jcodemunch_mcp.retrieval import ledger_trust as _r187_trust
from jcodemunch_mcp.retrieval import regret as _r187_regret
from jcodemunch_mcp.retrieval.confidence import attach_confidence as _r187_conf
from jcodemunch_mcp.storage import token_tracker as _r187_tt
from jcodemunch_mcp.tools import analyze_perf as _r187_perf_mod
from jcodemunch_mcp.tools.get_ranked_context import get_ranked_context
from jcodemunch_mcp.tools.search_symbols import search_symbols

_R187_GRC = "get_ranked_context_fusion"
_R187_SS = "search_symbols_fusion"


# --- fixtures ---------------------------------------------------------------- #


@pytest.fixture()
def _r187_env(tmp_path, monkeypatch):
    """A real folder index plus an isolated ledger with telemetry forced on.

    ⚠ `_loaded = True` is load-bearing: `record_savings` calls
    `_ensure_loaded(None)` on the first savings write, which repins the perf db to
    `~/.code-index` even when the tool was handed a `storage_path`.
    """
    store = tmp_path / "idx"
    monkeypatch.setenv("CODE_INDEX_PATH", str(store))
    proj = tmp_path / "proj"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "a.py").write_text(
        "def refresh_token(user):\n"
        "    '''Rotate a template credential.'''\n"
        "    return user\n",
        encoding="utf-8",
    )
    (proj / "src" / "b.py").write_text(
        "def render_html(template):\n    '''Expand a template.'''\n    return template\n",
        encoding="utf-8",
    )
    from jcodemunch_mcp.tools.index_folder import index_folder

    repo = index_folder(path=str(proj), use_ai_summaries=False, storage_path=str(store))["repo"]

    # v1.108.188: telemetry rows now follow the caller's storage_path, so the ledger
    # IS the store. A separate directory only captured rows while the writers
    # ignored it.
    ledger = store
    fresh = _r187_tt._State()
    fresh._base_path = str(ledger)
    fresh._loaded = True
    monkeypatch.setattr(_r187_tt, "_state", fresh)

    real_get = _r187_config.get

    def _patched(key, default=None, *args, **kwargs):
        if key == "perf_telemetry_enabled":
            return True
        return real_get(key, default, *args, **kwargs)

    monkeypatch.setattr(_r187_config, "get", _patched)
    return {"repo": repo, "storage": str(store), "ledger": str(ledger), "state": fresh}


def _r187_ledger_rows(base: str) -> list:
    con = sqlite3.connect(str(Path(base) / "telemetry.db"))
    try:
        return con.execute(
            "SELECT tool, top1_score, top2_score, identity_hit, returned_ids "
            "FROM ranking_events"
        ).fetchall()
    finally:
        con.close()


def _r187_only(base: str, tool: str) -> tuple:
    rows = [r for r in _r187_ledger_rows(base) if r[0] == tool]
    assert rows, f"no ranking event recorded for {tool}"
    return rows[0]


def _r187_row(tool: str, top1, ident: int, *, returned: str = '["a"]', conf=0.9, sem=1) -> tuple:
    """A ranking_events row in the shape ranking_db_query SELECTs."""
    return (0.0, "acme/widget", tool, "qh", "q", returned, top1, None, conf, sem, ident, 0)


# --- the features, through the real route ------------------------------------ #


class TestGetRankedContextFusionRecordsWhatItMeasured:
    def test_top_scores_are_recorded(self, _r187_env):
        out = get_ranked_context(
            _r187_env["repo"], query="template", token_budget=4000,
            fusion=True, storage_path=_r187_env["storage"],
        )
        assert out["items_included"] >= 2
        _, top1, top2, _, returned = _r187_only(_r187_env["ledger"], _R187_GRC)
        assert top1 is not None, "returned rows but recorded no top score"
        assert top2 is not None
        assert top1 >= top2
        assert len(json.loads(returned)) == out["items_included"]

    def test_an_identity_match_is_recorded(self, _r187_env):
        """`refresh_token` is a symbol name, so the identity channel scores it."""
        get_ranked_context(
            _r187_env["repo"], query="refresh_token", token_budget=4000,
            fusion=True, storage_path=_r187_env["storage"],
        )
        assert _r187_only(_r187_env["ledger"], _R187_GRC)[3] == 1

    def test_no_identity_match_is_also_recorded(self, _r187_env):
        """Non-vacuity for the column: `template` matches lexically and appears in
        no symbol NAME, so a hardcoded True would fail here."""
        out = get_ranked_context(
            _r187_env["repo"], query="template", token_budget=4000,
            fusion=True, storage_path=_r187_env["storage"],
        )
        assert out["items_included"] >= 1
        assert _r187_only(_r187_env["ledger"], _R187_GRC)[3] == 0

    def test_the_row_is_no_longer_featureless(self, _r187_env):
        """The rows this release stops writing are exactly the ones the trust
        predicate refuses. A post-fix row must not match its own guard."""
        get_ranked_context(
            _r187_env["repo"], query="template", token_budget=4000,
            fusion=True, storage_path=_r187_env["storage"],
        )
        rows = _r187_tt.ranking_db_query(base_path=_r187_env["ledger"])
        grc = [r for r in rows if r[2] == _R187_GRC]
        assert grc and all(_r187_trust.identity_label_is_trustworthy(r) for r in grc)


class TestSearchSymbolsFusionRecordsIdentity:
    def test_an_identity_match_is_recorded(self, _r187_env):
        """This exit always passed top scores and never an `identity` key, so
        `identity_hit` was False on every fusion row it wrote."""
        search_symbols(
            _r187_env["repo"], query="refresh_token", fusion=True,
            storage_path=_r187_env["storage"],
        )
        assert _r187_only(_r187_env["ledger"], _R187_SS)[3] == 1

    def test_no_identity_match_is_also_recorded(self, _r187_env):
        search_symbols(
            _r187_env["repo"], query="template", fusion=True,
            storage_path=_r187_env["storage"],
        )
        assert _r187_only(_r187_env["ledger"], _R187_SS)[3] == 0

    def test_confidence_would_move_if_the_inputs_were_shared(self):
        """⚠ Why the ledger input is SEPARATE from the confidence input. This was
        found by a failing test, not by reading: `compute_confidence` sniffs the same
        `identity` key when no `has_identity_match` is passed, scoring it 1.0
        known-true / 0.7 unknown / 0.6 known-false. Sharing one input would move the
        confidence of every fusion search, which is a ranking-adjacent change and not
        what "record the feature" means."""
        scores = [{"score": 9.0}, {"score": 4.0}, {"score": 1.0}]
        with_identity = [dict(r, identity=50.0) for r in scores]
        plain: dict = {"_meta": {}}
        enriched: dict = {"_meta": {}}
        _r187_conf(plain, scores, is_stale=False)
        _r187_conf(enriched, with_identity, is_stale=False)
        assert plain["_meta"]["confidence"] != enriched["_meta"]["confidence"]

    def test_only_the_ledger_sees_the_identity_key(self, _r187_env, monkeypatch):
        """The invariant that keeps the confidence number still, pinned behaviourally
        rather than by reading the source: `attach_confidence` must receive rows
        WITHOUT an identity key on the same call where the ledger features receive
        rows WITH one. Both exits import these from the confidence module inside the
        function body, so patching the module reaches the real calls."""
        from jcodemunch_mcp.retrieval import confidence as _r187_conf_mod

        seen: dict = {"conf": [], "feat": []}
        real_conf = _r187_conf_mod.attach_confidence
        real_feat = _r187_conf_mod.extract_ledger_features

        def _spy_conf(result, rows, **kw):
            seen["conf"].append([dict(r) for r in rows])
            return real_conf(result, rows, **kw)

        def _spy_feat(rows):
            seen["feat"].append([dict(r) for r in rows])
            return real_feat(rows)

        monkeypatch.setattr(_r187_conf_mod, "attach_confidence", _spy_conf)
        monkeypatch.setattr(_r187_conf_mod, "extract_ledger_features", _spy_feat)

        search_symbols(
            _r187_env["repo"], query="refresh_token", fusion=True,
            storage_path=_r187_env["storage"],
        )
        get_ranked_context(
            _r187_env["repo"], query="refresh_token", token_budget=4000,
            fusion=True, storage_path=_r187_env["storage"],
        )

        conf_rows = [r for call in seen["conf"] for r in call]
        feat_rows = [r for call in seen["feat"] for r in call]
        assert conf_rows and feat_rows
        assert not any("identity" in r for r in conf_rows), (
            "an identity key reached attach_confidence, which moves the confidence "
            "of every fusion search"
        )
        assert any(r.get("identity") for r in feat_rows), (
            "the ledger features never saw an identity score"
        )


# --- the predicate, and the asymmetry --------------------------------------- #


class TestIdentityLabelTrust:
    def test_a_featureless_row_that_returned_rows_is_not_evidence(self):
        assert not _r187_trust.identity_label_is_trustworthy(
            _r187_row(_R187_GRC, None, 0)
        )

    def test_a_row_with_a_top_score_is_evidence(self):
        assert _r187_trust.identity_label_is_trustworthy(
            _r187_row(_R187_GRC, 12.5, 0)
        )

    def test_a_zero_result_row_is_evidence(self):
        """A post-fix empty result records no top score either, so the rule must
        not reach it — `returned_ids` is what separates the two."""
        assert _r187_trust.identity_label_is_trustworthy(
            _r187_row(_R187_GRC, None, 0, returned="[]")
        )

    def test_search_symbols_fusion_history_is_not_separable(self):
        """⚠ Deliberate gap, stated rather than papered over. Its pre-fix rows carry
        top scores and `identity_hit=0`, which is indistinguishable from an honest
        post-fix row that had no identity match. Inventing a discriminator here
        would discard good evidence forever."""
        assert _r187_trust.identity_label_is_trustworthy(_r187_row(_R187_SS, 9.0, 0))
        assert _r187_trust.identity_label_is_trustworthy(_r187_row(_R187_SS, None, 0))

    def test_other_tools_are_untouched(self):
        for tool in ("search_symbols", "get_ranked_context", "plan_turn"):
            assert _r187_trust.identity_label_is_trustworthy(_r187_row(tool, None, 0)), tool

    def test_a_short_row_is_trusted(self):
        assert _r187_trust.identity_label_is_trustworthy((0.0, "acme/widget"))
        assert _r187_trust.identity_label_is_trustworthy(())

    def test_the_two_rules_are_independent(self):
        """A row can have an honest semantic label and a featureless identity one."""
        row = _r187_row(_R187_GRC, None, 0, sem=0)
        assert _r187_trust.semantic_label_is_trustworthy(row)
        assert not _r187_trust.identity_label_is_trustworthy(row)


# --- regret ----------------------------------------------------------------- #


def _r187_seed(state, rows: list, *, query: str = "same-question"):
    """Insert rows described as (tool, top1, top2, identity_hit, n_returned)."""
    for tool, top1, top2, ident, n in rows:
        state.record_ranking_event(
            tool=tool, repo="acme/widget", query=query,
            returned_ids=[f"id{i}" for i in range(n)],
            top1_score=top1, top2_score=top2, confidence=0.90,
            semantic_used=True, identity_hit=bool(ident),
        )


class TestRegretIgnoresFeaturelessRows:
    def test_the_semantic_rule_already_covers_the_vocabulary_gap_signal(self, _r187_env):
        """⚠ Found by a failing non-vacuity test: an identity guard on this signal is
        UNREACHABLE. The signal requires `semantic_used`, and a
        get_ranked_context_fusion row with semantic_used=1 is already refused by the
        v1.108.186 rule — so these rows are suppressed either way, and adding a second
        guard would have read like protection without being any."""
        _r187_seed(_r187_env["state"], [(_R187_GRC, None, None, 0, 2)] * 4)
        out = _r187_regret.analyze_regret(
            "acme/widget", storage_path=_r187_env["ledger"], all_time=True
        )
        assert "vocabulary_gap" not in {c["signal"] for c in out["clusters"]}
        assert out["events_semantic_label_unknown"] == 4
        assert out["events_without_ledger_features"] == 4

    def test_a_featureless_single_result_is_not_thin(self, _r187_env):
        """`top1 is None` counted as thin, which reads a MISSING measurement as a
        weak one."""
        _r187_seed(_r187_env["state"], [(_R187_GRC, None, None, 0, 1)] * 3)
        out = _r187_regret.analyze_regret(
            "acme/widget", storage_path=_r187_env["ledger"], all_time=True
        )
        assert "thin_result" not in {c["signal"] for c in out["clusters"]}

    def test_a_genuinely_scoreless_single_result_is_still_thin(self, _r187_env):
        """Behaviour preserved for every other producer: only the known-featureless
        rows are skipped, not every row with a null top score."""
        _r187_seed(_r187_env["state"], [("search_symbols", None, None, 0, 1)] * 3)
        out = _r187_regret.analyze_regret(
            "acme/widget", storage_path=_r187_env["ledger"], all_time=True
        )
        assert "thin_result" in {c["signal"] for c in out["clusters"]}

    def test_an_empty_result_is_still_thin(self, _r187_env):
        """A zero-result row is thin on its own evidence, whatever its features."""
        _r187_seed(_r187_env["state"], [(_R187_GRC, None, None, 0, 0)] * 3)
        out = _r187_regret.analyze_regret(
            "acme/widget", storage_path=_r187_env["ledger"], all_time=True
        )
        assert "thin_result" in {c["signal"] for c in out["clusters"]}

    def test_a_weak_scored_single_result_is_still_thin(self, _r187_env):
        _r187_seed(_r187_env["state"], [(_R187_GRC, 0.01, None, 0, 1)] * 3)
        out = _r187_regret.analyze_regret(
            "acme/widget", storage_path=_r187_env["ledger"], all_time=True
        )
        assert "thin_result" in {c["signal"] for c in out["clusters"]}

    def test_a_clean_ledger_keeps_the_old_shape(self, _r187_env):
        _r187_seed(_r187_env["state"], [("search_symbols", 9.0, 4.0, 1, 3)] * 3)
        out = _r187_regret.analyze_regret(
            "acme/widget", storage_path=_r187_env["ledger"], all_time=True
        )
        assert "events_without_ledger_features" not in out


# --- analyze_perf ----------------------------------------------------------- #


class TestPerfLedgerSummary:
    def test_a_default_is_not_counted_as_an_identity_miss(self):
        rows = [_r187_row(_R187_GRC, None, 0) for _ in range(3)]
        rows += [_r187_row("search_symbols", 9.0, 1) for _ in range(2)]
        by_repo = _r187_perf_mod._ledger_summary(rows, top=10)["by_repo"][0]
        assert by_repo["identity_hits"] == 2
        assert by_repo["no_ledger_features"] == 3

    def test_a_clean_ledger_keeps_the_old_shape(self):
        rows = [_r187_row("search_symbols", 9.0, 1), _r187_row("search_symbols", 4.0, 0)]
        by_repo = _r187_perf_mod._ledger_summary(rows, top=10)["by_repo"][0]
        assert "no_ledger_features" not in by_repo
        assert by_repo["identity_hits"] == 1

    def test_a_pre_fix_ledger_is_still_readable(self, _r187_env):
        """The rows are history. Nothing here rewrites or drops them."""
        _r187_seed(_r187_env["state"], [(_R187_GRC, None, None, 0, 2)] * 5)
        rows = _r187_tt.ranking_db_query(base_path=_r187_env["ledger"], repo="acme/widget")
        assert len(rows) == 5
        assert all(r[6] is None and r[10] == 0 for r in rows), "history must not be rewritten"
