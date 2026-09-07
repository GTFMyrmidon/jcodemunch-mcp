"""Native registration must not traverse a workspace's symlink graph."""

import asyncio
import os
from contextlib import aclosing
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from jcodemunch_mcp import watcher


@pytest.fixture
def workspace(tmp_path):
    for name in ("a", "b", "c"):
        package = tmp_path / "packages" / name
        (package / "src").mkdir(parents=True)
        (package / "node_modules").mkdir()
        (package / "src" / "code.py").write_text("def original(): pass\n")
    try:
        # Fan-out plus cycles: real paths stay small, alias paths do not.
        for name in ("a", "b", "c"):
            for target in ("a", "b", "c"):
                (tmp_path / "packages" / name / "node_modules" / target).symlink_to(
                    tmp_path / "packages" / target, target_is_directory=True
                )
        (tmp_path / "alias").symlink_to(tmp_path / "packages", target_is_directory=True)
        (tmp_path / "loop").symlink_to(tmp_path, target_is_directory=True)
        (tmp_path / "broken").symlink_to(tmp_path / "missing", target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks unavailable")
    return tmp_path


def test_directory_walk_is_bounded_by_real_tree(workspace):
    paths = watcher._watch_directories(str(workspace))
    assert len(paths) == 8  # root, packages, three packages and their src dirs
    assert all("node_modules" not in p for p in paths)
    assert all(not os.path.islink(p) for p in paths)
    assert all(len(identity) == 2 for identity in paths.values())


def test_external_symlink_and_hidden_directory_are_not_registered(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".hidden").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (root / "external").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks unavailable")
    assert set(watcher._watch_directories(str(root))) == {str(root)}


def test_directory_identity_changes_on_same_path_replacement(tmp_path):
    child = tmp_path / "child"
    child.mkdir()
    before = watcher._watch_directories(str(tmp_path))
    child.rename(tmp_path / "old")
    child.mkdir()
    after = watcher._watch_directories(str(tmp_path))
    assert before[str(child)] != after[str(child)]


@pytest.mark.asyncio
async def test_native_registration_is_always_nonrecursive(workspace):
    watchfiles = pytest.importorskip("watchfiles")
    calls = []
    closed = []

    async def fake_awatch(*paths, **kwargs):
        calls.append((paths, kwargs))
        try:
            yield {(watchfiles.Change.modified, str(workspace / "file.py"))}
        finally:
            closed.append(True)

    with patch.object(watchfiles, "awatch", fake_awatch):
        async with aclosing(watcher._safe_awatch(str(workspace), 200)) as stream:
            assert await anext(stream)
    assert len(calls) == 1
    assert set(calls[0][0]) == set(watcher._watch_directories(str(workspace)))
    assert calls[0][1]["recursive"] is False
    assert calls[0][1]["yield_on_timeout"] is True
    assert closed == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["add", "delete", "replace"])
async def test_topology_refresh_closes_old_watch_and_requests_rescan(tmp_path, operation):
    watchfiles = pytest.importorskip("watchfiles")
    root = str(tmp_path)
    child = tmp_path / "child"
    if operation != "add":
        child.mkdir()
    calls = []
    closed = []

    async def fake_awatch(*paths, **kwargs):
        calls.append(paths)
        generation = len(calls)
        if generation == 2:
            assert closed == [1]  # no accumulation of native watchers
        try:
            if generation == 1:
                if operation == "add":
                    (child / "nested").mkdir(parents=True)
                    (child / "nested" / "new.py").write_text("def new(): pass\n")
                elif operation == "delete":
                    child.rmdir()
                else:
                    child.rename(tmp_path / "old")
                    child.mkdir()
            yield set()  # timeout must detect topology even without events
        finally:
            closed.append(generation)

    ticks = iter(range(0, 1000, 61))
    with patch.object(watchfiles, "awatch", fake_awatch), patch.object(
        watcher, "time", SimpleNamespace(monotonic=lambda: next(ticks))
    ):
        async with aclosing(watcher._safe_awatch(root, 200)) as stream:
            assert await anext(stream) == {(watchfiles.Change.modified, root)}
    assert len(calls) == 2
    assert set(calls[1]) == set(watcher._watch_directories(root))
    assert closed == [1, 2]


@pytest.mark.asyncio
async def test_regular_edits_do_not_rescan_directory_tree(tmp_path):
    watchfiles = pytest.importorskip("watchfiles")
    target = tmp_path / "code.py"
    target.write_text("pass\n")

    async def fake_awatch(*paths, **kwargs):
        for _ in range(3):
            yield {(watchfiles.Change.modified, str(target))}

    with patch.object(watchfiles, "awatch", fake_awatch), patch.object(
        watcher, "_watch_directories", wraps=watcher._watch_directories
    ) as discover, patch.object(watcher, "time", SimpleNamespace(monotonic=lambda: 0)):
        assert len([batch async for batch in watcher._safe_awatch(str(tmp_path), 200)]) == 3
    assert discover.call_count == 1


@pytest.mark.asyncio
async def test_missing_root_fails_instead_of_silently_stopping(tmp_path):
    pytest.importorskip("watchfiles")
    async with aclosing(watcher._safe_awatch(str(tmp_path / "gone"), 200)) as stream:
        with pytest.raises(FileNotFoundError):
            await anext(stream)


@pytest.mark.asyncio
async def test_root_reconciliation_uses_full_incremental_index(tmp_path):
    watchfiles = pytest.importorskip("watchfiles")
    root = str(tmp_path)

    async def changes(*args):
        yield {(watchfiles.Change.modified, root)}

    result = {"success": True, "message": "No changes detected"}
    with patch.object(watcher, "_safe_awatch", changes), patch.object(
        watcher, "index_folder", return_value=result
    ) as index, patch.object(watcher, "IndexStore", return_value=MagicMock()), patch.object(
        watcher, "mark_reindex_start"
    ), patch.object(watcher, "mark_reindex_done"):
        await watcher._watch_single(
            root, 200, False, None, None, False,
            skip_initial_index=True, quiet=True,
        )
    assert index.call_count == 1
    assert index.call_args.kwargs["changed_paths"] is None
    assert index.call_args.kwargs["incremental"] is True


@pytest.mark.asyncio
async def test_real_watcher_rearms_new_tree_and_reconciles_deletion(workspace):
    """Exercise nested edits and topology with real native watches and symlink cycles."""
    watchfiles = pytest.importorskip("watchfiles")
    root = str(workspace)
    marker = workspace / "packages" / "a" / "src" / "code.py"
    observed = asyncio.Queue()

    async def consume():
        async with aclosing(watcher._safe_awatch(root, 200)) as stream:
            async for batch in stream:
                await observed.put(batch)

    async def wait_for_path(path):
        while True:
            batch = await observed.get()
            if any(p == str(path) for _, p in batch):
                return batch

    async def write_until_observed(path):
        async def edit():
            while True:
                path.write_text("def changed(): pass\n")
                await asyncio.sleep(0.1)
        editor = asyncio.create_task(edit())
        try:
            await asyncio.wait_for(wait_for_path(path), 10)
        finally:
            editor.cancel()
            await asyncio.gather(editor, return_exceptions=True)

    consumer = asyncio.create_task(consume())
    try:
        await write_until_observed(marker)  # proves native registration is ready
        target = workspace / "new" / "nested" / "code.py"
        target.parent.mkdir(parents=True)
        target.write_text("def before_registration(): pass\n")
        assert await asyncio.wait_for(wait_for_path(root), 10) == {
            (watchfiles.Change.modified, root)
        }
        await write_until_observed(target)  # new nested directory is watched
        target.unlink()
        target.parent.rmdir()
        target.parent.parent.rmdir()
        await asyncio.wait_for(wait_for_path(root), 10)
    finally:
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)
