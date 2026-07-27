"""v1.108.189 — the two residual #375 sub-problems, closed.

#383: a client that sends no ``progressToken`` gets an elapsed-time heartbeat
      on the log channel instead of total silence.
#384: ``index_folder`` no longer indexes its own path twice under auto-watch.

Both were filed as decisions-not-taken rather than bugs-not-found, so the tests
here pin the DECISIONS as much as the behaviour: that a fast run stays silent,
that the heartbeat cannot become an unrequested MCP notification, and that the
deferral is scoped rather than a blanket exclusion.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from jcodemunch_mcp.progress import (
    HeartbeatReporter,
    HEARTBEAT_INTERVAL_S,
    _heartbeat_interval,
    drain_reporter,
    make_progress_notify,
)


class _Clock:
    """Manual monotonic clock so elapsed-time behaviour is deterministic."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _reporter(clock, interval=30.0):
    lines: list[str] = []
    r = HeartbeatReporter(
        "Index",
        interval=interval,
        clock=clock,
        log=lambda fmt, *a: lines.append(fmt % a if a else fmt),
    )
    return r, lines


# ── #383: heartbeat ─────────────────────────────────────────────────────────


class TestHeartbeatSilenceOnFastRuns:
    def test_fast_run_emits_nothing(self):
        """The whole point of the interval: a normal run stays as quiet as before.

        Post-v1.108.171 an index finishes in seconds. If those runs started
        logging WARNINGs, the fix would be a worse bug than the silence.
        """
        clock = _Clock()
        r, lines = _reporter(clock)
        r.start(total=100)
        for i in range(1, 101):
            clock.advance(0.05)  # 5s total, well under the interval
            r.update(i, 100, "indexing")
        r.finish()
        assert lines == []

    def test_start_emits_nothing(self):
        clock = _Clock()
        r, lines = _reporter(clock)
        r.start(total=10)
        assert lines == []


class TestHeartbeatOnLongRuns:
    def test_emits_once_per_interval(self):
        clock = _Clock()
        r, lines = _reporter(clock, interval=30.0)
        r.start(total=100)

        clock.advance(29.0)
        r.update(10, 100)
        assert lines == []  # not yet a full interval

        clock.advance(2.0)  # 31s
        r.update(20, 100)
        assert len(lines) == 1

        clock.advance(10.0)  # 41s — 10s since last emit
        r.update(30, 100)
        assert len(lines) == 1  # throttled

        clock.advance(25.0)  # 66s — 35s since last emit
        r.update(40, 100)
        assert len(lines) == 2

    def test_first_line_explains_why_there_is_no_progress_bar(self):
        """An unexplained WARNING reads as an error. Say what it substitutes for."""
        clock = _Clock()
        r, lines = _reporter(clock)
        r.start(total=100)
        clock.advance(31.0)
        r.update(5, 100)

        assert "progressToken" in lines[0]
        assert "JCODEMUNCH_HEARTBEAT_SECONDS=0" in lines[0]

        clock.advance(31.0)
        r.update(6, 100)
        # Said once, not on every beat.
        assert "progressToken" not in lines[1]

    def test_line_carries_elapsed_and_counts(self):
        clock = _Clock()
        r, lines = _reporter(clock)
        r.start(total=90)
        clock.advance(45.0)
        r.update(30, 90, "parsing")

        assert "45s" in lines[0]
        assert "30/90" in lines[0]
        assert "parsing" in lines[0]

    def test_finish_closes_the_loop_only_if_it_spoke(self):
        clock = _Clock()
        r, lines = _reporter(clock)
        r.start(total=10)
        clock.advance(31.0)
        r.update(1, 10)
        assert len(lines) == 1

        clock.advance(5.0)
        r.finish("ok")
        assert len(lines) == 2
        assert "finished after 36s" in lines[1]

    def test_finish_is_silent_when_no_heartbeat_was_emitted(self):
        """A run nobody worried about gets no completion line either."""
        clock = _Clock()
        r, lines = _reporter(clock)
        r.start(total=10)
        clock.advance(2.0)
        r.finish("ok")
        assert lines == []


class TestHeartbeatDisabling:
    def test_zero_interval_disables(self):
        clock = _Clock()
        r, lines = _reporter(clock, interval=0.0)
        assert r.enabled is False
        r.start(total=10)
        clock.advance(10_000.0)
        r.update(1, 10)
        r.finish()
        assert lines == []

    def test_negative_interval_disables(self):
        clock = _Clock()
        r, _ = _reporter(clock, interval=-5.0)
        assert r.enabled is False

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("JCODEMUNCH_HEARTBEAT_SECONDS", "5")
        assert _heartbeat_interval() == 5.0

    def test_env_zero_disables(self, monkeypatch):
        monkeypatch.setenv("JCODEMUNCH_HEARTBEAT_SECONDS", "0")
        assert _heartbeat_interval() == 0.0
        assert HeartbeatReporter("Index").enabled is False

    def test_garbage_env_falls_back_to_default_not_to_silence(self, monkeypatch):
        """A typo must not reintroduce the exact failure this fixes."""
        monkeypatch.setenv("JCODEMUNCH_HEARTBEAT_SECONDS", "thirty")
        assert _heartbeat_interval() == HEARTBEAT_INTERVAL_S
        assert HeartbeatReporter("Index").enabled is True

    def test_unset_env_uses_default(self, monkeypatch):
        monkeypatch.delenv("JCODEMUNCH_HEARTBEAT_SECONDS", raising=False)
        assert _heartbeat_interval() == HEARTBEAT_INTERVAL_S


class TestHeartbeatIsNotAProtocolViolation:
    """The reason 'just notify anyway' was rejected, made structural.

    MCP makes progressToken the client's opt-in. The heartbeat must therefore
    have no path to the session at all — not merely refrain from using one.
    """

    def test_heartbeat_holds_no_notify_channel(self):
        r = HeartbeatReporter("Index")
        assert not hasattr(r, "_notify")
        assert "_notify" not in HeartbeatReporter.__slots__

    async def test_drain_is_a_noop(self):
        """close() yields no futures, so nothing can trail the response."""
        r = HeartbeatReporter("Index")
        assert r.close() == []
        await drain_reporter(r)  # must not raise

    def test_thread_safety_under_concurrent_updates(self):
        """update() is called from inside asyncio.to_thread workers."""
        import threading

        clock = _Clock()
        r, lines = _reporter(clock, interval=0.0001)
        r.start(total=1000)

        def worker():
            for i in range(200):
                r.update(i, 1000, "x")

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        clock.advance(10.0)
        for t in threads:
            t.join()
        # No assertion on count (timing-dependent); the pin is that concurrent
        # updates neither raise nor corrupt state.
        assert r.close() == []


class TestMakeProgressNotifyStillGatesOnToken:
    """The heartbeat is a FALLBACK. It must not have loosened the gate."""

    def test_returns_none_without_token(self):
        class _Meta:
            progressToken = None

        class _Ctx:
            meta = _Meta()
            session = object()

        class _Server:
            request_context = _Ctx()

        assert make_progress_notify(_Server()) is None

    def test_returns_none_when_meta_absent(self):
        class _Ctx:
            meta = None
            session = object()

        class _Server:
            request_context = _Ctx()

        assert make_progress_notify(_Server()) is None


# ── #384: no double index ───────────────────────────────────────────────────


def _mock_manager(is_watched=False, takeover_status="lock_failed"):
    from jcodemunch_mcp.watcher import WatcherManager

    mgr = AsyncMock(spec=WatcherManager)
    mgr.is_watched.return_value = is_watched
    mgr.maybe_takeover = AsyncMock(return_value={"status": takeover_status})
    mgr.ensure_indexed = AsyncMock(return_value={"status": "indexed"})
    mgr.add_folder = AsyncMock(return_value={"status": "started"})
    return mgr


@pytest.fixture
def watch_on(monkeypatch):
    from jcodemunch_mcp import server

    monkeypatch.setattr(
        server.config_module, "get",
        lambda k, d=False, **kw: True if k == "watch" else d,
    )
    return server


class TestIndexFolderIsDeferredNotExcluded:
    def test_index_folder_is_not_in_the_exclusion_set(self, watch_on):
        """The blunt fix was rejected: excluding it removes auto-start-watching.

        That is a behaviour removal under the 1.x zero-surprise contract, and
        index_folder is the most natural way a user asks for a folder to be
        watched. Pinned so a future cleanup does not quietly take it.
        """
        assert "index_folder" not in watch_on._AUTO_WATCH_EXCLUDED
        assert "index_folder" in watch_on._AUTO_WATCH_DEFERRED

    def test_index_file_stays_excluded(self, watch_on):
        """Unchanged: its path arg is a file, not a folder."""
        assert "index_file" in watch_on._AUTO_WATCH_EXCLUDED
        assert "index_file" not in watch_on._AUTO_WATCH_DEFERRED

    async def test_hook_indexes_nothing_before_dispatch(self, tmp_path, watch_on, monkeypatch):
        mgr = _mock_manager()
        monkeypatch.setattr(watch_on, "_watcher_manager", mgr)

        folder = tmp_path / "repo"
        folder.mkdir()

        deferred = await watch_on._auto_watch_if_needed(
            "index_folder", {"path": str(folder)}, None
        )

        assert deferred == str(folder.resolve())
        mgr.ensure_indexed.assert_not_called()
        mgr.add_folder.assert_not_called()

    async def test_post_dispatch_registers_without_reindexing(self, tmp_path, watch_on, monkeypatch):
        """The whole fix in one assertion: watched, and indexed exactly once."""
        mgr = _mock_manager()
        monkeypatch.setattr(watch_on, "_watcher_manager", mgr)

        folder = str((tmp_path / "repo").resolve())
        await watch_on._auto_watch_after_tool(folder)

        mgr.ensure_indexed.assert_not_called()
        mgr.add_folder.assert_called_once()
        assert mgr.add_folder.call_args[1]["skip_initial_index"] is True

    async def test_already_watched_short_circuits(self, tmp_path, watch_on, monkeypatch):
        mgr = _mock_manager(is_watched=True)
        monkeypatch.setattr(watch_on, "_watcher_manager", mgr)

        await watch_on._auto_watch_after_tool(str(tmp_path))
        mgr.add_folder.assert_not_called()

    async def test_takeover_does_not_reindex_on_the_deferred_path(self, tmp_path, watch_on, monkeypatch):
        """The eager path calls ensure_indexed after a takeover. This one must not.

        The tool that just ran is what made the index current.
        """
        mgr = _mock_manager(takeover_status="started")
        monkeypatch.setattr(watch_on, "_watcher_manager", mgr)

        await watch_on._auto_watch_after_tool(str(tmp_path))

        mgr.ensure_indexed.assert_not_called()
        mgr.add_folder.assert_not_called()

    async def test_no_watcher_manager_is_a_noop(self, tmp_path, watch_on, monkeypatch):
        monkeypatch.setattr(watch_on, "_watcher_manager", None)
        await watch_on._auto_watch_after_tool(str(tmp_path))  # must not raise

    async def test_add_folder_failure_is_swallowed(self, tmp_path, watch_on, monkeypatch):
        """A watch registration must never surface as a tool failure."""
        mgr = _mock_manager()
        mgr.add_folder = AsyncMock(side_effect=RuntimeError("lock gone"))
        monkeypatch.setattr(watch_on, "_watcher_manager", mgr)

        await watch_on._auto_watch_after_tool(str(tmp_path))  # must not raise


class TestWatchTaskSkipsItsOwnInitialIndex:
    async def test_skip_initial_index_does_not_call_index_folder(self, tmp_path, monkeypatch):
        """The saved pass, measured at the only place that can prove it."""
        from jcodemunch_mcp import watcher

        calls = []

        def _fake_index_folder(**kwargs):
            calls.append(kwargs)
            return {"success": True, "message": "1 symbol", "duration_seconds": 0.1}

        monkeypatch.setattr(watcher, "index_folder", _fake_index_folder)
        monkeypatch.setattr(watcher, "_require_watchfiles", lambda: None)

        folder = tmp_path / "repo"
        folder.mkdir()
        (folder / "a.py").write_text("def f(): pass\n")

        # awatch is never reached — cancel once the initial phase is done.
        task = asyncio.create_task(
            watcher._watch_single(
                folder_path=str(folder),
                debounce_ms=100,
                use_ai_summaries=False,
                storage_path=str(tmp_path / "store"),
                extra_ignore_patterns=None,
                follow_symlinks=False,
                quiet=True,
                skip_initial_index=True,
            )
        )
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

        assert calls == [], "skip_initial_index must not run an index pass"

    async def test_default_still_indexes(self, tmp_path, monkeypatch):
        """Non-vacuity: the same harness DOES index when the flag is off.

        Without this, the assertion above would pass on a broken harness that
        never reached the initial-index block at all.
        """
        from jcodemunch_mcp import watcher

        calls = []

        def _fake_index_folder(**kwargs):
            calls.append(kwargs)
            return {"success": True, "message": "1 symbol", "duration_seconds": 0.1}

        monkeypatch.setattr(watcher, "index_folder", _fake_index_folder)
        monkeypatch.setattr(watcher, "_require_watchfiles", lambda: None)

        folder = tmp_path / "repo"
        folder.mkdir()
        (folder / "a.py").write_text("def f(): pass\n")

        task = asyncio.create_task(
            watcher._watch_single(
                folder_path=str(folder),
                debounce_ms=100,
                use_ai_summaries=False,
                storage_path=str(tmp_path / "store"),
                extra_ignore_patterns=None,
                follow_symlinks=False,
                quiet=True,
                skip_initial_index=False,
            )
        )
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

        assert len(calls) == 1

    async def test_skip_records_who_made_the_index_fresh(self, tmp_path, monkeypatch):
        """get_watch_status has no other way to learn the index is current.

        Recording it keeps the claim checkable instead of presenting the
        caller's work as the watcher's own.
        """
        from jcodemunch_mcp import watcher

        recorded = {}

        def _fake_done(repo, result=None):
            recorded["repo"] = repo
            recorded["result"] = result

        monkeypatch.setattr(watcher, "index_folder", lambda **kw: {"success": True})
        monkeypatch.setattr(watcher, "_require_watchfiles", lambda: None)
        monkeypatch.setattr(watcher, "mark_reindex_done", _fake_done)

        folder = tmp_path / "repo"
        folder.mkdir()

        task = asyncio.create_task(
            watcher._watch_single(
                folder_path=str(folder),
                debounce_ms=100,
                use_ai_summaries=False,
                storage_path=str(tmp_path / "store"),
                extra_ignore_patterns=None,
                follow_symlinks=False,
                quiet=True,
                skip_initial_index=True,
                record_index_ready=True,
            )
        )
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

        assert recorded["result"]["skipped_initial_index"] is True
        assert recorded["result"]["success"] is True
