"""An index whose symbols predate the current extractor re-parses once (#414).

The byte-offset fix repairs the PARSER. It does not repair the DATA: a symbol
extracted with a corrupted name is stored under that name, and the file that
produced it is unchanged, so every incremental path -- mtime diff, watcher
change set, GitHub blob SHA -- correctly concludes there is nothing to re-read.
Measured before this existed: after upgrading, `index_folder(incremental=True)`
returned "No changes detected" and left the wrong names in place.

`PARSER_GENERATION` closes that. It is stamped only by the full-save path,
because only a full corpus parse earns it, and an index carrying an older
generation takes one forced re-parse, reported through the #413 fields
(`performed_incremental: false` + `rebuild_reason`).
"""
from __future__ import annotations

import sqlite3

import pytest

from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.storage.index_store import PARSER_GENERATION
from jcodemunch_mcp.tools.index_folder import index_folder

#: A comment whose five U+00E4 shift every byte offset after it. Without the
#: extractor fix these names come back as fragments; the point here is the
#: STORED rows, so the fixture is the same shape as the bug report's.
SOURCE = "# kommentti äääää\nfunction Get-Summa { }\nfunction Get-Toinen { }\n"
NAMES = {"Get-Summa", "Get-Toinen"}


def _meta(db, key):
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _set_meta(db, key, value):
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
    finally:
        conn.close()


def _drop_meta(db, key):
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("DELETE FROM meta WHERE key=?", (key,))
        conn.commit()
    finally:
        conn.close()


def _names(store, repo):
    owner, name = repo.split("/", 1)
    index = IndexStore(base_path=str(store)).load_index(owner, name)
    return {s["name"] for s in index.symbols}


@pytest.fixture()
def legacy_index(tmp_path):
    """An index that looks like one written before the generation stamp."""
    src = tmp_path / "proj"
    src.mkdir()
    (src / "demo.ps1").write_text(SOURCE, encoding="utf-8")
    store = tmp_path / "store"

    first = index_folder(path=str(src), storage_path=str(store), use_ai_summaries=False)
    assert first.get("success") is True
    db = next(store.glob("*.db"))
    # A pre-1.108.244 index carries no stamp at all -- not a zero, the key is
    # simply absent. Reproduce that rather than a value it never wrote.
    _drop_meta(db, "parser_generation")
    return src, store, db, first["repo"]


def test_a_missing_stamp_forces_one_full_reparse(legacy_index):
    src, store, db, repo = legacy_index

    out = index_folder(
        path=str(src), storage_path=str(store), use_ai_summaries=False,
        incremental=True,
    )

    assert out.get("success") is True, out
    assert out.get("rebuild_reason") == "parser_generation_upgrade"
    assert out.get("requested_incremental") is True
    assert out.get("performed_incremental") is False
    assert any("re-parsing every file once" in w for w in out.get("warnings") or [])
    assert _names(store, repo) == NAMES
    assert _meta(db, "parser_generation") == str(PARSER_GENERATION)


def test_the_reparse_happens_exactly_once(legacy_index):
    src, store, _db, _repo = legacy_index

    index_folder(path=str(src), storage_path=str(store), use_ai_summaries=False)
    second = index_folder(
        path=str(src), storage_path=str(store), use_ai_summaries=False,
        incremental=True,
    )

    assert "rebuild_reason" not in second, second.get("rebuild_reason")
    assert second.get("performed_incremental") is True


def test_the_watcher_fast_path_cannot_skip_the_upgrade(legacy_index):
    """The change-set path is the one that would silently keep bad rows.

    A watcher hands index_folder the exact files that changed. The corrupted
    symbols live in files that did NOT change, so a delta pass would repair
    nothing while reporting success.
    """
    src, store, db, repo = legacy_index
    other = src / "toinen.ps1"
    other.write_text("function Get-Kolmas { }\n", encoding="utf-8")

    out = index_folder(
        path=str(src), storage_path=str(store), use_ai_summaries=False,
        incremental=True, changed_paths=[("added", str(other))],
    )

    assert out.get("rebuild_reason") == "parser_generation_upgrade", out
    assert out.get("performed_incremental") is False
    assert out.get("fast_path") is not True
    assert NAMES <= _names(store, repo)
    assert _meta(db, "parser_generation") == str(PARSER_GENERATION)


def test_an_incremental_save_does_not_claim_the_current_generation(tmp_path):
    """⚠ The stamp must mean 'every row was produced by this generation'.

    Only the full-save path writes it. If an incremental save stamped it too,
    a partial re-parse would certify rows nobody re-read -- and being wrong in
    that direction is permanent, because the upgrade never fires again.
    """
    src = tmp_path / "proj"
    src.mkdir()
    (src / "demo.ps1").write_text(SOURCE, encoding="utf-8")
    store = tmp_path / "store"
    index_folder(path=str(src), storage_path=str(store), use_ai_summaries=False)

    db = next(store.glob("*.db"))
    _set_meta(db, "parser_generation", "0")

    # A delta write through the store, bypassing index_folder's upgrade branch.
    (src / "demo.ps1").write_text(SOURCE + "function Get-Neljas { }\n", encoding="utf-8")
    owner, name = next(iter(IndexStore(base_path=str(store)).list_repos()))["repo"].split("/", 1)
    IndexStore(base_path=str(store)).incremental_save(
        owner=owner, name=name,
        changed_files=["demo.ps1"], new_files=[], deleted_files=[],
        new_symbols=[], raw_files={},
    )

    assert _meta(db, "parser_generation") == "0", (
        "an incremental save stamped a generation it did not earn"
    )


def test_a_current_index_is_left_alone(tmp_path):
    """Non-vacuity: the check must be able to answer 'nothing to do'."""
    src = tmp_path / "proj"
    src.mkdir()
    (src / "demo.ps1").write_text(SOURCE, encoding="utf-8")
    store = tmp_path / "store"

    first = index_folder(path=str(src), storage_path=str(store), use_ai_summaries=False)
    db = next(store.glob("*.db"))
    assert _meta(db, "parser_generation") == str(PARSER_GENERATION)

    second = index_folder(
        path=str(src), storage_path=str(store), use_ai_summaries=False,
        incremental=True,
    )
    assert "rebuild_reason" not in second
    assert _names(store, first["repo"]) == NAMES
