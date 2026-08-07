"""A starter-pack index must survive the startup orphan sweep (#419, @MotoMato85).

Pack indexes are built from clones on the pack CI machine
(`build_pack.py`: `tempfile.gettempdir() / "jcm-pack-clones" / <owner>-<repo>`),
and that absolute path ships inside every pack `.db`'s `meta.source_root`.
`install-pack` extracted the files verbatim, and `cleanup_orphan_indexes()`
deletes any index whose non-empty `source_root` is not a directory — skipping
only EMPTY ones as "remote repos".

So on every machine but the builder's, every installed pack was classified as a
vanished local clone and deleted on the next server start. Reproduced against a
real download before the fix: install the free `nodejs` pack, run the sweep,
`-> 4`, store empty.

⚠⚠ WHY THIS WAS INVISIBLE. An audit of the maintainer's own store found 95
indexes and ZERO deletions pending: his packs predate the current builder and
carry an EMPTY `source_root`, so they take the remote-repo skip. The person most
likely to notice was structurally immune. Do not "verify" this class of bug by
checking one machine.

⚠ The user-visible failure is worse than disappearance. The `.pack-<id>.json`
marker is NOT deleted by the sweep, so `install-pack`'s already-installed check
and the Console keep reporting the pack as present while nothing is queryable,
and reinstalling restarts the cycle. `test_marker_survives_so_repair_not_reinstall_is_the_fix`
pins that asymmetry, because it is the reason a repair-in-place migration is
required rather than telling users to reinstall.

⚠ NON-VACUITY. Against pre-fix code the pack-survival, heal, install-time and
facade tests all fail. The controls — a genuinely vanished local repo still gets
deleted, an empty source_root is still skipped, a real local repo is left alone —
pass on BOTH sides, so this file cannot go green by orphan cleanup quietly
ceasing to work altogether, which would "fix" the symptom by disabling the
feature.
"""
from __future__ import annotations

import shutil
import sqlite3

import pytest

from jcodemunch_mcp.cli.install_pack import _clear_builder_paths
from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.storage.sqlite_store import is_pack_clone_path
from jcodemunch_mcp.tools.index_folder import index_folder

# The literal shape shipped inside every pack .db built by the CI.
PACK_ROOT = "/tmp/jcm-pack-clones/expressjs-express"


def _make_indexed_store(tmp_path, name="proj"):
    """Build one REAL index, so the meta/schema under test is the real thing."""
    project = tmp_path / name
    project.mkdir()
    (project / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    store_dir = tmp_path / "store"
    index_folder(
        str(project),
        use_ai_summaries=False,
        storage_path=str(store_dir),
        incremental=False,
        context_providers=False,
    )
    dbs = list(store_dir.glob("*.db"))
    assert len(dbs) == 1, dbs
    return store_dir, project, dbs[0]


def _set_meta(db, **kv):
    conn = sqlite3.connect(str(db))
    try:
        for k, v in kv.items():
            conn.execute("UPDATE meta SET value = ? WHERE key = ?", (v, k))
        conn.commit()
    finally:
        conn.close()


def _get_meta(db, key):
    conn = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


# ── the path predicate ────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/tmp/jcm-pack-clones/expressjs-express",
    "/var/folders/xy/T/jcm-pack-clones/koajs-koa",       # macOS gettempdir()
    r"C:\Users\ci\AppData\Local\Temp\jcm-pack-clones\nodejs-node",
])
def test_pack_clone_paths_are_recognised(path):
    assert is_pack_clone_path(path) is True


@pytest.mark.parametrize("path", [
    "",
    "/home/dev/projects/app",
    "/home/dev/my-jcm-pack-clones-backup/app",  # substring, NOT a component
    "/home/dev/jcm-pack-clones-old/app",
])
def test_non_pack_paths_are_not_recognised(path):
    """Matched on a path COMPONENT. A substring match would silently exempt a
    user's real directory from orphan cleanup and leak dead indexes forever."""
    assert is_pack_clone_path(path) is False


# ── the sweep ─────────────────────────────────────────────────────────────────

def test_pack_index_is_not_deleted_by_the_sweep(tmp_path):
    """The bug, stated directly."""
    store_dir, _project, db = _make_indexed_store(tmp_path)
    _set_meta(db, source_root=PACK_ROOT, git_root=PACK_ROOT)

    store = IndexStore(base_path=str(store_dir))
    deleted = store.cleanup_orphan_indexes()

    assert deleted == 0, "a starter-pack index was deleted as an orphan"
    assert db.exists(), "pack .db removed from disk"


def test_vanished_local_repo_is_still_deleted(tmp_path):
    """CONTROL — passes on both sides. Orphan cleanup must still do its job."""
    store_dir, project, db = _make_indexed_store(tmp_path)
    shutil.rmtree(project)  # the real orphan case

    store = IndexStore(base_path=str(store_dir))
    deleted = store.cleanup_orphan_indexes()

    assert deleted == 1, "a genuinely vanished local repo was not cleaned up"
    assert not db.exists()


def test_live_local_repo_is_untouched(tmp_path):
    """CONTROL — passes on both sides."""
    store_dir, _project, db = _make_indexed_store(tmp_path)
    store = IndexStore(base_path=str(store_dir))
    assert store.cleanup_orphan_indexes() == 0
    assert db.exists()


def test_empty_source_root_is_still_skipped(tmp_path):
    """CONTROL — the pre-existing remote-repo skip must survive the new guard."""
    store_dir, _project, db = _make_indexed_store(tmp_path)
    _set_meta(db, source_root="", git_root="")
    store = IndexStore(base_path=str(store_dir))
    assert store.cleanup_orphan_indexes() == 0
    assert db.exists()


# ── the migration ─────────────────────────────────────────────────────────────

def test_heal_clears_builder_paths_on_installed_packs(tmp_path):
    """Fixing install-pack only helps the NEXT install; packs already on disk
    still carry the CI path and must be repaired in place."""
    store_dir, _project, db = _make_indexed_store(tmp_path)
    _set_meta(db, source_root=PACK_ROOT, git_root=PACK_ROOT)

    store = IndexStore(base_path=str(store_dir))
    healed = store.heal_pack_index_paths()

    assert healed == 1
    assert _get_meta(db, "source_root") == ""
    assert _get_meta(db, "git_root") == "", (
        "git_root left set would keep the pack visible to watch-all"
    )


def test_heal_is_exposed_on_the_facade_callers_actually_hold(tmp_path):
    """`server.py` constructs IndexStore, not SQLiteIndexStore. A method added
    only to the implementation is invisible in production — this was a real
    miss during the fix, caught only by running the repro end to end."""
    assert hasattr(IndexStore, "heal_pack_index_paths")


def test_heal_leaves_real_local_repos_alone(tmp_path):
    """CONTROL — passes on both sides once the method exists. Blanking a real
    repo's source_root would exempt it from orphan cleanup permanently."""
    store_dir, project, db = _make_indexed_store(tmp_path)
    before = _get_meta(db, "source_root")
    assert before  # a real path

    store = IndexStore(base_path=str(store_dir))
    assert store.heal_pack_index_paths() == 0
    assert _get_meta(db, "source_root") == before


def test_heal_then_sweep_is_the_startup_order(tmp_path):
    """End-to-end in the order server.py runs them."""
    store_dir, _project, db = _make_indexed_store(tmp_path)
    _set_meta(db, source_root=PACK_ROOT, git_root=PACK_ROOT)

    store = IndexStore(base_path=str(store_dir))
    healed = store.heal_pack_index_paths()
    deleted = store.cleanup_orphan_indexes()

    assert (healed, deleted) == (1, 0)
    assert db.exists()


# ── install time ──────────────────────────────────────────────────────────────

def test_install_clears_builder_paths(tmp_path):
    """The extraction-time half: a freshly installed pack is neutralised so it
    never depends on the migration having run."""
    store_dir, _project, db = _make_indexed_store(tmp_path)
    _set_meta(db, source_root=PACK_ROOT, git_root=PACK_ROOT)

    _clear_builder_paths(db)

    assert _get_meta(db, "source_root") == ""
    assert _get_meta(db, "git_root") == ""
    store = IndexStore(base_path=str(store_dir))
    assert store.cleanup_orphan_indexes() == 0


def test_clear_builder_paths_never_raises_on_a_bad_file(tmp_path):
    """Best-effort by design: a pack that installs but is not neutralised is
    still recoverable (heal + sweep guard). Failing the install would turn a
    meta problem into an unusable pack."""
    bogus = tmp_path / "not-a.db"
    bogus.write_text("not sqlite", encoding="utf-8")
    _clear_builder_paths(bogus)  # must not raise


def test_marker_survives_so_repair_not_reinstall_is_the_fix(tmp_path):
    """The asymmetry that makes this silent: the sweep deletes the index but
    NOT the `.pack-<id>.json` marker, so every 'is it installed' check keeps
    answering yes over an empty store. Pinning it here documents why the
    migration repairs in place instead of asking users to reinstall."""
    store_dir, project, db = _make_indexed_store(tmp_path)
    marker = store_dir / ".pack-nodejs.json"
    marker.write_text('{"pack": "nodejs"}', encoding="utf-8")
    shutil.rmtree(project)  # force a real deletion

    store = IndexStore(base_path=str(store_dir))
    assert store.cleanup_orphan_indexes() == 1
    assert not db.exists()
    assert marker.exists(), (
        "marker was cleaned up too — if this ever becomes true, the "
        "'installed but empty' state is gone and this rationale needs revisiting"
    )
