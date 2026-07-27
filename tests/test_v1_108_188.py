"""Telemetry rows land in the store the caller named (v1.108.188).

The second finding 1.108.186 reported and 1.108.187 left open. Every READER of the
perf database takes a base path — `ranking_db_query`, `WeightTuner`, `analyze_perf`
— but the WRITERS passed none, so rows resolved through `_State._base_path`, which
is whatever the first caller of `_ensure_loaded` happened to pass. Most savings
writes pass nothing, so it was usually `None` and every row landed in
`~/.code-index` no matter which `storage_path` the tool was handed.

**A search against a named store therefore wrote to one database and read from
another**, which is how a reproduction earlier in this arc put rows in a developer's
real telemetry db while pointing every tool at a temp directory. `WeightTuner` reads
through the same base path, so on a non-default store it learned from a ledger the
searches had never written to.

Both tables in that file had it: `ranking_events` (six producers) and `tool_calls`
(the dispatcher's latency row). `analyze_perf` reads both through one base path.

⚠ **What is deliberately NOT changed: where the savings TOTAL lives.**
`_ensure_loaded` still pins `_base_path` on first call. That counter is a
process-global lifetime total loaded once from disk, and re-anchoring it mid-process
would move a user's cumulative savings between files. The telemetry rows are
per-call facts about a named store; the savings total is not.

Helper names are prefixed ``_r188``; the replay fixture queries ``analyze_perf``,
``record_tool_latency`` and ``_State``, so no helper here carries those names.
"""

from __future__ import annotations

import pathlib
import sqlite3
from pathlib import Path

import pytest

from jcodemunch_mcp import config as _r188_config
from jcodemunch_mcp.storage import token_tracker as _r188_tt
from jcodemunch_mcp.tools._utils import ledger_base_path
from jcodemunch_mcp.tools.get_ranked_context import get_ranked_context
from jcodemunch_mcp.tools.plan_turn import plan_turn
from jcodemunch_mcp.tools.search_symbols import search_symbols


@pytest.fixture()
def _r188_boxed(tmp_path, monkeypatch):
    """A named store, a FAKE default store, and a fresh tracker state.

    ⚠ `Path.home` is redirected so the default-store assertions cannot be satisfied
    (or polluted) by the developer's real `~/.code-index`. The tracker state is
    deliberately left as a new process would find it — `_loaded` False and
    `_base_path` unset is the exact condition the defect needs.
    """
    fake_home = tmp_path / "home"
    (fake_home / ".code-index").mkdir(parents=True)
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: fake_home))

    store = tmp_path / "named_store"
    monkeypatch.setenv("CODE_INDEX_PATH", str(store))
    proj = tmp_path / "proj"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "a.py").write_text(
        "def refresh_token(user):\n    return user\n", encoding="utf-8"
    )
    from jcodemunch_mcp.tools.index_folder import index_folder

    repo = index_folder(path=str(proj), use_ai_summaries=False, storage_path=str(store))["repo"]

    monkeypatch.setattr(_r188_tt, "_state", _r188_tt._State())
    real_get = _r188_config.get

    def _patched(key, default=None, *args, **kwargs):
        if key == "perf_telemetry_enabled":
            return True
        return real_get(key, default, *args, **kwargs)

    monkeypatch.setattr(_r188_config, "get", _patched)
    return {
        "repo": repo,
        "named": str(store),
        "default": str(fake_home / ".code-index"),
    }


def _r188_tools(base: str, table: str = "ranking_events") -> dict:
    db = Path(base) / "telemetry.db"
    if not db.exists():
        return {}
    con = sqlite3.connect(str(db))
    try:
        col = "tool" if table == "ranking_events" else "tool"
        return dict(con.execute(f"SELECT {col}, COUNT(*) FROM {table} GROUP BY {col}").fetchall())
    except sqlite3.OperationalError:
        return {}
    finally:
        con.close()


# --- the routing --------------------------------------------------------------- #


class TestRankingEventsFollowTheNamedStore:
    def test_every_producer_writes_to_the_named_store(self, _r188_boxed):
        """All six call sites, through the real route. A partial fix here is worse
        than none: a ledger holding four of six producers reads complete."""
        repo, named = _r188_boxed["repo"], _r188_boxed["named"]
        search_symbols(repo, query="refresh_token", storage_path=named)
        search_symbols(repo, query="refresh_token", fusion=True, storage_path=named)
        search_symbols(repo, query="refresh_token", semantic=True, storage_path=named)
        get_ranked_context(repo, query="refresh_token", token_budget=2000, storage_path=named)
        get_ranked_context(
            repo, query="refresh_token", token_budget=2000, fusion=True, storage_path=named
        )
        plan_turn(repo, query="refresh_token", storage_path=named)

        written = _r188_tools(named)
        assert written.get("search_symbols"), written
        assert written.get("search_symbols_fusion"), written
        assert written.get("get_ranked_context"), written
        assert written.get("get_ranked_context_fusion"), written
        assert written.get("plan_turn"), written

    def test_nothing_reaches_the_default_store(self, _r188_boxed):
        repo, named = _r188_boxed["repo"], _r188_boxed["named"]
        search_symbols(repo, query="refresh_token", storage_path=named)
        get_ranked_context(
            repo, query="refresh_token", token_budget=2000, fusion=True, storage_path=named
        )
        assert _r188_tools(_r188_boxed["default"]) == {}

    def test_the_reader_sees_what_the_search_wrote(self, _r188_boxed):
        """The whole point: writer and reader must mean the same database."""
        repo, named = _r188_boxed["repo"], _r188_boxed["named"]
        search_symbols(repo, query="refresh_token", storage_path=named)
        rows = _r188_tt.ranking_db_query(base_path=named)
        assert len(rows) >= 1
        assert not _r188_tt.ranking_db_query(base_path=_r188_boxed["default"])

    def test_the_tuner_reads_the_store_the_searches_wrote_to(self, _r188_boxed):
        """`WeightTuner` takes the same base path, so a split ledger meant learning
        from rows the searches never produced."""
        from jcodemunch_mcp.retrieval.tuning import WeightTuner

        repo, named = _r188_boxed["repo"], _r188_boxed["named"]
        # Distinct queries: the session result cache serves a repeated search from
        # memory and writes no ledger row, which is correct and not what is under
        # test here.
        for q in ("refresh_token", "user", "refresh"):
            search_symbols(repo, query=q, storage_path=named)
        out = WeightTuner(base_path=named).learn(repo, dry_run=True, min_events=2)
        assert out["events"] >= 3


class TestLatencyRowsFollowTheNamedStore:
    def test_a_persisted_latency_row_honours_the_base_path(self, _r188_boxed):
        """`analyze_perf` reads tool_calls and ranking_events through ONE base path,
        so the latency table had the same write-here / read-there split."""
        _r188_tt.record_tool_latency(
            "search_symbols", 12.5, ok=True, repo="acme/widget",
            base_path=_r188_boxed["named"],
        )
        assert _r188_tools(_r188_boxed["named"], "tool_calls").get("search_symbols") == 1
        assert _r188_tools(_r188_boxed["default"], "tool_calls") == {}

    def test_no_base_path_still_uses_the_default(self, _r188_boxed):
        """Compatibility: the old call shape keeps the old destination."""
        _r188_tt.record_tool_latency("search_symbols", 12.5, ok=True, repo="acme/widget")
        assert _r188_tools(_r188_boxed["default"], "tool_calls").get("search_symbols") == 1


# --- the resolver ------------------------------------------------------------- #


class TestPerfDbPathResolution:
    def test_an_explicit_base_wins_over_the_sticky_one(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path / "home"))
        state = _r188_tt._State()
        state._base_path = str(tmp_path / "sticky")
        explicit = tmp_path / "explicit"
        assert state._perf_db_path(str(explicit)) == explicit / "telemetry.db"

    def test_an_explicit_base_is_not_cached(self, tmp_path, monkeypatch):
        """It belongs to one call, not to the process. Caching it would make the
        SECOND store in a session silently write to the first one's database."""
        monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path / "home"))
        state = _r188_tt._State()
        first = state._perf_db_path(str(tmp_path / "a"))
        second = state._perf_db_path(str(tmp_path / "b"))
        assert first != second
        assert state._perf_db_path_cached is None

    def test_the_default_is_still_cached(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path / "home"))
        state = _r188_tt._State()
        state._perf_db_path(str(tmp_path / "explicit"))
        default = state._perf_db_path()
        assert state._perf_db_path_cached == default

    def test_one_bad_store_does_not_disable_the_rest(self, tmp_path, monkeypatch):
        """⚠ `_perf_db_failed` is a process-wide kill switch written when there was
        only one path it could mean. An unwritable caller-supplied path must not
        take telemetry down for every other store."""
        monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path / "home"))
        state = _r188_tt._State()
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("i am a file", encoding="utf-8")
        assert state._ensure_perf_db_locked(str(blocker)) is None
        assert state._perf_db_failed is False
        conn = state._ensure_perf_db_locked()
        assert conn is not None
        conn.close()


class TestLedgerBasePathHelper:
    def test_it_names_the_store(self, tmp_path):
        from jcodemunch_mcp.storage.index_store import IndexStore

        store = IndexStore(base_path=str(tmp_path / "s"))
        assert ledger_base_path(store) == str(tmp_path / "s")

    def test_a_store_that_cannot_say_returns_none(self):
        """None restores the previous default rather than dropping the row."""
        assert ledger_base_path(object()) is None
        assert ledger_base_path(None) is None


# --- what is deliberately unchanged ------------------------------------------- #


class TestTheSavingsTotalIsNotReanchored:
    def test_a_none_first_caller_still_pins_the_savings_path(self, tmp_path, monkeypatch):
        """⚠ Stated rather than silently fixed. The savings total is a
        process-global lifetime counter loaded once; re-anchoring it mid-process
        would move a user's cumulative total between files. Only the per-call
        telemetry rows were routed."""
        monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path / "home"))
        state = _r188_tt._State()
        state._ensure_loaded(None)
        assert state._base_path is None
        state._ensure_loaded(str(tmp_path / "named"))
        assert state._base_path is None, "savings anchoring intentionally unchanged"

    def test_ranking_rows_no_longer_depend_on_that_anchor(self, _r188_boxed):
        """The remedy for the reported bug does not go through `_base_path` at all,
        which is why a test no longer needs `_state._loaded = True` to stay off the
        developer's real telemetry db."""
        state = _r188_tt._state
        state._ensure_loaded(None)
        assert state._base_path is None
        search_symbols(_r188_boxed["repo"], query="refresh_token", storage_path=_r188_boxed["named"])
        assert _r188_tools(_r188_boxed["named"]).get("search_symbols") == 1
        assert _r188_tools(_r188_boxed["default"]) == {}
