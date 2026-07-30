"""A future-version index must never look like an absent symbol.

Adversarial reproduction requested on #377 (@mightydanp), sourced from
@tgarrick-bit's #389 confirmation:

    a newer server upgrades the index schema
    -> an older long-running MCP process remains attached
    -> the old process reports the index as sqlite_future_version
    -> search_symbols returns an empty result instead of a structured error

The behaviour did not reproduce on current main, and the guard
(`if not index: return index_status_to_tool_error(...)`) plus the translator in
`tools/_utils.py` are byte-identical back to v1.108.20 -- the very build the
reporter was pinned to. Which is exactly why this file exists: the invariant was
being upheld by convention across many call sites and pinned by NO test at the
tool layer (`test_resolve_repo.py` covers `resolve_repo` alone). A convention
enforced nowhere is one edit away from silently breaking on one of these paths.

Every tool here can return a legitimately empty list. That is what makes them the
dangerous set: for these, "no rows" and "cannot read the index" are the same shape
on the wire unless the error is structured. Absence must be a MEASURED result,
never a side effect of an unreadable producer.
"""
from __future__ import annotations

import sqlite3

import pytest

from jcodemunch_mcp.tools.check_references import check_references
from jcodemunch_mcp.tools.find_dead_code import find_dead_code
from jcodemunch_mcp.tools.find_references import find_references
from jcodemunch_mcp.tools.get_file_outline import get_file_outline
from jcodemunch_mcp.tools.get_file_tree import get_file_tree
from jcodemunch_mcp.tools.get_repo_outline import get_repo_outline
from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.search_symbols import search_symbols
from jcodemunch_mcp.tools.search_text import search_text

MARKER = "unique_marker_fn"


@pytest.fixture()
def future_index(tmp_path):
    """An index whose schema version is ahead of this server's."""
    src = tmp_path / "proj"
    src.mkdir()
    (src / "a.py").write_text(f"def {MARKER}():\n    return 1\n", encoding="utf-8")
    store = tmp_path / "store"

    indexed = index_folder(
        path=str(src), storage_path=str(store), use_ai_summaries=False
    )
    repo = indexed["repo"]

    # Sanity: the symbol IS findable before the bump. Without this the test could
    # pass against an index that never held the symbol at all.
    before = search_symbols(repo=repo, query=MARKER, storage_path=str(store))
    assert before.get("results"), "fixture never indexed the marker symbol"

    db = next(store.glob("*.db"))
    conn = sqlite3.connect(str(db))
    try:
        cur = conn.execute("SELECT value FROM meta WHERE key='index_version'")
        stored = int(cur.fetchone()[0])
        conn.execute(
            "UPDATE meta SET value=? WHERE key='index_version'", (str(stored + 100),)
        )
        conn.commit()
    finally:
        conn.close()
    return repo, str(store)


def _assert_structured_refusal(out, tool_name):
    """The response must SAY it could not read the index."""
    assert isinstance(out, dict), f"{tool_name} returned {type(out).__name__}"

    lists = {k: v for k, v in out.items() if isinstance(v, list)}
    empty_lists = lists and all(len(v) == 0 for v in lists.values())

    declared = out.get("error") or out.get("load_error") or out.get("status")
    assert declared, (
        f"{tool_name} gave NO structured refusal for an unreadable index"
        + (" and returned empty result lists -- indistinguishable from"
           " 'the symbol does not exist'" if empty_lists else "")
    )
    assert "sqlite_future_version" in str(
        out.get("status") or out.get("load_error") or ""
    ), f"{tool_name} refused, but not with the incompatibility reason: {declared!r}"
    assert out.get("loadable") is False, f"{tool_name} did not report loadable=False"


CASES = [
    ("search_symbols", search_symbols, {"query": MARKER}),
    ("search_text", search_text, {"query": MARKER}),
    ("get_file_outline", get_file_outline, {"file_path": "a.py"}),
    ("find_references", find_references, {"identifier": MARKER}),
    ("check_references", check_references, {"identifier": MARKER}),
    ("get_repo_outline", get_repo_outline, {}),
    ("get_file_tree", get_file_tree, {}),
    ("find_dead_code", find_dead_code, {}),
]


@pytest.mark.parametrize("name,fn,kwargs", CASES, ids=[c[0] for c in CASES])
def test_future_version_index_refuses_instead_of_reporting_absence(
    future_index, name, fn, kwargs
):
    repo, store = future_index
    out = fn(repo=repo, storage_path=store, **kwargs)
    _assert_structured_refusal(out, name)


def test_the_marker_is_not_reported_as_missing(future_index):
    """The specific false-absence shape: a symbol that EXISTS in the index being
    reported as not found because the reader cannot open the index."""
    repo, store = future_index
    out = check_references(repo=repo, identifier=MARKER, storage_path=store)

    # Whatever else it says, it must not assert the identifier is unused.
    assert out.get("error"), "check_references answered a question it could not read"
    for key in ("exists", "referenced", "used", "found"):
        assert out.get(key) is not False, (
            f"check_references reported {key}=False from an unreadable index -- "
            "that is a false absence, not a measurement"
        )
