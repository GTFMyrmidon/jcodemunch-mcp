"""v1.108.223 — the semantic scoring pass stops re-decoding the whole store.

#399 (@vondecron). Every semantic `search_symbols` call ran
`EmbeddingStore.get_all()` — re-reading and re-parsing all N stored vectors out
of SQLite — and then scored them with a pure-Python cosine that recomputed the
query vector's norm once per symbol. On the reporter's 30,479-symbol index that
was ~2.5 s of per-query cost no warm process could amortise.

`storage.embedding_matrix` decodes the table once per (repo, store-stamp) into
one L2-normalised matrix and scores with a single dot product, numpy when
importable and a norm-hoisted Python loop when not.

The tests that matter here are the ones that pin what must NOT change:

* the pure-Python fallback still ranks (numpy is not a dependency and the
  fallback is exercised with numpy forced absent),
* both paths agree with `_cosine_similarity` to floating-point tolerance,
* the cache invalidates on write — a freshly embedded symbol scoring 0.0
  forever is the failure mode this whole design has to avoid,
* the read path does not bump the database mtime (the v1.108.185 defect class).
"""

from __future__ import annotations

import builtins
import math

import pytest

from jcodemunch_mcp.retrieval import signal_fusion
from jcodemunch_mcp.storage import embedding_matrix as em
from jcodemunch_mcp.storage.embedding_store import EmbeddingStore
from jcodemunch_mcp.tools.search_symbols import _cosine_similarity


VECS = {
    "a.py::one#function": [1.0, 0.0, 0.0],
    "a.py::two#function": [0.0, 1.0, 0.0],
    "b.py::three#function": [0.6, 0.8, 0.0],
    "b.py::zero#function": [0.0, 0.0, 0.0],
}


@pytest.fixture(autouse=True)
def _clean_cache():
    em.invalidate()
    yield
    em.invalidate()


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "idx.db"
    EmbeddingStore(path).set_many(VECS)
    return path


@pytest.fixture
def no_numpy(monkeypatch):
    """Force the pure-Python path regardless of what is installed."""
    real_import = builtins.__import__

    def _fake(name, *args, **kwargs):
        if name == "numpy" or name.startswith("numpy."):
            raise ImportError("numpy disabled for this test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake)
    em.invalidate()
    return None


class TestScoresMatchTheShippedCosine:
    def test_numpy_path_matches_per_symbol_cosine(self, db):
        matrix = em.get_matrix(db)
        assert matrix is not None
        if not matrix.vectorised:
            pytest.skip("numpy not installed in this environment")
        q = [0.3, 0.4, 0.5]
        scores = matrix.score_all(q)
        for sid, vec in VECS.items():
            assert scores[sid] == pytest.approx(_cosine_similarity(q, vec), abs=1e-6)

    def test_python_fallback_matches_per_symbol_cosine(self, db, no_numpy):
        matrix = em.get_matrix(db)
        assert matrix is not None
        assert not matrix.vectorised, "fallback test ran on the numpy path"
        q = [0.3, 0.4, 0.5]
        scores = matrix.score_all(q)
        for sid, vec in VECS.items():
            assert scores[sid] == pytest.approx(_cosine_similarity(q, vec), abs=1e-6)

    def test_zero_vector_scores_zero_not_nan(self, db):
        scores = em.get_matrix(db).score_all([1.0, 1.0, 1.0])
        got = scores["b.py::zero#function"]
        assert got == 0.0
        assert not math.isnan(got)

    def test_zero_query_scores_nothing(self, db):
        assert em.get_matrix(db).score_all([0.0, 0.0, 0.0]) == {}

    def test_wrong_dimension_query_scores_nothing(self, db):
        # `_cosine_similarity` returned 0.0 per symbol for a length mismatch;
        # an empty map reaches the same answer through `.get(sid, 0.0)`.
        assert em.get_matrix(db).score_all([1.0, 0.0]) == {}


class TestCacheIdentity:
    def test_second_call_reuses_the_same_object(self, db):
        first = em.get_matrix(db)
        assert em.get_matrix(db) is first

    def test_write_invalidates(self, db):
        first = em.get_matrix(db)
        EmbeddingStore(db).set_many({"c.py::new#function": [0.0, 0.0, 1.0]})
        second = em.get_matrix(db)
        assert second is not first
        assert "c.py::new#function" in second

    def test_a_new_symbol_is_scorable_after_a_write(self, db):
        """The failure this design must not have: a fresh vector stuck at 0.0."""
        em.get_matrix(db)
        EmbeddingStore(db).set_many({"c.py::new#function": [0.0, 0.0, 1.0]})
        scores = em.get_matrix(db).score_all([0.0, 0.0, 1.0])
        assert scores["c.py::new#function"] == pytest.approx(1.0, abs=1e-6)

    def test_delete_invalidates(self, db):
        em.get_matrix(db)
        EmbeddingStore(db).delete_many(["a.py::one#function"])
        assert "a.py::one#function" not in em.get_matrix(db)

    def test_clear_invalidates(self, db):
        em.get_matrix(db)
        EmbeddingStore(db).clear()
        assert em.get_matrix(db) is None

    def test_disabled_cache_still_scores(self, db, monkeypatch):
        monkeypatch.setenv("JCODEMUNCH_EMBED_MATRIX_CACHE", "0")
        first = em.get_matrix(db)
        second = em.get_matrix(db)
        assert first is not second
        assert em.cache_stats()["repos"] == 0
        assert second.score_all([1.0, 0.0, 0.0])["a.py::one#function"] == pytest.approx(1.0)

    def test_cache_is_bounded(self, tmp_path):
        paths = []
        for i in range(em._MAX_CACHED_REPOS + 2):
            p = tmp_path / f"repo{i}.db"
            EmbeddingStore(p).set_many({f"f{i}.py::s#function": [1.0, 0.0]})
            paths.append(p)
            em.get_matrix(p)
        assert em.cache_stats()["repos"] == em._MAX_CACHED_REPOS

    def test_absent_database_is_none_not_an_error(self, tmp_path):
        assert em.get_matrix(tmp_path / "never-written.db") is None


class TestReadPathDoesNotWrite:
    def test_load_does_not_bump_the_database_stamp(self, db):
        """v1.108.185's defect class: a read that moves the mtime.

        The cache would also invalidate itself on every call if it did.
        """
        before = em._stamp(db)
        em.invalidate()
        em.get_matrix(db)
        assert em._stamp(db) == before


class TestFusionChannelUnchanged:
    def test_from_scores_matches_the_embedding_form(self):
        q = [0.3, 0.4, 0.5]
        legacy = signal_fusion.build_similarity_channel(q, VECS)
        scores = {sid: _cosine_similarity(q, v) for sid, v in VECS.items()}
        split = signal_fusion.build_similarity_channel_from_scores(scores)
        assert legacy.ranked_ids == split.ranked_ids
        assert legacy.raw_scores == split.raw_scores

    def test_min_similarity_still_filters(self):
        scores = {"a": 0.9, "b": 0.05}
        ch = signal_fusion.build_similarity_channel_from_scores(scores, min_similarity=0.1)
        assert ch.ranked_ids == ["a"]


class TestMismatchedWidthRowsAreInert:
    def test_a_row_of_another_dimension_is_excluded_not_fatal(self, tmp_path):
        """A store written by two different models must not crash the matrix.

        `_cosine_similarity` answered 0.0 for a length mismatch; dropping the
        row reaches the same answer through `.get(sid, 0.0)`.
        """
        path = tmp_path / "mixed.db"
        s = EmbeddingStore(path)
        s.set_many({"a.py::three#function": [1.0, 0.0, 0.0]})
        s.set_many({"a.py::five#function": [1.0, 0.0, 0.0, 0.0, 0.0]})
        matrix = em.get_matrix(path)
        assert matrix is not None
        assert matrix.skipped_dim_mismatch == 1
        assert matrix.score_all([1.0, 0.0, 0.0]).get("a.py::five#function", 0.0) == 0.0


class TestIterRaw:
    def test_returns_undecoded_blobs_for_every_row(self, db):
        raw = EmbeddingStore(db).iter_raw()
        assert {sid for sid, _ in raw} == set(VECS)
        assert all(isinstance(blob, (bytes, memoryview)) for _, blob in raw)

    def test_absent_table_is_empty_not_an_error(self, tmp_path):
        assert EmbeddingStore(tmp_path / "nope.db").iter_raw() == []
