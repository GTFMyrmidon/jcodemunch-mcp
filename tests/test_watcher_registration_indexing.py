"""Native registration races must leave the persisted symbol index current."""

import asyncio
from contextlib import aclosing
import json
import shutil
import subprocess

import pytest

from jcodemunch_mcp import watcher


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["initial", "delete", "rename"])
async def test_registration_race_updates_persisted_symbols(tmp_path, monkeypatch, operation):
    watchfiles = pytest.importorskip("watchfiles")
    root = tmp_path / "project"
    child = root / "child"
    child.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    target = root / "code.py"
    target.write_text("def before_arm(): pass\n")
    (child / "nested.py").write_text("def nested_symbol(): pass\n")
    storage = str(tmp_path / "index")
    result = watcher.index_folder(
        path=str(root), storage_path=storage, use_ai_summaries=False,
        context_providers=False,
    )
    assert result["success"]
    store = watcher.IndexStore(base_path=storage)
    owner, name = result["repo"].split("/", 1)

    def symbols():
        index = store.load_index(owner, name)
        return sorted((symbol["name"], symbol["file"]) for symbol in index.symbols)

    before = symbols()
    assert ("before_arm", "code.py") in before
    native_awatch = watchfiles.awatch
    attempts, closed = [], []

    async def race(*paths, **kwargs):
        attempt = len(attempts)
        if attempt:
            assert attempt - 1 in closed
        attempts.append(sorted(str_path.removeprefix(str(root)) or "." for str_path in paths))
        assert kwargs["recursive"] is False
        if attempt == 0:
            # Exactly one edit after indexing/enumeration, before native registration.
            target.write_text("def during_arm(): pass\n")
            if operation == "delete":
                shutil.rmtree(child)
            elif operation == "rename":
                child.rename(root / "renamed")
        try:
            async with aclosing(native_awatch(*paths, force_polling=False, **kwargs)) as stream:
                async for changes in stream:
                    yield changes
        finally:
            closed.append(attempt)

    monkeypatch.setattr(watchfiles, "awatch", race)
    task = asyncio.create_task(watcher._watch_single(
        str(root), 200, False, storage, None, False,
        skip_initial_index=True, quiet=True, context_providers=False,
    ))

    async def wait_for_symbol(symbol):
        async def observe():
            while (symbol, "code.py") not in symbols():
                if task.done():
                    task.result()
                    pytest.fail("Watcher stopped before updating the index")
                await asyncio.sleep(0.05)
        await asyncio.wait_for(observe(), 10)
        return symbols()

    try:
        reconciled = await wait_for_symbol("during_arm")
        assert ("before_arm", "code.py") not in reconciled
        assert len(attempts) == (1 if operation == "initial" else 2)
        if operation == "delete":
            assert not any(symbol == "nested_symbol" for symbol, _ in reconciled)
        elif operation == "rename":
            assert ("nested_symbol", "renamed/nested.py") in reconciled
            assert ("nested_symbol", "child/nested.py") not in reconciled
        target.write_text("def after_recovery(): pass\n")
        subsequent = await wait_for_symbol("after_recovery")
        assert ("during_arm", "code.py") not in subsequent
        assert not task.done()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert sorted(closed) == list(range(len(attempts)))
    print(json.dumps({
        "scenario": operation, "native_registration_sets": attempts,
        "persisted_symbols_before": before,
        "persisted_symbols_after_registration": reconciled,
        "persisted_symbols_after_one_subsequent_edit": subsequent,
        "all_native_streams_closed": True,
    }, sort_keys=True))
