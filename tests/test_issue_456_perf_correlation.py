"""Acceptance contracts for issue #456 perf-table correlation keys."""

from __future__ import annotations

import asyncio
import queue
import sqlite3
from pathlib import Path

import pytest

from jcodemunch_mcp import config as config_module
from jcodemunch_mcp import server
from jcodemunch_mcp.storage import token_tracker


def _set_perf_enabled(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
    original_get = config_module.get

    def configured_get(key: str, default=None, **kwargs):
        if key == "perf_telemetry_enabled":
            return enabled
        return original_get(key, default, **kwargs)

    monkeypatch.setattr(config_module, "get", configured_get)


def _columns(db_path: Path, table: str) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def test_issue_456_fresh_database_has_both_keys_on_both_tables(monkeypatch, tmp_path):
    _set_perf_enabled(monkeypatch, True)
    state = token_tracker._State()
    state.record_latency("fresh", 1.0, base_path=str(tmp_path))
    db_path = tmp_path / "telemetry.db"

    for table in ("tool_calls", "ranking_events"):
        assert {"session_uid", "call_uid"} <= _columns(db_path, table)
    state.close_perf_dbs()


def test_issue_456_legacy_rows_remain_null_after_four_column_migration(
    monkeypatch, tmp_path
):
    _set_perf_enabled(monkeypatch, True)
    db_path = tmp_path / "telemetry.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE tool_calls (ts REAL NOT NULL, tool TEXT NOT NULL, "
            "duration_ms REAL NOT NULL, ok INTEGER NOT NULL, repo TEXT)"
        )
        conn.execute("INSERT INTO tool_calls VALUES (1, 'legacy-tool', 2, 1, 'repo')")
        conn.execute(
            "CREATE TABLE ranking_events (ts REAL NOT NULL, repo TEXT, "
            "tool TEXT NOT NULL, query_hash TEXT NOT NULL, query TEXT, "
            "returned_ids TEXT NOT NULL, top1_score REAL, top2_score REAL, "
            "confidence REAL, semantic_used INTEGER NOT NULL, "
            "identity_hit INTEGER NOT NULL, repo_is_stale INTEGER NOT NULL)"
        )
        conn.execute(
            "INSERT INTO ranking_events VALUES "
            "(1, 'repo', 'legacy-rank', 'hash', 'query', '[]', NULL, NULL, "
            "NULL, 0, 0, 0)"
        )

    state = token_tracker._State()
    state.record_latency("current-tool", 3.0, base_path=str(tmp_path))
    state.record_ranking_event(
        tool="current-rank",
        repo="repo",
        query="query",
        returned_ids=[],
        base_path=str(tmp_path),
    )
    state.close_perf_dbs()

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT session_uid, call_uid FROM tool_calls WHERE tool='legacy-tool'"
        ).fetchone() == (None, None)
        assert conn.execute(
            "SELECT session_uid, call_uid FROM ranking_events "
            "WHERE tool='legacy-rank'"
        ).fetchone() == (None, None)


def test_issue_456_schema_probe_runs_once_per_resolved_path(monkeypatch, tmp_path):
    _set_perf_enabled(monkeypatch, True)
    original = token_tracker._State._add_column_if_missing
    probes: list[tuple[str, str, str]] = []

    def observed_probe(conn, table, column, decl):
        path = str(conn.execute("PRAGMA database_list").fetchone()[2])
        if column in {"session_uid", "call_uid"}:
            probes.append((str(Path(path).resolve()), table, column))
        return original(conn, table, column, decl)

    monkeypatch.setattr(
        token_tracker._State, "_add_column_if_missing", staticmethod(observed_probe)
    )
    state = token_tracker._State()
    paths = [tmp_path / "one", tmp_path / "two"]
    for path in paths:
        path.mkdir()
    state.record_latency("first", 1.0, base_path=str(paths[0]))
    state.record_latency("second", 1.0, base_path=str(paths[0]))
    state.record_latency("third", 1.0, base_path=str(paths[1]))
    state.close_perf_dbs()

    expected = {
        (str((path / "telemetry.db").resolve()), table, column)
        for path in paths
        for table in ("tool_calls", "ranking_events")
        for column in ("session_uid", "call_uid")
    }
    assert set(probes) == expected
    for probe in expected:
        assert probes.count(probe) == 1


@pytest.mark.asyncio
async def test_issue_456_one_dispatcher_entry_joins_ranking_and_latency(
    monkeypatch, tmp_path
):
    _set_perf_enabled(monkeypatch, True)
    state = token_tracker._State()

    async def write_both(_name: str, _arguments: dict):
        state.record_ranking_event(
            tool="search_symbols",
            repo="repo",
            query="query",
            returned_ids=["symbol"],
            base_path=str(tmp_path),
        )
        state.record_latency(
            "search_symbols", 2.0, repo="repo", base_path=str(tmp_path)
        )
        return []

    monkeypatch.setattr(server, "_call_tool_impl", write_both)
    await server.call_tool("search_symbols", {})
    state.close_perf_dbs()

    with sqlite3.connect(tmp_path / "telemetry.db") as conn:
        ranking = conn.execute(
            "SELECT session_uid, call_uid FROM ranking_events"
        ).fetchone()
        latency = conn.execute(
            "SELECT session_uid, call_uid FROM tool_calls"
        ).fetchone()
    assert ranking == latency
    assert ranking[0] == state._session_uid
    assert ranking[1]


@pytest.mark.asyncio
async def test_issue_456_concurrent_dispatcher_entries_have_distinct_call_ids(
    monkeypatch,
):
    observations: dict[str, tuple[str | None, str | None]] = {}

    async def observe(name: str, _arguments: dict):
        before = token_tracker._CURRENT_CALL_UID.get()
        await asyncio.sleep(0)
        observations[name] = (before, token_tracker._CURRENT_CALL_UID.get())
        return []

    monkeypatch.setattr(server, "_call_tool_impl", observe)
    await asyncio.gather(server.call_tool("one", {}), server.call_tool("two", {}))

    assert observations["one"][0] == observations["one"][1]
    assert observations["two"][0] == observations["two"][1]
    assert observations["one"][0] != observations["two"][0]
    assert token_tracker._CURRENT_CALL_UID.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["order", "route"])
@pytest.mark.parametrize("failure", [None, "inner", "outer"])
async def test_issue_456_reentrant_dispatch_restores_context_on_success_and_failure(
    monkeypatch, mode, failure
):
    observed: dict[str, str | None] = {}
    scenario = {"failure": failure}

    monkeypatch.setattr(server, "_catalog_names", lambda: ["inner"])
    monkeypatch.setattr(server._counter, "order_gate", lambda *args: None)
    monkeypatch.setattr(
        server._counter,
        "classify_intent",
        lambda *args: [{"action": "inner", "why": "test"}],
    )
    monkeypatch.setattr(
        server._counter,
        "shape_execute_args",
        lambda *args: {"fail_inner": scenario["failure"] == "inner"},
    )
    monkeypatch.setattr(server._counter, "is_state_changing", lambda *args: False)

    async def reentrant(name: str, arguments: dict):
        if name in {"order", "route"}:
            observed["outer_before"] = token_tracker._CURRENT_CALL_UID.get()
            try:
                result = await (
                    server._handle_order(arguments)
                    if name == "order"
                    else server._handle_route(arguments)
                )
                if scenario["failure"] == "outer":
                    raise RuntimeError("outer failure")
                return result
            finally:
                observed["outer_after"] = token_tracker._CURRENT_CALL_UID.get()
        observed["inner"] = token_tracker._CURRENT_CALL_UID.get()
        if arguments.get("fail_inner"):
            raise RuntimeError("inner failure")
        return []

    monkeypatch.setattr(server, "_call_tool_impl", reentrant)
    arguments = (
        {"action": "inner", "args": {"fail_inner": failure == "inner"}}
        if mode == "order"
        else {"task": "inner", "execute": True}
    )
    if failure:
        with pytest.raises(RuntimeError, match=f"{failure} failure"):
            await server.call_tool(mode, arguments)
    else:
        await server.call_tool(mode, arguments)

    assert observed["outer_before"] == observed["outer_after"]
    assert observed["inner"] != observed["outer_before"]
    assert observed["inner"] and observed["outer_before"]
    assert token_tracker._CURRENT_CALL_UID.get() is None


def test_issue_456_writes_outside_dispatch_store_null_call_id(monkeypatch, tmp_path):
    _set_perf_enabled(monkeypatch, True)
    state = token_tracker._State()
    state.record_latency("outside", 1.0, base_path=str(tmp_path))
    state.record_ranking_event(
        tool="outside",
        repo=None,
        query="outside",
        returned_ids=[],
        base_path=str(tmp_path),
    )
    state.close_perf_dbs()

    with sqlite3.connect(tmp_path / "telemetry.db") as conn:
        tool = conn.execute("SELECT session_uid, call_uid FROM tool_calls").fetchone()
        ranking = conn.execute(
            "SELECT session_uid, call_uid FROM ranking_events"
        ).fetchone()
    assert tool == ranking == (state._session_uid, None)


def test_issue_456_telemetry_disabled_creates_no_database(monkeypatch, tmp_path):
    _set_perf_enabled(monkeypatch, False)
    state = token_tracker._State()
    state.record_latency("disabled", 1.0, base_path=str(tmp_path))
    state.record_ranking_event(
        tool="disabled",
        repo=None,
        query="disabled",
        returned_ids=[],
        base_path=str(tmp_path),
    )
    assert not (tmp_path / "telemetry.db").exists()


def test_issue_456_outbound_payload_has_exact_legacy_keys(monkeypatch):
    captured: list[dict] = []
    work_queue: queue.Queue = queue.Queue()
    work_queue.put((1, 2, "anonymous"))
    work_queue.put(None)
    monkeypatch.setattr(token_tracker, "_telemetry_queue", work_queue)

    import httpx

    monkeypatch.setattr(
        httpx,
        "post",
        lambda _url, *, json, timeout: captured.append(json),
    )
    token_tracker._telemetry_worker()

    assert captured == [{"delta": 1, "total": 2, "anon_id": "anonymous"}]
    assert not ({"session_uid", "call_uid"} & set(captured[0]))


def test_issue_456_context_helpers_restore_nested_values():
    assert token_tracker._CURRENT_CALL_UID.get() is None
    outer = token_tracker.begin_call_context("outer")
    try:
        assert token_tracker._CURRENT_CALL_UID.get() == "outer"
        inner = token_tracker.begin_call_context("inner")
        try:
            assert token_tracker._CURRENT_CALL_UID.get() == "inner"
        finally:
            token_tracker.end_call_context(inner)
        assert token_tracker._CURRENT_CALL_UID.get() == "outer"
    finally:
        token_tracker.end_call_context(outer)
    assert token_tracker._CURRENT_CALL_UID.get() is None
