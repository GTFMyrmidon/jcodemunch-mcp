"""#398 Arc 2 — transactional selective code snapshots.

The close condition has three parts and each gets its own class:

  1. supported narrow calls complete without hydrating the full ``CodeIndex``;
  2. unsupported and broad modes promote once, with no change to errors,
     suggestions, ordering, branch behaviour or response fields;
  3. no read on this path uses ``immutable=1``.

The load-bearing tests are in ``TestPromotionBoundary``. A view that answers a
corpus-wide question from a partial row set is a false-absence generator, which
is the defect class v1.108.215 shipped a release to close — so "it promoted"
matters more here than "it was fast".
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jcodemunch_mcp.storage.selective import (
    CORPUS_WIDE,
    EXACT_FIELDS,
    SelectiveIndexView,
)
from jcodemunch_mcp.storage.sqlite_store import SQLiteIndexStore, _cache_clear
from jcodemunch_mcp.storage.index_store import CodeIndex


OWNER, NAME = "local", "selective-fixture"


def _symbol(file: str, name: str):
    from jcodemunch_mcp.parser.symbols import Symbol

    return Symbol(
        id=f"{file}::{name}#function",
        file=file,
        name=name,
        qualified_name=name,
        kind="function",
        language="python",
        signature=f"def {name}(): pass",
        summary=f"{name} summary",
    )


@pytest.fixture
def store(tmp_path):
    _cache_clear()
    s = SQLiteIndexStore(base_path=str(tmp_path))
    s.save_index(
        owner=OWNER,
        name=NAME,
        source_files=["a.py", "b.py", "c.py"],
        symbols=[
            _symbol("a.py", "alpha"),
            _symbol("a.py", "beta"),
            _symbol("b.py", "gamma"),
            _symbol("c.py", "delta"),
        ],
        raw_files={
            "a.py": "def alpha(): pass\ndef beta(): pass\n",
            "b.py": "def gamma(): pass\n",
            "c.py": "def delta(): pass\n",
        },
        languages={"python": 3},
        file_languages={"a.py": "python", "b.py": "python", "c.py": "python"},
        file_summaries={"a.py": "", "b.py": "", "c.py": ""},
        git_head="deadbeef",
    )
    _cache_clear()
    yield s
    _cache_clear()


class _Tripwire:
    """Counts full hydrations so a narrow call can prove it did not do one."""

    def __init__(self, store, monkeypatch):
        self.count = 0
        real = store.load_index

        def counted(*a, **kw):
            self.count += 1
            return real(*a, **kw)

        monkeypatch.setattr(store, "load_index", counted)


# --------------------------------------------------------------------------- #
# 1. narrow calls do not hydrate                                               #
# --------------------------------------------------------------------------- #


class TestNarrowReads:
    def test_named_symbol_arrives_without_a_full_load(self, store, monkeypatch):
        trip = _Tripwire(store, monkeypatch)
        view = store.open_selective(
            OWNER, NAME, symbol_ids=["a.py::alpha#function"]
        )
        assert isinstance(view, SelectiveIndexView)
        sym = view.get_symbol("a.py::alpha#function")
        assert sym["name"] == "alpha"
        assert view.selective_symbol_count == 1
        assert trip.count == 0, "a one-symbol read must not hydrate the corpus"
        assert not view.promoted

    def test_a_file_scope_loads_only_that_file(self, store, monkeypatch):
        trip = _Tripwire(store, monkeypatch)
        view = store.open_selective(OWNER, NAME, files=["a.py"])
        assert view.selective_symbol_count == 2
        assert trip.count == 0

    def test_file_level_answers_are_exact_not_partial(self, store, monkeypatch):
        """The ``files`` table is read in full, so these never need promotion."""
        trip = _Tripwire(store, monkeypatch)
        view = store.open_selective(OWNER, NAME, symbol_ids=["a.py::alpha#function"])
        assert sorted(view.source_files) == ["a.py", "b.py", "c.py"]
        assert view.has_source_file("c.py") is True
        assert view.has_source_file("nope.py") is False
        assert view.file_languages["c.py"] == "python"
        assert view.git_head == "deadbeef"
        assert view.indexed_at
        assert trip.count == 0

    def test_an_unrequested_symbol_is_fetched_not_guessed(self, store, monkeypatch):
        """A miss against the preloaded set is a query, never an absence."""
        trip = _Tripwire(store, monkeypatch)
        view = store.open_selective(OWNER, NAME, symbol_ids=["a.py::alpha#function"])
        sym = view.get_symbol("c.py::delta#function")
        assert sym is not None and sym["name"] == "delta"
        assert trip.count == 0, "a targeted row read beats hydrating 665k rows"
        assert not view.promoted

    def test_a_genuinely_absent_symbol_is_absent(self, store):
        view = store.open_selective(OWNER, NAME, symbol_ids=["a.py::alpha#function"])
        assert view.get_symbol("nowhere.py::ghost#function") is None

    def test_load_provenance_is_answered_without_promoting(self, store, monkeypatch):
        """Every verdict path reads these. If they promoted, nothing else would
        matter — `subject_state.capture` runs on essentially every response."""
        trip = _Tripwire(store, monkeypatch)
        view = store.open_selective(OWNER, NAME, symbol_ids=["a.py::alpha#function"])
        assert view._db_path
        assert view._loaded_mtime_ns is not None

        from jcodemunch_mcp.retrieval import subject_state

        state = subject_state.capture(view)
        assert state["generation"]
        assert state["db_mtime_ns"] is not None
        assert trip.count == 0


# --------------------------------------------------------------------------- #
# 2. the promotion boundary                                                    #
# --------------------------------------------------------------------------- #


class TestPromotionBoundary:
    @pytest.mark.parametrize("attr", CORPUS_WIDE)
    def test_every_corpus_wide_name_promotes(self, store, monkeypatch, attr):
        trip = _Tripwire(store, monkeypatch)
        view = store.open_selective(OWNER, NAME, symbol_ids=["a.py::alpha#function"])
        getattr(view, attr)
        assert trip.count == 1, f"{attr} answered from a partial symbol set"
        assert view.promoted and view.promoted_by == attr

    def test_symbols_is_the_whole_corpus_after_promotion(self, store):
        view = store.open_selective(OWNER, NAME, symbol_ids=["a.py::alpha#function"])
        assert len(view.symbols) == 4, (
            "a partial `symbols` list is a false-absence generator; this is the "
            "single most important assertion in the file"
        )

    def test_search_matches_the_full_index_exactly(self, store):
        """No change to ordering or response fields — the close condition."""
        full = store.load_index(OWNER, NAME)
        _cache_clear()
        view = store.open_selective(OWNER, NAME, symbol_ids=["a.py::alpha#function"])
        assert [s["id"] for s in view.search("gamma")] == [
            s["id"] for s in full.search("gamma")
        ]

    def test_an_unknown_future_field_promotes(self, store, monkeypatch):
        """Fail-safe direction. A field added to CodeIndex that this module has
        never heard of must be slow, not wrong."""
        trip = _Tripwire(store, monkeypatch)
        view = store.open_selective(OWNER, NAME, symbol_ids=["a.py::alpha#function"])
        monkeypatch.setattr(
            CodeIndex, "a_field_invented_later", "value", raising=False
        )
        assert view.a_field_invented_later == "value"
        assert trip.count == 1

    def test_promotion_happens_at_most_once(self, store, monkeypatch):
        trip = _Tripwire(store, monkeypatch)
        view = store.open_selective(OWNER, NAME, symbol_ids=["a.py::alpha#function"])
        for _ in range(5):
            len(view.symbols)
            view.search("alpha")
            view.get_callers_by_name()
        assert trip.count == 1
        assert view.promotions == 1

    def test_exact_and_corpus_wide_do_not_overlap(self):
        """A name on both lists would mean the boundary is ambiguous."""
        assert not (set(EXACT_FIELDS) & set(CORPUS_WIDE))

    def test_every_exact_field_exists_on_code_index(self):
        """A typo here silently routes a real field through __getattr__ and
        promotes the corpus on every request — expensive, not wrong, but it
        would erase the arc's entire benefit invisibly."""
        missing = [f for f in EXACT_FIELDS if f not in CodeIndex.__dataclass_fields__]
        assert not missing, missing

    def test_a_failed_targeted_fetch_promotes_rather_than_reporting_absent(
        self, store, monkeypatch
    ):
        trip = _Tripwire(store, monkeypatch)
        view = store.open_selective(OWNER, NAME, symbol_ids=["a.py::alpha#function"])

        def boom(_sid):
            raise sqlite3.OperationalError("database is locked")

        object.__setattr__(view, "_fetch_symbol", boom)
        sym = view.get_symbol("c.py::delta#function")
        assert sym is not None and sym["name"] == "delta", (
            "could-not-establish must never be served as could-not-find"
        )
        assert trip.count == 1


# --------------------------------------------------------------------------- #
# 3. the paths that must NOT take this route                                   #
# --------------------------------------------------------------------------- #


class TestWiredCallerParity:
    """`get_symbol_source` is the canonical narrow call. Its responses must not
    move at all — the close condition says "no change to errors, suggestions,
    ordering, branch behaviour, or response fields"."""

    def _run(self, tmp_path, monkeypatch, selective: bool, **kwargs):
        from jcodemunch_mcp.storage import IndexStore
        from jcodemunch_mcp.tools.get_symbol import get_symbol_source

        if not selective:
            monkeypatch.setattr(
                IndexStore, "open_selective", lambda *a, **k: None
            )
        _cache_clear()
        return get_symbol_source(
            repo=f"{OWNER}/{NAME}", storage_path=str(tmp_path), **kwargs
        )

    def test_a_hit_is_byte_identical(self, store, tmp_path, monkeypatch):
        args = {"symbol_id": "a.py::alpha#function"}
        sel = self._run(tmp_path, monkeypatch, True, **args)
        old = self._run(tmp_path, monkeypatch, False, **args)
        sel.pop("_meta", None)
        old.pop("_meta", None)
        assert sel == old
        # The fixture stores no byte offsets, so `source` is empty on BOTH
        # paths. Parity is the gate here; content is covered by the ordinary
        # get_symbol_source suite against a real index.
        assert sel["name"] == "alpha" and sel["file"] == "a.py"

    def test_a_miss_keeps_its_did_you_mean(self, store, tmp_path, monkeypatch):
        """The suggestion needs the whole corpus, so this promotes — and the
        promoted answer must equal the un-selective one exactly."""
        args = {"symbol_id": "a.py::alpah#function"}
        sel = self._run(tmp_path, monkeypatch, True, **args)
        old = self._run(tmp_path, monkeypatch, False, **args)
        sel.pop("_meta", None)
        old.pop("_meta", None)
        assert sel == old
        assert "did_you_mean" in sel or "error" in sel

    def test_a_batch_is_byte_identical(self, store, tmp_path, monkeypatch):
        args = {"symbol_ids": ["a.py::alpha#function", "b.py::gamma#function"]}
        sel = self._run(tmp_path, monkeypatch, True, **args)
        old = self._run(tmp_path, monkeypatch, False, **args)
        sel.pop("_meta", None)
        old.pop("_meta", None)
        assert sel == old
        assert [s["name"] for s in sel["symbols"]] == ["alpha", "gamma"]

    def test_a_hit_does_not_hydrate(self, store, tmp_path, monkeypatch):
        """The point of the arc, measured at the tool rather than the store."""
        from jcodemunch_mcp.storage.sqlite_store import SQLiteIndexStore

        calls = []
        real = SQLiteIndexStore.load_index
        monkeypatch.setattr(
            SQLiteIndexStore, "load_index",
            lambda self, *a, **k: (calls.append(a), real(self, *a, **k))[1],
        )
        self._run(tmp_path, monkeypatch, True, symbol_id="a.py::alpha#function")
        assert calls == [], "serving a known id must not read the whole corpus"


class TestRefusals:
    def test_a_branch_request_never_selects(self, store):
        """A branch view is load_index composed with a delta, including the
        stale-base warning. Reproducing that here would change branch behaviour;
        None sends the caller to the ordinary path."""
        assert store.open_selective(OWNER, NAME, symbol_ids=["x"], branch="feat") is None

    def test_an_unindexed_repo_returns_none_not_an_empty_view(self, store):
        assert store.open_selective(OWNER, "never-indexed", symbol_ids=["x"]) is None

    def test_a_cached_full_index_is_reused_rather_than_re_read(self, store):
        """Paying for a narrower read when the exact answer is already resident
        is strictly worse. The cached CodeIndex comes back as itself."""
        full = store.load_index(OWNER, NAME)
        again = store.open_selective(OWNER, NAME, symbol_ids=["a.py::alpha#function"])
        assert again is full

    def test_the_read_leaves_no_sidecars(self, store, tmp_path):
        """Arc 1's contract, inherited: a selective read must not look like a
        rebuild to a concurrent scan."""
        db = Path(store._db_path(OWNER, NAME))
        wal = Path(str(db) + "-wal")
        if wal.exists():
            pytest.skip("writer left sidecars in place; the absent case is the gate")
        before = db.stat().st_mtime_ns
        _cache_clear()
        view = store.open_selective(OWNER, NAME, symbol_ids=["a.py::alpha#function"])
        assert view.get_symbol("a.py::alpha#function") is not None
        assert not wal.exists()
        assert db.stat().st_mtime_ns == before
