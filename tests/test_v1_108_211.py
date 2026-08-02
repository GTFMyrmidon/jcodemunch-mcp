"""v1.108.211 — "not embedded" and "these candidates are not embedded" stop
looking identical.

Follow-on to v1.108.210 / #398 Arc 5, raised by @rknighton. Dropping `count()`
was right, but `count()` was answering a repository-level question as a side
effect of guarding the fetch. Without it, `find_similar_symbols` derived
everything from `len(get_many(cand_ids)) >= 2`, which collapses two states:

  - this repository has no embeddings at all
  - this repository IS embedded, but fewer than two of THESE candidates are

Both produced `mode = "structural"` and one shared note telling the caller to
run `embed_repo` — advice that is simply wrong in the second state, and was
wrong before .210 too (the old `count() > 0` guard never surfaced the
difference either).

`EmbeddingStore.has_any()` restores the repository-level signal for less than
what was removed: `SELECT 1 ... LIMIT 1` on the same `mode=ro&immutable=1`
connection, no scan and no write, and only when no candidate vector was
fetched. It is three-state — `None` means "could not establish", never `False`.
"""

from __future__ import annotations

import sqlite3

import pytest

from jcodemunch_mcp.storage.embedding_store import EmbeddingStore
from jcodemunch_mcp.tools.find_similar_symbols import _STRUCTURAL_NOTES


class TestHasAny:
    def test_true_when_embeddings_exist(self, tmp_path):
        s = EmbeddingStore(tmp_path / "idx.db")
        s.set_many({"a.py::one#function": [1.0, 0.0]})
        assert s.has_any() is True

    def test_false_when_table_is_empty(self, tmp_path):
        db = tmp_path / "idx.db"
        s = EmbeddingStore(db)
        s.set_many({"a.py::one#function": [1.0]})
        s.clear()
        assert s.has_any() is False

    def test_false_when_database_file_is_absent(self, tmp_path):
        assert EmbeddingStore(tmp_path / "nope.db").has_any() is False

    def test_false_when_table_is_absent(self, tmp_path):
        db = tmp_path / "other.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE unrelated (x INTEGER)")
        conn.close()
        assert EmbeddingStore(db).has_any() is False

    def test_unreadable_database_is_unknown_not_false(self, tmp_path):
        """A file we could not read is not evidence of an empty repository."""
        db = tmp_path / "garbage.db"
        db.write_bytes(b"this is not a sqlite database" * 200)
        assert EmbeddingStore(db).has_any() is None


class TestHasAnyDoesNotTouchTheFile:
    """The whole point of replacing count(): the probe must stay read-only."""

    def _sidecars(self, db_path):
        return (
            db_path.with_suffix(db_path.suffix + "-wal").exists(),
            db_path.with_suffix(db_path.suffix + "-shm").exists(),
        )

    def test_probe_does_not_bump_mtime_or_make_sidecars(self, tmp_path):
        db = tmp_path / "ro.db"
        s = EmbeddingStore(db)
        s.set_many({f"a.py::f{i}#function": [float(i)] for i in range(5)})
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
        for suffix in ("-wal", "-shm"):
            p = db.with_suffix(db.suffix + suffix)
            if p.exists():
                p.unlink()

        before_mtime = db.stat().st_mtime_ns
        before_sidecars = self._sidecars(db)

        assert s.has_any() is True

        assert db.stat().st_mtime_ns == before_mtime, "has_any moved the .db mtime"
        assert self._sidecars(db) == before_sidecars, "has_any created WAL sidecars"


class TestNotesAreDistinct:
    def test_every_state_has_its_own_note(self):
        notes = set(_STRUCTURAL_NOTES.values())
        assert len(notes) == len(_STRUCTURAL_NOTES), "two states share one note"

    def test_only_the_unembedded_state_tells_you_to_run_embed_repo(self):
        """The regression this release exists to prevent."""
        assert "Run embed_repo" in _STRUCTURAL_NOTES["repo_not_embedded"]
        assert "will not change this result" in _STRUCTURAL_NOTES["candidates_not_embedded"]
        assert "not evidence" in _STRUCTURAL_NOTES["unknown"]


class TestFindSimilarSymbolsStates:
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

    def _embed_store(self, repo, store_dir):
        from jcodemunch_mcp.storage import IndexStore
        from jcodemunch_mcp.tools._utils import resolve_repo as _rr

        owner, name = _rr(repo, store_dir)
        idx_store = IndexStore(base_path=store_dir)
        index = idx_store.load_index(owner, name)
        db_path = idx_store._sqlite._db_path(owner, name)
        return EmbeddingStore(db_path), index

    def test_no_embeddings_reports_repo_not_embedded(self, tmp_path):
        from jcodemunch_mcp.tools.find_similar_symbols import find_similar_symbols

        repo, store_dir = self._index(tmp_path)
        out = find_similar_symbols(repo=repo, storage_path=store_dir)
        assert out.get("mode") == "structural"
        assert out.get("semantic_state") == "repo_not_embedded"
        assert "Run embed_repo" in out["note"]

    def test_embedded_repo_with_unembedded_candidates(self, tmp_path):
        """The state the old single note lied about."""
        from jcodemunch_mcp.tools.find_similar_symbols import find_similar_symbols

        repo, store_dir = self._index(tmp_path)
        emb, _index = self._embed_store(repo, store_dir)
        # Embeddings exist for this repository, but for no candidate the tool
        # will consider — exactly the shape of a symbol added after embed_repo ran.
        emb.set_many({"ghost.py::vanished#function": [1.0, 0.5, 0.25]})

        out = find_similar_symbols(repo=repo, storage_path=store_dir)
        assert out.get("mode") == "structural"
        assert out.get("semantic_state") == "candidates_not_embedded"
        assert "Run embed_repo" not in out["note"]

    def test_exactly_one_embedded_candidate_is_also_candidates_not_embedded(self, tmp_path):
        """One vector is below the blend floor but still proves the repo is embedded,
        and proves it without a probe."""
        from jcodemunch_mcp.tools.find_similar_symbols import find_similar_symbols

        repo, store_dir = self._index(tmp_path)
        emb, index = self._embed_store(repo, store_dir)
        fn_ids = [s["id"] for s in index.symbols if s["kind"] == "function"]
        assert fn_ids
        emb.set_many({fn_ids[0]: [1.0, 0.5, 0.25]})

        out = find_similar_symbols(repo=repo, storage_path=store_dir)
        assert out.get("mode") == "structural"
        assert out.get("semantic_state") == "candidates_not_embedded"

    def test_hybrid_response_does_not_carry_the_field(self, tmp_path):
        """`mode: hybrid` already says the signal was applied; do not pay twice."""
        from jcodemunch_mcp.tools.find_similar_symbols import find_similar_symbols

        repo, store_dir = self._index(tmp_path)
        emb, index = self._embed_store(repo, store_dir)
        fn_ids = [s["id"] for s in index.symbols if s["kind"] == "function"]
        assert len(fn_ids) >= 3
        emb.set_many({sid: [1.0, 0.5, 0.25] for sid in fn_ids})

        out = find_similar_symbols(repo=repo, storage_path=store_dir)
        assert out.get("mode") == "hybrid"
        assert "semantic_state" not in out
        assert "note" not in out

    def test_too_few_candidates_is_not_applicable(self, tmp_path):
        """The early exit never consults the store, so it must not imply either reason."""
        from jcodemunch_mcp.tools.find_similar_symbols import find_similar_symbols

        repo, store_dir = self._index(tmp_path)
        out = find_similar_symbols(repo=repo, storage_path=store_dir, scope="b.py")
        assert out.get("candidates_considered", 0) < 2
        assert out.get("semantic_state") == "not_applicable"


@pytest.mark.parametrize("state", ["repo_not_embedded", "candidates_not_embedded", "unknown"])
def test_notes_mapping_is_total_for_reachable_states(state):
    assert _STRUCTURAL_NOTES[state]
