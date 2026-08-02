"""#398 Arc 1 — the generation-safe read contract.

Two halves, and each has a measurement behind it rather than an assumption:

  1. one contract answers "which index is this", so the three surfaces that
     used to read the index object separately cannot disagree;
  2. a read-only open sees committed WAL data when the sidecars already exist
     and creates nothing when they do not.

The second half's tests are written as a matrix on purpose. Both of the
single-flag answers are wrong in exactly one cell, and both of those cells are
reproduced here — a future simplification back to one flag fails a named test
instead of quietly reintroducing either defect.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jcodemunch_mcp.storage import generation as gen


def _mtime_ns(db: Path) -> int:
    """The same reading `_db_mtime_ns` takes: max over .db and .db-wal."""
    m = db.stat().st_mtime_ns
    wal = Path(str(db) + "-wal")
    try:
        return max(m, wal.stat().st_mtime_ns)
    except OSError:
        return m


def _sidecars(db: Path) -> tuple[bool, bool]:
    return Path(str(db) + "-wal").exists(), Path(str(db) + "-shm").exists()


@pytest.fixture
def wal_db(tmp_path):
    """A WAL database with an OPEN writer, so the sidecars exist and the rows
    it wrote are still un-checkpointed."""
    db = tmp_path / "index.db"
    w = sqlite3.connect(str(db), isolation_level=None)
    w.execute("PRAGMA journal_mode = WAL")
    w.execute("CREATE TABLE t (k TEXT PRIMARY KEY)")
    w.execute("INSERT INTO t VALUES ('live')")
    yield db, w
    try:
        w.close()
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# The read contract                                                            #
# --------------------------------------------------------------------------- #


class TestReadContract:
    def test_sidecars_present_reads_the_uncheckpointed_wal(self, wal_db):
        db, _writer = wal_db
        assert _sidecars(db) == (True, True)
        conn = gen.connect_readonly(db)
        try:
            rows = conn.execute("SELECT k FROM t").fetchall()
        finally:
            conn.close()
        assert rows == [("live",)], (
            "a row committed to the WAL is committed; a reader that cannot see "
            "it reports a false absence"
        )

    def test_sidecars_present_does_not_move_the_mtime(self, wal_db):
        db, _writer = wal_db
        before = _mtime_ns(db)
        conn = gen.connect_readonly(db)
        try:
            conn.execute("SELECT k FROM t").fetchall()
        finally:
            conn.close()
        assert _mtime_ns(db) == before, (
            "reading an existing WAL must not look like a rebuild — that is the "
            "v1.108.185 `rebuilding` verdict"
        )

    def test_immutable_hides_the_whole_table_not_just_rows(self, wal_db):
        """The measurement that changed the fix.

        v1.108.185 stated the trade-off as "vectors in an un-checkpointed WAL
        are not read", i.e. a weaker ranking. It is stronger than that: when the
        CREATE TABLE itself is un-checkpointed the table is not there at all,
        and `EmbeddingStore.has_any` maps that to a confident False.
        """
        db, _writer = wal_db
        conn = sqlite3.connect(
            f"file:{db}?mode=ro&immutable=1", uri=True, isolation_level=None
        )
        try:
            with pytest.raises(sqlite3.OperationalError, match="no such table"):
                conn.execute("SELECT k FROM t").fetchall()
        finally:
            conn.close()

    def test_sidecars_absent_creates_none_and_still_reads(self, tmp_path):
        db = tmp_path / "index.db"
        w = sqlite3.connect(str(db), isolation_level=None)
        w.execute("PRAGMA journal_mode = WAL")
        w.execute("CREATE TABLE t (k TEXT PRIMARY KEY)")
        w.execute("INSERT INTO t VALUES ('checkpointed')")
        w.close()
        assert _sidecars(db) == (False, False)

        before = _mtime_ns(db)
        conn = gen.connect_readonly(db)
        try:
            rows = conn.execute("SELECT k FROM t").fetchall()
        finally:
            conn.close()

        assert rows == [("checkpointed",)]
        assert _sidecars(db) == (False, False), (
            "creating a -wal beside a quiet database is what moved the mtime "
            "mid-scan in v1.108.185"
        )
        assert _mtime_ns(db) == before

    def test_plain_mode_ro_would_create_sidecars(self, tmp_path):
        """The negative control: the flag we do NOT use when they are absent."""
        db = tmp_path / "index.db"
        w = sqlite3.connect(str(db), isolation_level=None)
        w.execute("PRAGMA journal_mode = WAL")
        w.execute("CREATE TABLE t (k TEXT PRIMARY KEY)")
        w.close()
        assert _sidecars(db) == (False, False)

        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, isolation_level=None)
        try:
            conn.execute("SELECT k FROM t").fetchall()
        finally:
            conn.close()
        assert Path(str(db) + "-wal").exists(), (
            "if this ever stops being true the conditional can collapse to one "
            "flag — until then it cannot"
        )

    def test_uri_switches_on_sidecar_presence(self, wal_db, tmp_path):
        db, _writer = wal_db
        assert gen.readonly_uri(db).endswith("mode=ro")
        assert gen.wal_sidecar_present(db) is True

        quiet = tmp_path / "quiet.db"
        quiet.write_bytes(b"")
        assert gen.readonly_uri(quiet).endswith("immutable=1")
        assert gen.wal_sidecar_present(quiet) is False


# --------------------------------------------------------------------------- #
# The generation contract                                                      #
# --------------------------------------------------------------------------- #


class _FakeIndex:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class TestGenerationContract:
    def test_empty_revision_normalises_once(self):
        """The disagreement this arc closes.

        `producers._snapshot` applied `or None`; `subject_state.capture` did
        not. The same index therefore described itself as `""` or `None`
        depending on which surface was asked.
        """
        g = gen.describe(_FakeIndex(indexed_at="", git_head=""))
        assert g.generation is None
        assert g.indexed_revision is None

    def test_capture_and_snapshot_agree(self, tmp_path):
        from jcodemunch_mcp.retrieval import subject_state

        index = _FakeIndex(indexed_at="", git_head="", source_root=None)
        state = subject_state.capture(index)
        g = gen.describe(index)
        assert state["generation"] == g.generation
        assert state["index_sha"] == g.indexed_revision

    def test_rewritten_since_load_needs_both_readings(self):
        assert gen.IndexGeneration(db_mtime_ns=5, loaded_mtime_ns=5).rewritten_since_load is False
        assert gen.IndexGeneration(db_mtime_ns=6, loaded_mtime_ns=5).rewritten_since_load is True
        # Unknown is not changed: an unstamped index must not degrade every
        # verdict it touches.
        assert gen.IndexGeneration(db_mtime_ns=6).rewritten_since_load is False
        assert gen.IndexGeneration(loaded_mtime_ns=5).rewritten_since_load is False

    def test_describe_never_raises(self):
        class Hostile:
            @property
            def indexed_at(self):
                raise RuntimeError("boom")

        assert gen.describe(Hostile()).generation is None
        assert gen.describe(None).generation is None

    def test_verdict_delegates_to_the_contract(self, tmp_path):
        from jcodemunch_mcp.retrieval import verdict

        db = tmp_path / "index.db"
        db.write_bytes(b"")
        index = _FakeIndex(_db_path=str(db), _loaded_mtime_ns=1)
        assert verdict.index_changed_since_load(index) is True

        index2 = _FakeIndex(_db_path=str(db), _loaded_mtime_ns=db.stat().st_mtime_ns)
        assert verdict.index_changed_since_load(index2) is False

        # No stamp at all: unknown, not changed.
        assert verdict.index_changed_since_load(_FakeIndex()) is False


# --------------------------------------------------------------------------- #
# The consumer the finding actually bit                                        #
# --------------------------------------------------------------------------- #


class TestEveryReaderSharesTheContract:
    """A convention needs a test, not a habit.

    Both single-flag spellings were live in the tree when this arc started —
    ten sites on ``immutable=1`` (invisible un-checkpointed rows, including
    ``check_delete_safe``'s runtime evidence and ``get_redaction_log``'s PII
    accounting) and four on plain ``mode=ro`` (sidecars created, mtime moved,
    including a ``token_tracker`` loop that did it to every index in
    ``~/.code-index`` at once). Guard every path that shares the hazard, and
    exempt the remaining one BY NAME.
    """

    #: The module that BUILDS the URIs is the only place they may be spelled.
    EXEMPT = {"storage/generation.py"}

    def test_no_module_hand_rolls_a_readonly_uri(self):
        import jcodemunch_mcp

        root = Path(jcodemunch_mcp.__file__).parent
        offenders = []
        for path in sorted(root.rglob("*.py")):
            rel = path.relative_to(root).as_posix()
            if rel in self.EXEMPT:
                continue
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if "?mode=ro" in line and "connect(" in line:
                    offenders.append(f"{rel}:{lineno}: {line.strip()}")
        assert not offenders, (
            "hand-rolled read-only connection(s); use "
            "storage.generation.connect_readonly, which is correct whether or "
            "not the WAL sidecars exist:\n  " + "\n  ".join(offenders)
        )

    def test_the_guard_would_catch_a_regression(self):
        """Non-vacuous: the scan must fire on the shape it is meant to stop."""
        import re

        bad = '        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)'
        assert "?mode=ro" in bad and "connect(" in bad
        # ...and must not fire on a docstring that merely names the flag.
        prose = "    ``mode=ro`` alone is NOT enough, which cost an hour."
        assert not ("?mode=ro" in prose and "connect(" in prose)
        assert re.search(r"mode=ro", prose)


class TestEmbeddingStoreFalseAbsence:
    def test_has_any_sees_an_uncheckpointed_embedding(self, tmp_path):
        """A repository embedded moments ago must not answer "no embeddings"."""
        from jcodemunch_mcp.storage.embedding_store import EmbeddingStore

        db = tmp_path / "index.db"
        w = sqlite3.connect(str(db), isolation_level=None)
        w.execute("PRAGMA journal_mode = WAL")
        w.execute(
            "CREATE TABLE symbol_embeddings ("
            "symbol_id TEXT PRIMARY KEY, embedding BLOB NOT NULL)"
        )
        w.execute("INSERT INTO symbol_embeddings VALUES ('a.py::f#function', X'00000000')")
        try:
            store = EmbeddingStore(str(db))
            assert store.has_any() is True
            assert set(store.get_all_readonly()) == {"a.py::f#function"}
            assert set(store.get_many(["a.py::f#function"])) == {"a.py::f#function"}
        finally:
            w.close()

    def test_reads_still_leave_no_sidecars_on_a_quiet_db(self, tmp_path):
        from jcodemunch_mcp.storage.embedding_store import EmbeddingStore

        db = tmp_path / "index.db"
        w = sqlite3.connect(str(db), isolation_level=None)
        w.execute("PRAGMA journal_mode = WAL")
        w.execute(
            "CREATE TABLE symbol_embeddings ("
            "symbol_id TEXT PRIMARY KEY, embedding BLOB NOT NULL)"
        )
        w.execute("INSERT INTO symbol_embeddings VALUES ('a.py::f#function', X'00000000')")
        w.close()
        assert _sidecars(db) == (False, False)

        before = _mtime_ns(db)
        store = EmbeddingStore(str(db))
        assert store.has_any() is True
        assert store.get_all_readonly()
        assert store.get_many(["a.py::f#function"])

        assert _sidecars(db) == (False, False)
        assert _mtime_ns(db) == before
