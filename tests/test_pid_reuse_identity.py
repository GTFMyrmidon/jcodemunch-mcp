"""PID-reuse identity checks (jcm#450).

`_is_pid_alive` answers "is this PID taken?", not "is my process still there?"
After the OS recycles a PID, a registry row or lock file naming a long-dead
process reads as live forever — observed in the field as two-week-old registry
rows resolving to a Chrome renderer and an AMD service.

The fix binds process identity at write time: `register()` and `acquire()`
record the holder's OS creation time, and every reader treats *PID alive but
creation time mismatched* as dead → stale → prune. Rows/locks written by older
versions carry no `create_time` and keep the old liveness-only behavior.
"""
import os
import sys
import time

import pytest

from jcodemunch_mcp.storage import process_registry as registry
from jcodemunch_mcp.storage import process_locks as locks


IDENTITY_PLATFORMS = ("win32", "linux")


# --- process_create_time ---------------------------------------------------

def test_own_create_time_is_sane():
    ct = locks.process_create_time(os.getpid())
    if sys.platform not in IDENTITY_PLATFORMS:
        pytest.skip("creation-time identity not implemented on this platform")
    assert ct is not None
    # Created after 2020 and not in the future (small skew tolerance).
    assert 1577836800.0 < ct <= time.time() + 5.0


def test_create_time_stable_across_reads():
    if sys.platform not in IDENTITY_PLATFORMS:
        pytest.skip("creation-time identity not implemented on this platform")
    a = locks.process_create_time(os.getpid())
    b = locks.process_create_time(os.getpid())
    assert a is not None and b is not None
    assert abs(a - b) < 0.5


def test_create_time_of_dead_pid_is_none():
    assert locks.process_create_time(999999999) is None


# --- registry: recycled-PID rows are pruned --------------------------------

def _write_row(tmp_path, pid, extra=""):
    d = tmp_path / registry._DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{pid}.json").write_text(
        '{"pid": %d, "client_id": "ghost", "transport": "stdio",'
        ' "version": "0", "started_at": "2020-01-01T00:00:00+00:00"%s}' % (pid, extra),
        encoding="utf-8",
    )
    return d / f"{pid}.json"


def test_recycled_pid_row_is_pruned(tmp_path):
    """THE bug: our own PID is definitely alive, but the row's create_time says
    it was recorded for a different incarnation — must prune, not report live."""
    if sys.platform not in IDENTITY_PLATFORMS:
        pytest.skip("creation-time identity not implemented on this platform")
    row = _write_row(tmp_path, os.getpid(), extra=', "create_time": 12345.0')
    assert registry.live_processes(str(tmp_path)) == []
    assert not row.exists(), "identity-mismatched row should be pruned"


def test_row_without_create_time_keeps_old_behavior(tmp_path):
    """Back-compat: rows written by pre-fix versions have no create_time and
    must still be reported when the PID is alive."""
    _write_row(tmp_path, os.getpid())
    entries = registry.live_processes(str(tmp_path))
    assert [e.pid for e in entries] == [os.getpid()]


def test_register_records_matching_create_time(tmp_path):
    try:
        path = registry.register("stdio", "9.9.9", str(tmp_path))
        assert path is not None
        entries = registry.live_processes(str(tmp_path))
        assert [e.pid for e in entries] == [os.getpid()]
        if sys.platform in IDENTITY_PLATFORMS:
            own = locks.process_create_time(os.getpid())
            assert entries[0].create_time is not None
            assert abs(entries[0].create_time - own) < 2.0
    finally:
        registry.unregister()


# --- locks: recycled-PID holders are stale ---------------------------------

def test_lock_with_recycled_pid_is_reclaimable(tmp_path):
    if sys.platform not in IDENTITY_PLATFORMS:
        pytest.skip("creation-time identity not implemented on this platform")
    lock_fp = locks.lock_path("testscope", "some/target", str(tmp_path))
    lock_fp.write_text(
        '{"scope": "testscope", "target": "some/target", "pid": %d,'
        ' "client_id": "ghost", "started_at": "2020-01-01T00:00:00+00:00",'
        ' "create_time": 12345.0}' % os.getpid(),
        encoding="utf-8",
    )
    # inspect: identity mismatch => no holder
    assert locks.inspect("testscope", "some/target", str(tmp_path)) is None
    # acquire: identity mismatch => stale, reclaimed
    try:
        assert locks.acquire("testscope", "some/target", str(tmp_path)) is True
    finally:
        locks.release("testscope", "some/target", str(tmp_path))


def test_lock_without_create_time_still_held(tmp_path):
    """Back-compat: a lock written by a pre-fix version (no create_time) whose
    PID is alive must still read as held."""
    lock_fp = locks.lock_path("testscope", "bc/target", str(tmp_path))
    lock_fp.write_text(
        '{"scope": "testscope", "target": "bc/target", "pid": %d,'
        ' "client_id": "old", "started_at": "2020-01-01T00:00:00+00:00"}' % os.getpid(),
        encoding="utf-8",
    )
    holder = locks.inspect("testscope", "bc/target", str(tmp_path))
    assert holder is not None and holder.pid == os.getpid()
    assert locks.acquire("testscope", "bc/target", str(tmp_path)) is False
    lock_fp.unlink()


def test_lock_roundtrip_with_identity(tmp_path):
    try:
        assert locks.acquire("testscope", "rt/target", str(tmp_path)) is True
        holder = locks.inspect("testscope", "rt/target", str(tmp_path))
        assert holder is not None and holder.pid == os.getpid()
        assert locks.acquire("testscope", "rt/target", str(tmp_path)) is False
    finally:
        locks.release("testscope", "rt/target", str(tmp_path))
