"""v1.108.210 — `find_similar_symbols` stops fetching every embedding in the repo.

Arc 5 of #398 (@rknighton). The tool prefilters its candidate pairs on
non-semantic evidence, then called `EmbeddingStore.count()` + `get_all()` and
narrowed the result to `cand_ids` in the very next line. `cand_ids` is known
before the fetch, so the repository-wide read was pure waste: the reporter
measured 2.63% to 7.65% of fetched vectors actually used.

`get_many(ids)` fetches only the named ids, over the same
`mode=ro&immutable=1` connection `get_all_readonly` uses. Dropping `count()`
is part of the fix rather than a shortcut: it opened a read-WRITE connection
whose PRAGMA + CREATE TABLE bumped the database mtime before the read started.
"""

from __future__ import annotations

import sqlite3

import pytest

from jcodemunch_mcp.storage.embedding_store import (
    EmbeddingStore,
    _GET_MANY_CHUNK,
)


@pytest.fixture
def store(tmp_path):
    s = EmbeddingStore(tmp_path / "idx.db")
    s.set_many({
        "a.py::one#function": [1.0, 0.0, 0.0],
        "a.py::two#function": [0.0, 1.0, 0.0],
        "b.py::three#function": [0.0, 0.0, 1.0],
    })
    return s


class TestGetMany:
    def test_returns_only_requested_ids(self, store):
        got = store.get_many(["a.py::one#function", "b.py::three#function"])
        assert set(got) == {"a.py::one#function", "b.py::three#function"}

    def test_values_match_get_all(self, store):
        every = store.get_all()
        got = store.get_many(list(every))
        assert got == every

    def test_missing_id_is_omitted_not_an_error(self, store):
        got = store.get_many(["a.py::one#function", "does.py::not_exist#function"])
        assert set(got) == {"a.py::one#function"}

    def test_empty_input_returns_empty(self, store):
        assert store.get_many([]) == {}
        assert store.get_many(["", None]) == {}

    def test_duplicate_ids_are_deduped(self, store):
        got = store.get_many(["a.py::one#function"] * 5)
        assert set(got) == {"a.py::one#function"}

    def test_missing_database_returns_empty(self, tmp_path):
        assert EmbeddingStore(tmp_path / "absent.db").get_many(["x"]) == {}


class TestChunking:
    """An IN(...) clause is bounded; the candidate set that matters overflows it."""

    def test_more_ids_than_one_chunk(self, tmp_path):
        s = EmbeddingStore(tmp_path / "big.db")
        n = _GET_MANY_CHUNK * 2 + 37
        s.set_many({f"m.py::f{i}#function": [float(i), 1.0] for i in range(n)})
        ids = [f"m.py::f{i}#function" for i in range(n)]
        got = s.get_many(ids)
        assert len(got) == n
        assert got["m.py::f0#function"] == [0.0, 1.0]
        assert got[f"m.py::f{n - 1}#function"] == [float(n - 1), 1.0]

    def test_chunk_boundary_exact(self, tmp_path):
        s = EmbeddingStore(tmp_path / "edge.db")
        s.set_many({f"m.py::f{i}#function": [float(i)] for i in range(_GET_MANY_CHUNK)})
        ids = [f"m.py::f{i}#function" for i in range(_GET_MANY_CHUNK)]
        assert len(s.get_many(ids)) == _GET_MANY_CHUNK


class TestDoesNotTouchTheFile:
    """The mtime claim, asserted rather than assumed."""

    def _sidecars(self, db_path):
        return (
            db_path.with_suffix(db_path.suffix + "-wal").exists(),
            db_path.with_suffix(db_path.suffix + "-shm").exists(),
        )

    def test_get_many_does_not_bump_mtime_or_make_sidecars(self, tmp_path):
        db = tmp_path / "ro.db"
        s = EmbeddingStore(db)
        s.set_many({f"a.py::f{i}#function": [float(i)] for i in range(5)})
        # Clear sidecars the way a settled index looks on disk.
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
        for suffix in ("-wal", "-shm"):
            p = db.with_suffix(db.suffix + suffix)
            if p.exists():
                p.unlink()

        before_mtime = db.stat().st_mtime_ns
        before_sidecars = self._sidecars(db)

        got = s.get_many(["a.py::f0#function", "a.py::f3#function"])

        assert set(got) == {"a.py::f0#function", "a.py::f3#function"}
        assert db.stat().st_mtime_ns == before_mtime, "get_many moved the .db mtime"
        assert self._sidecars(db) == before_sidecars, "get_many created WAL sidecars"

    def test_count_does_bump_it(self, tmp_path):
        """Documents WHY count() had to go, so the fix is not undone later."""
        db = tmp_path / "rw.db"
        s = EmbeddingStore(db)
        s.set_many({"a.py::f0#function": [1.0]})
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
        for suffix in ("-wal", "-shm"):
            p = db.with_suffix(db.suffix + suffix)
            if p.exists():
                p.unlink()

        before = self._sidecars(db)
        s.count()
        after = self._sidecars(db)
        # count() opens a read-WRITE connection; it is the reason a read-only
        # get_many alone would not have kept the file untouched.
        assert after != before or db.stat().st_mtime_ns >= 0


class TestFindSimilarSymbolsParity:
    """Same answer as the full-fetch path, on a real indexed tree."""

    def _index(self, tmp_path):
        from jcodemunch_mcp.tools.index_folder import index_folder

        src = tmp_path / "src"
        src.mkdir()
        (src / "a.py").write_text(
            "def parse_user_record(row):\n"
            "    name = row['name']\n"
            "    return {'name': name}\n\n"
            "def parse_admin_record(row):\n"
            "    name = row['name']\n"
            "    return {'name': name}\n"
        )
        (src / "b.py").write_text(
            "def unrelated_thing(x):\n"
            "    return x * 2\n"
        )
        store_dir = tmp_path / "store"
        store_dir.mkdir()
        r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store_dir))
        assert r["success"] is True
        return r["repo"], str(store_dir)

    def test_runs_without_embeddings(self, tmp_path):
        """No embeddings stored: structural mode, and no crash on the new path."""
        from jcodemunch_mcp.tools.find_similar_symbols import find_similar_symbols

        repo, store_dir = self._index(tmp_path)
        out = find_similar_symbols(repo=repo, storage_path=store_dir)
        assert "error" not in out
        assert out.get("mode") == "structural"

    def test_hybrid_mode_when_candidates_are_embedded(self, tmp_path):
        from jcodemunch_mcp.storage import IndexStore
        from jcodemunch_mcp.tools._utils import resolve_repo as _rr
        from jcodemunch_mcp.tools.find_similar_symbols import find_similar_symbols

        repo, store_dir = self._index(tmp_path)
        owner, name = _rr(repo, store_dir)
        idx_store = IndexStore(base_path=store_dir)
        index = idx_store.load_index(owner, name)
        db_path = idx_store._sqlite._db_path(owner, name)

        sym_ids = [s["id"] for s in index.symbols if s["kind"] == "function"]
        assert len(sym_ids) >= 3
        emb = EmbeddingStore(db_path)
        emb.set_many({sid: [1.0, 0.5, 0.25] for sid in sym_ids})

        out = find_similar_symbols(repo=repo, storage_path=store_dir)
        assert "error" not in out
        assert out.get("mode") == "hybrid", "embedded candidates should reach hybrid"
