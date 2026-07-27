"""The ranking ledger stops claiming a channel that never ran (v1.108.186).

``_get_ranked_context_fusion`` recorded ``semantic_used=True`` on every row while
building only the lexical, identity and structural channels. Its own
``_meta.channels`` listed the three, and from v1.108.185 its verdict reported
``channels.semantic: "off"`` — so the response and the ledger disagreed about the
same call, and the ledger was the one that was wrong.

**Why this got its own release rather than a one-line edit.** ``WeightTuner``
learns a per-repo ``semantic_weight`` by splitting ledger rows on that column and
comparing mean confidence between the groups. Flipping the flag fixes new rows and
leaves the old ones steering a learned parameter for the whole 90-day recency
window, so the ledger would hold two labels for one operation. Reproduced before
fixing: the mislabelled rows walked ``semantic_weight`` from 0.5 to 0.45 on a
ledger where the honest labels decline to move it at all.

**The historical rows are excluded, not rewritten, and not counted as off.** Those
rows carry ``tool = "get_ranked_context_fusion"``, a value no other producer
writes, so they are exactly identifiable with no heuristic. A one-time corrective
UPDATE was rejected: the tuner has only ever written the ``tuning.jsonc`` sidecar
and ``suggest_corrections`` is read-only by charter, so rewriting a user's
telemetry rows is a charter change, not an implementation detail. Leaving them to
age out was also rejected, because that is learning from a corpus we know is
mislabelled. They become a THIRD answer — ``events_semantic_label_unknown`` —
disclosed by every consumer that reads the column.

Helper names are prefixed ``_r186``; a plain ``_state`` helper once outranked
``token_tracker._State`` for a golden replay query and cost a red CI run. The
replay fixture also queries ``analyze_perf`` and ``build_identity_channel``, so no
helper here carries either name.
"""

from __future__ import annotations

import inspect
import sqlite3
from pathlib import Path

import pytest

from jcodemunch_mcp import config as _r186_config
from jcodemunch_mcp.retrieval import ledger_trust as _r186_trust
from jcodemunch_mcp.retrieval import regret as _r186_regret
from jcodemunch_mcp.retrieval import tuning as _r186_tuning
from jcodemunch_mcp.retrieval.tuning import WeightTuner
from jcodemunch_mcp.storage import token_tracker as _r186_tt
from jcodemunch_mcp.tools import analyze_perf as _r186_perf_mod
from jcodemunch_mcp.tools.get_ranked_context import get_ranked_context

_R186_FUSION_TOOL = "get_ranked_context_fusion"


# --- fixtures ---------------------------------------------------------------- #


@pytest.fixture()
def _r186_ledger(tmp_path, monkeypatch):
    """A real, isolated ranking ledger with telemetry forced on.

    ⚠ ``record_savings`` calls ``_ensure_loaded(None)`` on the first savings write,
    which overwrites ``_base_path`` and pins the perf db to ``~/.code-index`` even
    when the tool was handed a ``storage_path``. Pre-marking the state loaded is
    what keeps a test off the developer's real telemetry.db.
    """
    # v1.108.188: the ledger lives in the STORE, so this must be the same directory
    # `_r186_repo` indexes into. A separate directory only captured rows while
    # telemetry writes ignored the caller's storage_path.
    base = tmp_path / "idx"
    base.mkdir(exist_ok=True)
    fresh = _r186_tt._State()
    fresh._base_path = str(base)
    fresh._loaded = True
    monkeypatch.setattr(_r186_tt, "_state", fresh)

    real_get = _r186_config.get

    def _patched(key, default=None, *args, **kwargs):
        if key == "perf_telemetry_enabled":
            return True
        return real_get(key, default, *args, **kwargs)

    monkeypatch.setattr(_r186_config, "get", _patched)
    return {"base": str(base), "state": fresh}


def _r186_rows(base: str) -> list:
    con = sqlite3.connect(str(Path(base) / "telemetry.db"))
    try:
        return con.execute(
            "SELECT tool, confidence, semantic_used, identity_hit FROM ranking_events"
        ).fetchall()
    finally:
        con.close()


def _r186_seed(
    state, *, tool: str, count: int, confidence: float, semantic: bool,
    query: str = None,
):
    """Seed ledger rows. ``query=None`` gives each row a distinct query_hash;
    regret's per-signal detectors group by query_hash, so a shared ``query`` is
    what a clustering test needs."""
    for i in range(count):
        state.record_ranking_event(
            tool=tool,
            repo="acme/widget",
            query=query if query is not None else f"{tool}-q{i}",
            returned_ids=["a"],
            confidence=confidence,
            semantic_used=semantic,
        )


@pytest.fixture()
def _r186_repo(tmp_path, monkeypatch):
    """A real two-file folder index — a hand-built index has no source root, so
    freshness reads `unknown` and that gate fires before anything under test."""
    store = tmp_path / "idx"
    monkeypatch.setenv("CODE_INDEX_PATH", str(store))
    proj = tmp_path / "proj"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "a.py").write_text(
        "def refresh_token(user):\n    return user\n", encoding="utf-8"
    )
    (proj / "src" / "b.py").write_text(
        "def render_html(template):\n    return template\n", encoding="utf-8"
    )
    from jcodemunch_mcp.tools.index_folder import index_folder

    repo = index_folder(path=str(proj), use_ai_summaries=False, storage_path=str(store))["repo"]
    return {"repo": repo, "storage": str(store)}


# --- the row itself, through the real route ---------------------------------- #


class TestTheLedgerRowMatchesTheChannelsBuilt:
    def test_a_fusion_call_records_semantic_used_false(self, _r186_repo, _r186_ledger):
        """The defect, through the real route: real fusion call, real ledger."""
        out = get_ranked_context(
            _r186_repo["repo"], query="refresh token", token_budget=2000,
            fusion=True, storage_path=_r186_repo["storage"],
        )
        assert out["items_included"] > 0, "need a non-empty result to have a row to check"
        rows = [r for r in _r186_rows(_r186_ledger["base"]) if r[0] == _R186_FUSION_TOOL]
        assert rows, "the fusion exit recorded no ranking event"
        assert rows[0][2] == 0, "ledger claims a similarity channel that was not built"

    def test_the_response_and_the_ledger_agree(self, _r186_repo, _r186_ledger):
        """The two used to disagree about the same call. Both derive from `channels`."""
        out = get_ranked_context(
            _r186_repo["repo"], query="refresh token", token_budget=2000,
            fusion=True, storage_path=_r186_repo["storage"],
        )
        channels = out["_meta"]["channels"]
        assert "similarity" not in channels
        assert out["_meta"]["verdict"]["channels"]["semantic"] == "off"
        rows = [r for r in _r186_rows(_r186_ledger["base"]) if r[0] == _R186_FUSION_TOOL]
        assert bool(rows[0][2]) is ("similarity" in channels)

    def test_a_similarity_channel_would_be_recorded(self, _r186_repo, _r186_ledger, monkeypatch):
        """Non-vacuity for the derivation: hardcoding False would pass every test
        above and re-arm the same trap. Give the exit a channel named `similarity`
        and both the ledger and the verdict must follow, with no second edit."""
        # The exit imports its channel builders from signal_fusion inside the
        # function body, so patching the module reaches the real call.
        from jcodemunch_mcp.retrieval import signal_fusion as _r186_sf

        real_identity = _r186_sf.build_identity_channel

        def _r186_as_similarity(*args, **kwargs):
            ch = real_identity(*args, **kwargs)
            fields = {f: getattr(ch, f) for f in ch.__dataclass_fields__}
            return ch.__class__(**{**fields, "name": "similarity"})

        monkeypatch.setattr(_r186_sf, "build_identity_channel", _r186_as_similarity)
        out = get_ranked_context(
            _r186_repo["repo"], query="refresh token", token_budget=2000,
            fusion=True, storage_path=_r186_repo["storage"],
        )
        assert "similarity" in out["_meta"]["channels"]
        assert out["_meta"]["verdict"]["channels"]["semantic"] == "ok"
        rows = [r for r in _r186_rows(_r186_ledger["base"]) if r[0] == _R186_FUSION_TOOL]
        assert rows[0][2] == 1

    def test_the_exit_passes_a_derived_flag_not_a_literal(self):
        """A source-level pin, because the derivation is what keeps this fixed.
        Asserted on code lines only: a comment mentioning the old literal must not
        decide whether this passes."""
        code = "\n".join(
            line for line in inspect.getsource(_r186_fusion_exit()).splitlines()
            if not line.lstrip().startswith("#")
        )
        assert "semantic_used=True" not in code
        assert "semantic_used=_semantic_used" in code
        assert 'ch.name == "similarity"' in code


def _r186_fusion_exit():
    from jcodemunch_mcp.tools.get_ranked_context import _get_ranked_context_fusion

    return _get_ranked_context_fusion


# --- the predicate ----------------------------------------------------------- #


def _r186_row(tool: str, semantic: int, *, conf: float = 0.5, ident: int = 0) -> tuple:
    """A ranking_events row in the shape ranking_db_query SELECTs."""
    return (0.0, "acme/widget", tool, "qh", "q", "[]", None, None, conf, semantic, ident, 0)


class TestSemanticLabelTrust:
    def test_a_pre_fix_fusion_row_is_not_evidence(self):
        assert not _r186_trust.semantic_label_is_trustworthy(
            _r186_row(_R186_FUSION_TOOL, 1)
        )

    def test_a_post_fix_fusion_row_is_evidence(self):
        """The tool alone must not disqualify a row, or the fix's own honest rows
        would be discarded — the ones a consumer most wants to read."""
        assert _r186_trust.semantic_label_is_trustworthy(
            _r186_row(_R186_FUSION_TOOL, 0)
        )

    def test_other_tools_keep_their_semantic_labels(self):
        for tool in ("search_symbols", "search_symbols_fusion", "get_ranked_context"):
            assert _r186_trust.semantic_label_is_trustworthy(_r186_row(tool, 1)), tool
            assert _r186_trust.semantic_label_is_trustworthy(_r186_row(tool, 0)), tool

    def test_a_short_row_is_trusted_rather_than_dropped(self):
        """This predicate refuses a KNOWN lie. Refusing rows it cannot classify
        would turn a shape surprise into silent data loss."""
        assert _r186_trust.semantic_label_is_trustworthy((0.0, "acme/widget"))
        assert _r186_trust.semantic_label_is_trustworthy(())

    def test_the_covered_tool_is_named_once(self):
        assert _r186_trust.MISLABELLED_FUSION_TOOL == _R186_FUSION_TOOL


# --- the tuner --------------------------------------------------------------- #


class TestTheTunerDoesNotLearnFromTheMislabel:
    def test_mislabelled_rows_do_not_move_the_weight(self, _r186_ledger):
        """The reproduction, as a test. 30 honest lexical rows at 0.80 and 30
        pre-fix fusion rows at 0.40 walked semantic_weight 0.5 -> 0.45."""
        state = _r186_ledger["state"]
        _r186_seed(state, tool="search_symbols", count=30, confidence=0.80, semantic=False)
        _r186_seed(state, tool=_R186_FUSION_TOOL, count=30, confidence=0.40, semantic=True)
        out = WeightTuner(base_path=_r186_ledger["base"]).learn("acme/widget", dry_run=True)
        assert out["applied"] is False
        assert "semantic_weight" not in out["after"]
        assert out["signals"]["events_semantic_label_unknown"] == 30
        assert "hardcoded argument" in out["signals"]["semantic_label_unknown_note"]

    def test_the_same_ledger_moves_the_weight_without_the_guard(self, _r186_ledger, monkeypatch):
        """Non-vacuity: with the predicate disabled, the identical ledger steps the
        learned weight down. The test above is not passing for another reason."""
        state = _r186_ledger["state"]
        _r186_seed(state, tool="search_symbols", count=30, confidence=0.80, semantic=False)
        _r186_seed(state, tool=_R186_FUSION_TOOL, count=30, confidence=0.40, semantic=True)
        monkeypatch.setattr(
            _r186_tuning, "_semantic_label_is_trustworthy", lambda row: True
        )
        out = WeightTuner(base_path=_r186_ledger["base"]).learn("acme/widget", dry_run=True)
        assert out["after"]["semantic_weight"] == 0.45
        assert out["signals"]["semantic_step"] == -0.05

    def test_honest_rows_still_teach_the_tuner(self, _r186_ledger):
        """The guard must not switch learning off. A real semantic search that
        outperforms lexical still raises the weight."""
        state = _r186_ledger["state"]
        _r186_seed(state, tool="search_symbols", count=30, confidence=0.40, semantic=False)
        _r186_seed(state, tool="search_symbols_fusion", count=30, confidence=0.90, semantic=True)
        out = WeightTuner(base_path=_r186_ledger["base"]).learn("acme/widget", dry_run=True)
        assert out["after"]["semantic_weight"] == 0.55
        assert "events_semantic_label_unknown" not in out["signals"]

    def test_a_clean_ledger_reports_no_unknown_key(self, _r186_ledger):
        """Shape stability: a caller whose ledger holds no mislabelled row sees the
        signals dict it saw before v1.108.186."""
        state = _r186_ledger["state"]
        _r186_seed(state, tool="search_symbols", count=60, confidence=0.60, semantic=False)
        out = WeightTuner(base_path=_r186_ledger["base"]).learn("acme/widget", dry_run=True)
        assert set(out["signals"]) == {
            "events_with_confidence",
            "mean_confidence_semantic_on",
            "mean_confidence_semantic_off",
        }

    def test_unknown_is_not_counted_as_semantic_off(self, _r186_ledger):
        """The mirrored error. If the 30 pre-fix rows were folded into the OFF
        group they would drag its mean to 0.6 and the proposal would move."""
        state = _r186_ledger["state"]
        _r186_seed(state, tool="search_symbols", count=30, confidence=0.80, semantic=False)
        _r186_seed(state, tool=_R186_FUSION_TOOL, count=30, confidence=0.40, semantic=True)
        _r186_seed(state, tool="search_symbols_fusion", count=30, confidence=0.70, semantic=True)
        out = WeightTuner(base_path=_r186_ledger["base"]).learn("acme/widget", dry_run=True)
        assert out["signals"]["mean_confidence_semantic_off"] == 0.8
        assert out["signals"]["mean_confidence_semantic_on"] == 0.7


# --- regret ------------------------------------------------------------------ #


class TestRegretDoesNotClusterOnTheMislabel:
    def test_pre_fix_rows_are_not_a_vocabulary_gap(self, _r186_ledger):
        """`not identity_hit and semantic_used` matched every pre-fix fusion row:
        that exit hardcoded the flag AND passed no ledger features, so identity_hit
        defaulted to 0 while the identity channel was one of the three it built."""
        _r186_seed(
            _r186_ledger["state"], tool=_R186_FUSION_TOOL, count=6,
            confidence=0.90, semantic=True, query="same-question",
        )
        out = _r186_regret.analyze_regret(
            "acme/widget", storage_path=_r186_ledger["base"], all_time=True
        )
        kinds = {c["signal"] for c in out["clusters"]}
        assert "vocabulary_gap" not in kinds
        assert out["events_semantic_label_unknown"] == 6

    def test_the_cluster_appears_without_the_guard(self, _r186_ledger, monkeypatch):
        """Non-vacuity, and it shows what shipped to users: suggest_corrections
        turns these clusters into config patches."""
        _r186_seed(
            _r186_ledger["state"], tool=_R186_FUSION_TOOL, count=6,
            confidence=0.90, semantic=True, query="same-question",
        )
        monkeypatch.setattr(
            _r186_regret, "_semantic_label_is_trustworthy", lambda row: True
        )
        out = _r186_regret.analyze_regret(
            "acme/widget", storage_path=_r186_ledger["base"], all_time=True
        )
        assert "vocabulary_gap" in {c["signal"] for c in out["clusters"]}

    def test_a_real_vocabulary_gap_still_clusters(self, _r186_ledger):
        state = _r186_ledger["state"]
        for i in range(4):
            state.record_ranking_event(
                tool="search_symbols", repo="acme/widget", query="same-question",
                returned_ids=["a"], confidence=0.90, semantic_used=True,
                identity_hit=False,
            )
        out = _r186_regret.analyze_regret(
            "acme/widget", storage_path=_r186_ledger["base"], all_time=True
        )
        assert "vocabulary_gap" in {c["signal"] for c in out["clusters"]}

    def test_a_clean_ledger_keeps_the_old_shape(self, _r186_ledger):
        _r186_seed(
            _r186_ledger["state"], tool="search_symbols", count=4,
            confidence=0.90, semantic=False,
        )
        out = _r186_regret.analyze_regret(
            "acme/widget", storage_path=_r186_ledger["base"], all_time=True
        )
        assert "events_semantic_label_unknown" not in out


# --- analyze_perf ------------------------------------------------------------ #


class TestPerfLedgerSummaryDiscloses:
    def test_mislabelled_rows_are_counted_apart(self):
        rows = [_r186_row(_R186_FUSION_TOOL, 1) for _ in range(3)]
        rows += [_r186_row("search_symbols", 1) for _ in range(2)]
        summary = _r186_perf_mod._ledger_summary(rows, top=10)
        by_repo = summary["by_repo"][0]
        assert by_repo["semantic_used"] == 2
        assert by_repo["semantic_label_unknown"] == 3

    def test_a_clean_ledger_keeps_the_old_shape(self):
        """Compatibility is testable, not asserted: no new key for a caller whose
        ledger holds no mislabelled row."""
        rows = [_r186_row("search_symbols", 1), _r186_row("search_symbols", 0)]
        summary = _r186_perf_mod._ledger_summary(rows, top=10)
        assert "semantic_label_unknown" not in summary["by_repo"][0]
        assert summary["by_repo"][0]["semantic_used"] == 1

    def test_a_pre_fix_ledger_is_still_readable(self, _r186_ledger):
        """The rows are history. Nothing here rewrites or drops them."""
        _r186_seed(
            _r186_ledger["state"], tool=_R186_FUSION_TOOL, count=5,
            confidence=0.5, semantic=True,
        )
        rows = _r186_tt.ranking_db_query(base_path=_r186_ledger["base"], repo="acme/widget")
        assert len(rows) == 5
        assert all(r[9] == 1 for r in rows), "history must not be rewritten"


# --- drift guard ------------------------------------------------------------- #


class TestTheGuardExpiresWhenTheExitChanges:
    def test_a_similarity_channel_in_the_exit_retires_the_rule(self):
        """⚠ The exclusion is only honest while that exit builds no similarity
        channel. If one is added, its `semantic_used=1` rows become real evidence
        and this rule would start discarding good data. Fail loudly then, rather
        than quietly under-learning."""
        src = inspect.getsource(_r186_fusion_exit())
        builds_similarity = "build_similarity_channel" in src
        rule_active = (
            _R186_FUSION_TOOL,
            True,
        ) in _r186_trust._UNTRUSTED_SEMANTIC_LABELS
        assert not (builds_similarity and rule_active), (
            "get_ranked_context's fusion exit now builds a similarity channel, so "
            "its semantic_used=1 rows are honest. Bound the ledger_trust rule by "
            "timestamp or drop it."
        )
