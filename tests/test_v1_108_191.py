"""v1.108.191 — the eager auto-watch path indexes ONCE.

Found while porting #388. Both arms of `_auto_watch_if_needed` indexed twice,
and neither was the bug #384 reported:

* the takeover arm started a watch task (whose initial index runs as a
  CONCURRENT asyncio task) and then awaited `ensure_indexed` on the same
  folder, putting two writers on the `indexwrite` lock where every acquire
  waits up to 60s;
* the fall-through arm awaited `ensure_indexed` and then called `add_folder`,
  whose watch task walked the same tree a second time. Serialized, so not a
  race, but a redundant full walk on EVERY eager auto-watch, not just
  `index_folder`.

The second one is older and wider than #384 ever was: it fired for any tool
touching an unwatched repo.
"""

import asyncio
import inspect
from unittest.mock import AsyncMock

import pytest

from jcodemunch_mcp import server, watcher
from jcodemunch_mcp.watcher import WatcherManager


@pytest.fixture
def watch_on(monkeypatch):
    monkeypatch.setattr(
        server.config_module, "get",
        lambda k, d=False, **kw: True if k == "watch" else d,
    )
    return server


def _mgr(takeover_status="lock_failed"):
    m = AsyncMock(spec=WatcherManager)
    m.is_watched.return_value = False
    m.maybe_takeover = AsyncMock(return_value={"status": takeover_status})
    m.ensure_indexed = AsyncMock(return_value={"status": "indexed"})
    m.add_folder = AsyncMock(return_value={"status": "started"})
    return m


class TestEagerPathIndexesExactlyOnce:
    async def test_fall_through_arm(self, tmp_path, watch_on, monkeypatch):
        m = _mgr(takeover_status="lock_failed")
        monkeypatch.setattr(watch_on, "_watcher_manager", m)

        await watch_on._auto_watch_if_needed(
            "search_symbols", {"path": str(tmp_path)}, None
        )

        m.ensure_indexed.assert_awaited_once()
        m.add_folder.assert_awaited_once()
        assert m.add_folder.await_args[1]["skip_initial_index"] is True

    async def test_takeover_arm(self, tmp_path, watch_on, monkeypatch):
        """The arm that was an actual race, not just waste."""
        m = _mgr(takeover_status="started")
        monkeypatch.setattr(watch_on, "_watcher_manager", m)

        await watch_on._auto_watch_if_needed(
            "search_symbols", {"path": str(tmp_path)}, None
        )

        m.ensure_indexed.assert_awaited_once()
        assert m.maybe_takeover.await_args[1]["skip_initial_index"] is True
        m.add_folder.assert_not_called()

    async def test_index_happens_before_any_watch_task_starts(
        self, tmp_path, watch_on, monkeypatch
    ):
        """Ordering is the fix, not just the flag.

        A watch task started before `ensure_indexed` completes builds its hash
        cache from an index that is about to be rewritten underneath it.
        """
        order = []

        m = AsyncMock(spec=WatcherManager)
        m.is_watched.return_value = False

        async def _ensure(*a, **k):
            order.append("ensure_indexed")
            return {"status": "indexed"}

        async def _takeover(*a, **k):
            order.append("maybe_takeover")
            return {"status": "lock_failed"}

        async def _add(*a, **k):
            order.append("add_folder")
            return {"status": "started"}

        m.ensure_indexed = _ensure
        m.maybe_takeover = _takeover
        m.add_folder = _add
        monkeypatch.setattr(watch_on, "_watcher_manager", m)

        await watch_on._auto_watch_if_needed(
            "search_symbols", {"path": str(tmp_path)}, None
        )

        assert order == ["ensure_indexed", "maybe_takeover", "add_folder"]
        assert order.index("ensure_indexed") == 0


class TestRecordIndexReadyIsSeparateFromSkip:
    """The two skipping callers differ in whether they record, so one flag
    could not serve both without either losing the signal or clobbering it."""

    def test_flags_are_independent_parameters(self):
        for fn in (WatcherManager.add_folder, WatcherManager.maybe_takeover,
                   WatcherManager._start_watch_task, watcher._watch_single):
            params = inspect.signature(fn).parameters
            assert "skip_initial_index" in params, fn.__name__
            assert "record_index_ready" in params, fn.__name__
            assert params["record_index_ready"].default is False, fn.__name__

    async def test_eager_path_does_not_record(self, tmp_path, watch_on, monkeypatch):
        """`ensure_indexed` writes a real record with the real result.

        A synthetic one written over it would replace a measurement with a
        placeholder, and `get_watch_status` would report the placeholder.
        """
        m = _mgr(takeover_status="lock_failed")
        monkeypatch.setattr(watch_on, "_watcher_manager", m)

        await watch_on._auto_watch_if_needed(
            "search_symbols", {"path": str(tmp_path)}, None
        )

        assert m.add_folder.await_args[1].get("record_index_ready", False) is False

    async def test_deferred_path_does_record(self, tmp_path, watch_on, monkeypatch):
        """The index_folder tool writes no reindex record, so nobody else will."""
        m = _mgr(takeover_status="lock_failed")
        monkeypatch.setattr(watch_on, "_watcher_manager", m)

        await watch_on._auto_watch_after_tool(str(tmp_path))

        assert m.add_folder.await_args[1]["record_index_ready"] is True
        assert m.maybe_takeover.await_args[1]["record_index_ready"] is True

    async def test_skip_without_record_writes_nothing(self, tmp_path, monkeypatch):
        """The behavioural half of the split, at the only place that shows it."""
        recorded = []

        monkeypatch.setattr(watcher, "_require_watchfiles", lambda: None)
        monkeypatch.setattr(watcher, "index_folder", lambda **kw: {"success": True})
        monkeypatch.setattr(
            watcher, "mark_reindex_done",
            lambda repo, result=None: recorded.append(result),
        )

        folder = tmp_path / "repo"
        folder.mkdir()

        task = asyncio.create_task(
            watcher._watch_single(
                folder_path=str(folder),
                debounce_ms=50,
                use_ai_summaries=False,
                storage_path=str(tmp_path / "store"),
                extra_ignore_patterns=None,
                follow_symlinks=False,
                quiet=True,
                skip_initial_index=True,
                record_index_ready=False,
            )
        )
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

        assert recorded == [], "must not overwrite the caller's real record"

    async def test_skip_with_record_writes_the_synthetic_record(
        self, tmp_path, monkeypatch
    ):
        """Non-vacuity for the test above."""
        recorded = []

        monkeypatch.setattr(watcher, "_require_watchfiles", lambda: None)
        monkeypatch.setattr(watcher, "index_folder", lambda **kw: {"success": True})
        monkeypatch.setattr(
            watcher, "mark_reindex_done",
            lambda repo, result=None: recorded.append(result),
        )

        folder = tmp_path / "repo"
        folder.mkdir()

        task = asyncio.create_task(
            watcher._watch_single(
                folder_path=str(folder),
                debounce_ms=50,
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

        assert len(recorded) == 1
        assert recorded[0]["skipped_initial_index"] is True


class TestStandbyLoopsStillIndex:
    """The internal callers that must keep the old behaviour.

    `_watch_standby_signal` and the fallback retry loop call `maybe_takeover`
    when nothing else is indexing. Passing the skip there would adopt an index
    nobody made.
    """

    def test_internal_takeover_calls_pass_no_flags(self):
        src = inspect.getsource(watcher.WatcherManager)
        # Both internal call sites are bare `await self.maybe_takeover(folder)`.
        assert src.count("await self.maybe_takeover(folder)") == 2
        assert "self.maybe_takeover(folder, skip_initial_index" not in src
