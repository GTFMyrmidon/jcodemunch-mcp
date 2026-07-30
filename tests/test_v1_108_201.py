"""v1.108.201 — persist the delivery ledger so P0's number can be collected.

`redelivery_rate` shipped in v1.108.167 as a live figure on the yield block and
was never written anywhere, so it died with each process. The F-15 P2 gate has
been waiting on a number that no session could contribute to. These tests pin
the sink, its opt-in gate, and the pooling rule the aggregate depends on.
"""
from __future__ import annotations

import importlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks" / "anchor"))


def _d(sid, tokens=100, full=True):
    return (sid, tokens, full)


@pytest.fixture()
def tracker(tmp_path, monkeypatch):
    from jcodemunch_mcp.storage import token_tracker as tt

    importlib.reload(tt)
    monkeypatch.setattr(
        tt._config, "get",
        lambda key, default=None: True if key == "perf_telemetry_enabled" else default,
    )
    st = tt._State()
    st._base_path = str(tmp_path)
    return tt, st, tmp_path


def _rows(db: Path):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return conn.execute("SELECT * FROM session_yield").fetchall()
    finally:
        conn.close()


def test_disabled_telemetry_writes_nothing(tmp_path, monkeypatch):
    """The default. No opt-in, no row, no db side effect."""
    from jcodemunch_mcp.storage import token_tracker as tt

    importlib.reload(tt)
    monkeypatch.setattr(
        tt._config, "get",
        lambda key, default=None: False if key == "perf_telemetry_enabled" else default,
    )
    st = tt._State()
    st._base_path = str(tmp_path)
    st.note_delivered([_d("a.py::f#function")])
    with st._lock:
        st._persist_session_yield_locked(str(tmp_path))
    db = tmp_path / "telemetry.db"
    if db.exists():
        with pytest.raises(sqlite3.OperationalError):
            _rows(db)


def test_persists_counts_when_enabled(tracker):
    tt, st, tmp_path = tracker
    st.note_delivered([_d("a.py::f#function"), _d("b.py::g#function")])
    st.note_delivered([_d("a.py::f#function")])  # redelivery
    with st._lock:
        st._persist_session_yield_locked(str(tmp_path))

    rows = _rows(tmp_path / "telemetry.db")
    assert len(rows) == 1
    (uid, started, updated, deliveries, distinct, redelivered, tokens, full) = rows[0]
    assert deliveries == 3          # 2 + 1
    assert distinct == 2
    assert redelivered == 1         # only a.py::f was delivered twice
    assert full == 2


def test_upserts_one_row_per_session(tracker):
    """Repeated flushes must UPDATE, not append -- else one session is counted
    many times and the pooled rate is silently inflated."""
    tt, st, tmp_path = tracker
    st.note_delivered([_d("a.py::f#function")])
    with st._lock:
        st._persist_session_yield_locked(str(tmp_path))
    st.note_delivered([_d("a.py::f#function")])
    with st._lock:
        st._persist_session_yield_locked(str(tmp_path))

    rows = _rows(tmp_path / "telemetry.db")
    assert len(rows) == 1
    assert rows[0][3] == 2          # deliveries updated in place


def test_distinct_sessions_get_distinct_rows(tracker):
    tt, st, tmp_path = tracker
    st.note_delivered([_d("a.py::f#function")])
    with st._lock:
        st._persist_session_yield_locked(str(tmp_path))

    other = tt._State()
    other._base_path = str(tmp_path)
    other.note_delivered([_d("c.py::h#function")])
    with other._lock:
        other._persist_session_yield_locked(str(tmp_path))

    assert len(_rows(tmp_path / "telemetry.db")) == 2


def test_empty_ledger_writes_no_row(tracker):
    """A session that delivered nothing is not a 0% session; it is not a
    session. Writing it would drag the pooled denominator."""
    tt, st, tmp_path = tracker
    with st._lock:
        st._persist_session_yield_locked(str(tmp_path))
    db = tmp_path / "telemetry.db"
    assert not db.exists() or _rows(db) == []


# ---- aggregator ----------------------------------------------------------


def _seed(db: Path, sessions):
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS session_yield (
            session_uid TEXT PRIMARY KEY, started_at REAL NOT NULL,
            updated_at REAL NOT NULL, deliveries INTEGER NOT NULL,
            distinct_symbols INTEGER NOT NULL, redelivered_symbols INTEGER NOT NULL,
            redelivered_tokens_est INTEGER NOT NULL, full_source_symbols INTEGER NOT NULL)
    """)
    for i, (deliveries, distinct) in enumerate(sessions):
        conn.execute(
            "INSERT INTO session_yield VALUES (?,?,?,?,?,?,?,?)",
            (f"s{i}", 1.0, 2.0, deliveries, distinct, deliveries - distinct, 0, distinct),
        )
    conn.commit()
    conn.close()


def test_absent_table_is_not_a_low_number(tmp_path):
    """Nothing collected must never read as 'under 10%, kill P2'."""
    import measure

    _seed(tmp_path / "t.db", [])
    art = measure.collect(tmp_path / "t.db")
    assert art["deliveries"] == 0
    assert art.get("verdict") != "kill_p2"


def test_thin_data_is_insufficient_not_a_verdict(tmp_path):
    import measure

    _seed(tmp_path / "t.db", [(10, 9)])
    art = measure.collect(tmp_path / "t.db")
    assert art["verdict"] == "insufficient_data"
    assert art["sufficient"] is False


def test_rate_is_pooled_not_averaged(tmp_path):
    """THE methodological point. One tiny 50%-redelivery session next to a large
    clean one must not read as ~25%.

    Pooled: (100+2 deliveries - 100+1 distinct) / 102 = 1/102 = 0.0098.
    Mean-of-rates would give (0.0 + 0.5) / 2 = 0.25 -- a different VERDICT.
    """
    import measure

    _seed(tmp_path / "t.db", [(100, 100), (2, 1), (60, 60), (60, 60), (60, 60)])
    art = measure.collect(tmp_path / "t.db")
    assert art["redelivery_rate"] == pytest.approx(1 / 282, abs=1e-4)
    assert art["verdict"] == "kill_p2"


def test_verdict_bands(tmp_path):
    import measure

    for deliveries, distinct, expected in [
        (1000, 950, "kill_p2"),            # 5%
        (1000, 850, "ship_p2_opt_in"),     # 15%
        (1000, 600, "opt_in_by_default_live"),  # 40%
    ]:
        db = tmp_path / f"t{distinct}.db"
        _seed(db, [(deliveries // 5, distinct // 5)] * 5)
        art = measure.collect(db)
        assert art["verdict"] == expected, art


# ---- v1.108.202: route the row to the store the caller named ---------------


def test_row_lands_in_the_caller_named_store(tmp_path, monkeypatch):
    """v1.108.188's correction, applied to this writer.

    Every reader takes a base path. A writer that passes none resolves through
    _base_path -- usually None, so ~/.code-index -- and the row is written to one
    database while the aggregator reads another. Found live: a session against a
    CODE_INDEX_PATH store filed its row in the default store instead.
    """
    from jcodemunch_mcp.storage import token_tracker as tt

    importlib.reload(tt)
    monkeypatch.setattr(
        tt._config, "get",
        lambda key, default=None: True if key == "perf_telemetry_enabled" else default,
    )
    named = tmp_path / "named"
    default = tmp_path / "default"
    st = tt._State()
    st._base_path = str(default)          # what _ensure_loaded would have pinned

    st.note_delivered([_d("a.py::f#function")], base_path=str(named))
    with st._lock:
        st._persist_session_yield_locked(st._delivery_base_path)

    assert _rows(named / "telemetry.db"), "row did not reach the caller-named store"
    assert not (default / "telemetry.db").exists(), "row leaked to the default store"


def test_no_explicit_base_still_uses_the_default(tmp_path, monkeypatch):
    """The fallback must not become a dropped row."""
    from jcodemunch_mcp.storage import token_tracker as tt

    importlib.reload(tt)
    monkeypatch.setattr(
        tt._config, "get",
        lambda key, default=None: True if key == "perf_telemetry_enabled" else default,
    )
    st = tt._State()
    st._base_path = str(tmp_path)
    st.note_delivered([_d("a.py::f#function")])
    assert st._delivery_base_path is None
    with st._lock:
        st._persist_session_yield_locked(st._delivery_base_path)
    assert _rows(tmp_path / "telemetry.db")
