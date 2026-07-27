"""v1.108.190 — the takeover half of the #384 double index.

Ported from @Bortlesboat's independent fix for #384 (PR #388). v1.108.189 fixed
`add_folder` and left `maybe_takeover` doing a full pass over a tree the tool had
just indexed, so the double index survived for exactly the case where another
process had been watching the folder. Their PR covered that path; ours did not.

Also adopts their lazy hash-cache hydration, which closes a window that predates
both fixes: a watch task whose initial index FAILS never builds its cache, and an
empty cache silently drops every `old_hash` rather than failing.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from jcodemunch_mcp.watcher import WatcherManager


class _Recorder:
    """Capture _start_watch_task calls without starting a real task."""

    def __init__(self):
        self.calls = []

    def __call__(self, folder, skip_initial_index=False, record_index_ready=False):
        self.calls.append((folder, skip_initial_index))
        return None


def _manager(tmp_path):
    return WatcherManager(storage_path=str(tmp_path / "store"), quiet=True)


class TestMaybeTakeoverDefaultIsUnchanged:
    """The standby loops call this when nothing else is indexing.

    `_watch_standby_signal` and the fallback retry loop both take over precisely
    so the folder gets indexed. Flipping this default would silently stop that,
    and nothing else in the suite would notice.
    """

    def test_default_is_false(self):
        import inspect

        sig = inspect.signature(WatcherManager.maybe_takeover)
        param = sig.parameters["skip_initial_index"]
        assert param.default is False
        assert param.kind is inspect.Parameter.KEYWORD_ONLY

    async def test_default_call_runs_the_initial_index(self, tmp_path, monkeypatch):
        mgr = _manager(tmp_path)
        rec = _Recorder()
        monkeypatch.setattr(mgr, "_start_watch_task", rec)
        monkeypatch.setattr("jcodemunch_mcp.watcher._acquire_lock", lambda *a, **k: True)

        folder = str((tmp_path / "repo").resolve())
        result = await mgr.maybe_takeover(folder)

        assert result["status"] == "started"
        assert rec.calls == [(folder, False)]

    async def test_explicit_skip_is_threaded_through(self, tmp_path, monkeypatch):
        mgr = _manager(tmp_path)
        rec = _Recorder()
        monkeypatch.setattr(mgr, "_start_watch_task", rec)
        monkeypatch.setattr("jcodemunch_mcp.watcher._acquire_lock", lambda *a, **k: True)

        folder = str((tmp_path / "repo").resolve())
        result = await mgr.maybe_takeover(folder, skip_initial_index=True)

        assert result["status"] == "started"
        assert rec.calls == [(folder, True)]


class TestDeferredPathTakeoverSkips:
    """The gap PR #388 caught: v1.108.189 fixed add_folder and not takeover."""

    @pytest.fixture
    def watch_on(self, monkeypatch):
        from jcodemunch_mcp import server

        monkeypatch.setattr(
            server.config_module, "get",
            lambda k, d=False, **kw: True if k == "watch" else d,
        )
        return server

    async def test_takeover_is_asked_to_skip(self, tmp_path, watch_on, monkeypatch):
        mgr = AsyncMock(spec=WatcherManager)
        mgr.is_watched.return_value = False
        mgr.maybe_takeover = AsyncMock(return_value={"status": "started"})
        mgr.ensure_indexed = AsyncMock()
        mgr.add_folder = AsyncMock(return_value={"status": "started"})
        monkeypatch.setattr(watch_on, "_watcher_manager", mgr)

        await watch_on._auto_watch_after_tool(str(tmp_path))

        mgr.maybe_takeover.assert_awaited_once()
        assert mgr.maybe_takeover.await_args[1]["skip_initial_index"] is True
        # Still no reindex on this path, and no add_folder after a takeover.
        mgr.ensure_indexed.assert_not_called()
        mgr.add_folder.assert_not_called()

    async def test_falls_through_to_add_folder_when_takeover_fails(
        self, tmp_path, watch_on, monkeypatch
    ):
        """Non-vacuity: the skip must reach add_folder too, not only takeover."""
        mgr = AsyncMock(spec=WatcherManager)
        mgr.is_watched.return_value = False
        mgr.maybe_takeover = AsyncMock(return_value={"status": "lock_failed"})
        mgr.ensure_indexed = AsyncMock()
        mgr.add_folder = AsyncMock(return_value={"status": "started"})
        monkeypatch.setattr(watch_on, "_watcher_manager", mgr)

        await watch_on._auto_watch_after_tool(str(tmp_path))

        assert mgr.maybe_takeover.await_args[1]["skip_initial_index"] is True
        mgr.add_folder.assert_called_once()
        assert mgr.add_folder.call_args[1]["skip_initial_index"] is True
        mgr.ensure_indexed.assert_not_called()


class TestLazyHashCacheHydration:
    """Predates both fixes: a FAILED initial index leaves the cache empty forever.

    An empty `_hash_cache` does not raise. Every `WatcherChange` just loses its
    `old_hash`, which is the quiet kind of wrong.
    """

    async def _run_until_cancelled(self, coro_task, seconds=0.25):
        await asyncio.sleep(seconds)
        coro_task.cancel()
        try:
            await coro_task
        except (asyncio.CancelledError, Exception):
            pass

    async def test_failed_initial_index_leaves_cache_unbuilt_then_hydrates(
        self, tmp_path, monkeypatch
    ):
        from jcodemunch_mcp import watcher

        builds = []

        monkeypatch.setattr(watcher, "_require_watchfiles", lambda: None)
        monkeypatch.setattr(
            watcher, "index_folder",
            lambda **kw: {"success": False, "error": "boom"},
        )

        folder = tmp_path / "repo"
        folder.mkdir()

        real_load = watcher.IndexStore.load_index

        def _counting_load(self, owner, name):
            builds.append((owner, name))
            return real_load(self, owner, name)

        monkeypatch.setattr(watcher.IndexStore, "load_index", _counting_load)

        task = asyncio.create_task(
            watcher._watch_single(
                folder_path=str(folder),
                debounce_ms=50,
                use_ai_summaries=False,
                storage_path=str(tmp_path / "store"),
                extra_ignore_patterns=None,
                follow_symlinks=False,
                quiet=True,
            )
        )
        await self._run_until_cancelled(task)

        # A failed initial index must NOT have built the cache. That is the
        # precondition the lazy guard exists to recover from; if this ever
        # becomes non-zero the guard is dead code and should be removed.
        assert builds == []

    def test_guard_uses_a_flag_not_emptiness(self):
        """`if not _hash_cache` would re-read the store on every change for a
        legitimately empty index. The flag distinguishes empty from unbuilt."""
        import inspect

        src = inspect.getsource(
            __import__("jcodemunch_mcp.watcher", fromlist=["_watch_single"])._watch_single
        )
        assert "if not _hash_cache_built:" in src
        assert "if not _hash_cache:" not in src
