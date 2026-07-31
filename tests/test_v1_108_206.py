"""v1.108.206 — the callee name index is built once per traversal, not per node.

Reported by @rknighton (#396) with 636 raw timing rows attached: the whole-repo
``name -> definitions`` map inside ``_callees_from_references`` was rebuilt for
every visited node carrying call references, so a django traversal paid
O(nodes x symbols) where O(symbols) would do.

The performance claim is his and it is measured; these tests hold the two things
a reader cannot check from a benchmark:

* the WORK COUNT actually dropped (and stayed at zero where it always was), and
* the RESULT did not move — asserted against an oracle that is the pre-change
  algorithm copied verbatim, over adversarial and randomized fixtures.

The duplicate-name asymmetry is the trap he named: same-file resolution emits
EVERY same-named definition, cross-file emits the FIRST per file. A faster
lookup that returned one same-file match would read as a win and be a
regression, so it gets its own non-vacuous test.
"""

import random

import pytest

from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.tools import _call_graph
from jcodemunch_mcp.tools._call_graph import (
    _CalleeNameIndex,
    _callees_from_references,
    find_direct_callees,
)
from jcodemunch_mcp.tools.get_call_hierarchy import get_call_hierarchy
from jcodemunch_mcp.tools.index_folder import index_folder


# ---------------------------------------------------------------------------
# The oracle: the algorithm exactly as it stood at 1.108.204.
# ---------------------------------------------------------------------------

def _legacy_callees_from_references(index, sym, symbols_by_file):
    """v1.108.204's `_callees_from_references`, copied verbatim.

    Kept as a literal copy rather than a paraphrase: a re-derived oracle only
    proves the two implementations share an author's understanding, which is the
    thing under test.
    """
    call_refs = sym.get("call_references", [])
    if not call_refs:
        return []

    name_index = {}
    for file_path, syms in symbols_by_file.items():
        for s in syms:
            name = s.get("name", "")
            if name:
                name_index.setdefault(name, []).append((name, file_path))

    sym_name = sym.get("name", "")
    sym_file = sym.get("file", "")
    seen_ids = set()
    results = []

    for called_name in call_refs:
        if called_name == sym_name:
            continue
        if sym_file:
            for name, file_path in name_index.get(called_name, []):
                if file_path == sym_file:
                    cand = symbols_by_file[sym_file]
                    for s in cand:
                        if s.get("name") == called_name:
                            cid = s.get("id", "")
                            if cid and cid not in seen_ids:
                                seen_ids.add(cid)
                                results.append({
                                    "id": cid,
                                    "name": called_name,
                                    "kind": s.get("kind", ""),
                                    "file": sym_file,
                                    "line": s.get("line", 0),
                                    "resolution": "ast_resolved",
                                })
                    break
        for name, file_path in name_index.get(called_name, []):
            if file_path != sym_file:
                for s in symbols_by_file.get(file_path, []):
                    if s.get("name") == called_name:
                        cid = s.get("id", "")
                        if cid and cid not in seen_ids:
                            seen_ids.add(cid)
                            results.append({
                                "id": cid,
                                "name": called_name,
                                "kind": s.get("kind", ""),
                                "file": file_path,
                                "line": s.get("line", 0),
                                "resolution": "ast_inferred",
                            })
                        break

    return results


def _assert_matches_legacy(sym, symbols_by_file, *, shared_index=None):
    """Both call shapes must agree with the oracle, list-for-list."""
    expected = _legacy_callees_from_references(None, sym, symbols_by_file)
    standalone = _callees_from_references(None, sym, symbols_by_file)
    assert standalone == expected
    if shared_index is not None:
        assert _callees_from_references(None, sym, symbols_by_file, shared_index) == expected
    return expected


class _CountingCalleeIndex(_CalleeNameIndex):
    """Counts how many times the map is actually constructed."""

    builds = 0

    def get(self, name):
        if self._index is None:
            type(self).builds += 1
        return super().get(name)


@pytest.fixture
def counting_index(monkeypatch):
    _CountingCalleeIndex.builds = 0
    monkeypatch.setattr(_call_graph, "_CalleeNameIndex", _CountingCalleeIndex)
    return _CountingCalleeIndex


# ---------------------------------------------------------------------------
# Work count
# ---------------------------------------------------------------------------

def _fan_out_repo(tmp_path):
    """A traversal with several nodes that each resolve callees (Q > 1)."""
    src = tmp_path / "src"
    store = tmp_path / "store"
    src.mkdir()
    store.mkdir()

    (src / "leaf.py").write_text(
        "def alpha():\n    return 1\n\n"
        "def beta():\n    return 2\n\n"
        "def gamma():\n    return 3\n"
    )
    (src / "mid.py").write_text(
        "from leaf import alpha, beta, gamma\n\n"
        "def one():\n    return alpha()\n\n"
        "def two():\n    return beta()\n\n"
        "def three():\n    return gamma()\n"
    )
    (src / "top.py").write_text(
        "from mid import one, two, three\n\n"
        "def entry():\n    return one() + two() + three()\n"
    )

    result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert result["success"] is True
    return result["repo"], str(store)


class TestWorkCount:
    """@rknighton's acceptance criteria, as assertions on construction count."""

    def test_a_multi_node_traversal_builds_the_index_once(self, tmp_path, counting_index):
        repo, store = _fan_out_repo(tmp_path)
        out = get_call_hierarchy(
            repo, "entry", direction="callees", depth=3, storage_path=store
        )
        assert "error" not in out
        # Non-vacuity: the traversal must actually have resolved callees, or a
        # build count of 1 would prove nothing.
        assert out.get("callees"), "fixture stopped exercising the callee path"
        assert counting_index.builds == 1

    def test_direction_callers_never_builds_it(self, tmp_path, counting_index):
        repo, store = _fan_out_repo(tmp_path)
        out = get_call_hierarchy(
            repo, "alpha", direction="callers", depth=3, storage_path=store
        )
        assert "error" not in out
        assert out.get("callers"), "fixture stopped exercising the caller path"
        assert counting_index.builds == 0

    def test_a_node_with_no_call_references_builds_nothing(self, counting_index):
        """The eager-build trap: constructing the memo must cost nothing."""
        symbols_by_file = {"a.py": [{"id": "a.py::x", "name": "x", "line": 1}]}
        shared = counting_index(symbols_by_file)
        assert _callees_from_references(None, {"call_references": []}, symbols_by_file, shared) == []
        assert counting_index.builds == 0

    def test_a_fresh_request_that_needs_it_builds_exactly_once(self, tmp_path, counting_index):
        repo, store = _fan_out_repo(tmp_path)
        for _ in range(3):
            _CountingCalleeIndex.builds = 0
            out = get_call_hierarchy(
                repo, "entry", direction="both", depth=3, storage_path=store
            )
            assert "error" not in out
            assert counting_index.builds == 1


# ---------------------------------------------------------------------------
# Result identity
# ---------------------------------------------------------------------------

class TestDuplicateNameAsymmetry:
    """The regression that would look like a speedup."""

    def test_same_file_emits_every_definition(self):
        symbols_by_file = {
            "a.py": [
                {"id": "a.py::dup#1", "name": "dup", "kind": "function", "line": 1},
                {"id": "a.py::dup#2", "name": "dup", "kind": "function", "line": 9},
            ],
        }
        sym = {"id": "a.py::caller", "name": "caller", "file": "a.py",
               "call_references": ["dup"]}
        got = _assert_matches_legacy(sym, symbols_by_file)
        assert [r["id"] for r in got] == ["a.py::dup#1", "a.py::dup#2"], (
            "both same-file definitions must survive"
        )
        assert {r["resolution"] for r in got} == {"ast_resolved"}

    def test_cross_file_emits_the_first_definition_per_file(self):
        symbols_by_file = {
            "a.py": [{"id": "a.py::caller", "name": "caller", "line": 1}],
            "b.py": [
                {"id": "b.py::dup#1", "name": "dup", "kind": "function", "line": 1},
                {"id": "b.py::dup#2", "name": "dup", "kind": "function", "line": 9},
            ],
            "c.py": [{"id": "c.py::dup", "name": "dup", "kind": "function", "line": 4}],
        }
        sym = {"id": "a.py::caller", "name": "caller", "file": "a.py",
               "call_references": ["dup"]}
        got = _assert_matches_legacy(sym, symbols_by_file)
        assert [r["id"] for r in got] == ["b.py::dup#1", "c.py::dup"], (
            "one per file, first-appearance order, and NOT b.py::dup#2"
        )
        assert {r["resolution"] for r in got} == {"ast_inferred"}

    def test_same_file_and_cross_file_both_run(self):
        symbols_by_file = {
            "a.py": [
                {"id": "a.py::caller", "name": "caller", "line": 1},
                {"id": "a.py::dup", "name": "dup", "kind": "function", "line": 3},
            ],
            "b.py": [{"id": "b.py::dup", "name": "dup", "kind": "function", "line": 1}],
        }
        sym = {"id": "a.py::caller", "name": "caller", "file": "a.py",
               "call_references": ["dup"]}
        got = _assert_matches_legacy(sym, symbols_by_file)
        assert [(r["id"], r["resolution"]) for r in got] == [
            ("a.py::dup", "ast_resolved"),
            ("b.py::dup", "ast_inferred"),
        ]


class TestAdversarialFixtures:
    def test_self_recursion_is_skipped(self):
        symbols_by_file = {
            "a.py": [{"id": "a.py::f", "name": "f", "kind": "function", "line": 1}],
        }
        sym = {"id": "a.py::f", "name": "f", "file": "a.py", "call_references": ["f"]}
        assert _assert_matches_legacy(sym, symbols_by_file) == []

    def test_unnamed_symbols_are_not_resolvable(self):
        symbols_by_file = {
            "a.py": [{"id": "a.py::anon", "name": "", "kind": "function", "line": 1}],
        }
        sym = {"id": "a.py::caller", "name": "caller", "file": "a.py",
               "call_references": [""]}
        assert _assert_matches_legacy(sym, symbols_by_file) == []

    def test_a_symbol_with_no_id_is_not_emitted(self):
        symbols_by_file = {
            "b.py": [{"name": "dup", "kind": "function", "line": 1},
                     {"id": "b.py::dup", "name": "dup", "kind": "function", "line": 5}],
        }
        sym = {"id": "a.py::caller", "name": "caller", "file": "a.py",
               "call_references": ["dup"]}
        # The idless symbol is the first per file, so it consumes the slot and
        # b.py::dup is NOT rescued. Ugly, and preserved deliberately.
        assert _assert_matches_legacy(sym, symbols_by_file) == []

    def test_caller_file_absent_from_the_mapping(self):
        symbols_by_file = {
            "b.py": [{"id": "b.py::t", "name": "t", "kind": "function", "line": 1}],
        }
        sym = {"id": "z.py::caller", "name": "caller", "file": "z.py",
               "call_references": ["t"]}
        got = _assert_matches_legacy(sym, symbols_by_file)
        assert [r["id"] for r in got] == ["b.py::t"]

    def test_caller_with_no_file_uses_the_cross_file_branch_only(self):
        symbols_by_file = {
            "b.py": [{"id": "b.py::t", "name": "t", "kind": "function", "line": 1}],
        }
        sym = {"id": "?::caller", "name": "caller", "file": "",
               "call_references": ["t"]}
        got = _assert_matches_legacy(sym, symbols_by_file)
        assert [(r["id"], r["resolution"]) for r in got] == [("b.py::t", "ast_inferred")]

    def test_resolution_follows_the_container_key_not_the_file_field(self):
        """@rknighton's grouping-invariant warning, pinned.

        ``build_symbols_by_file`` keys by ``symbol["file"]`` so the two agree in
        production. A direct-object index that trusted ``symbol["file"]`` would
        diverge on a mapping where they don't, so the reported ``file`` is
        asserted to be the CONTAINER, matching the scan this replaced.
        """
        symbols_by_file = {
            "container.py": [
                {"id": "x::t", "name": "t", "kind": "function", "line": 1,
                 "file": "lying.py"},
            ],
        }
        sym = {"id": "a.py::caller", "name": "caller", "file": "a.py",
               "call_references": ["t"]}
        got = _assert_matches_legacy(sym, symbols_by_file)
        assert [r["file"] for r in got] == ["container.py"]


class TestRandomizedEquivalence:
    def test_randomized_fixtures_match_the_oracle(self):
        rng = random.Random(1108205)
        names = ["a", "b", "c", "d", ""]
        files = ["f0.py", "f1.py", "f2.py"]
        compared = 0
        for case in range(3000):
            symbols_by_file = {}
            for f in files:
                if rng.random() < 0.15:
                    continue
                syms = []
                for i in range(rng.randint(0, 4)):
                    name = rng.choice(names)
                    entry = {"name": name, "kind": "function", "line": rng.randint(1, 50)}
                    if rng.random() < 0.9:
                        entry["id"] = f"{f}::{name}#{i}"
                    syms.append(entry)
                symbols_by_file[f] = syms
            sym = {
                "id": "caller",
                "name": rng.choice(names),
                "file": rng.choice(files + [""]),
                "call_references": [rng.choice(names) for _ in range(rng.randint(0, 4))],
            }
            shared = _CalleeNameIndex(symbols_by_file)
            _assert_matches_legacy(sym, symbols_by_file, shared_index=shared)
            compared += 1
        assert compared == 3000

    def test_the_oracle_can_fail(self):
        """Non-vacuity: the comparison harness detects a real divergence."""
        symbols_by_file = {
            "b.py": [
                {"id": "b.py::dup#1", "name": "dup", "kind": "function", "line": 1},
                {"id": "b.py::dup#2", "name": "dup", "kind": "function", "line": 9},
            ],
        }
        sym = {"id": "a.py::caller", "name": "caller", "file": "a.py",
               "call_references": ["dup"]}
        legacy = _legacy_callees_from_references(None, sym, symbols_by_file)
        # The exact regression the cross-file branch is written to avoid.
        naive = [
            {"id": s["id"], "name": "dup", "kind": s["kind"], "file": "b.py",
             "line": s["line"], "resolution": "ast_inferred"}
            for s in symbols_by_file["b.py"]
        ]
        assert naive != legacy


class TestSharedIndexSafety:
    def test_a_cache_for_a_different_mapping_is_refused(self):
        """Threading mistake insurance: a stale memo must not answer."""
        first = {"a.py": [{"id": "a.py::t", "name": "t", "kind": "function", "line": 1}]}
        second = {"b.py": [{"id": "b.py::t", "name": "t", "kind": "function", "line": 2}]}
        stale = _CalleeNameIndex(first)
        stale.get("t")  # force the build against `first`
        sym = {"id": "z.py::caller", "name": "caller", "file": "z.py",
               "call_references": ["t"]}
        got = _callees_from_references(None, sym, second, stale)
        assert [r["id"] for r in got] == ["b.py::t"], "answered from the wrong mapping"
        assert got == _legacy_callees_from_references(None, sym, second)

    def test_find_direct_callees_without_a_shared_index_is_unchanged(self, tmp_path):
        repo, store = _fan_out_repo(tmp_path)
        owner, name = repo.split("/", 1)
        store_instance = IndexStore(base_path=store)
        index = store_instance.load_index(owner, name)
        symbols_by_file = _call_graph.build_symbols_by_file(index)
        sym = next(s for s in index.symbols if s["name"] == "entry")

        bare = find_direct_callees(index, store_instance, owner, name, sym, symbols_by_file)
        shared = find_direct_callees(
            index, store_instance, owner, name, sym, symbols_by_file,
            None, _CalleeNameIndex(symbols_by_file),
        )
        assert bare, "fixture stopped resolving callees"
        assert bare == shared
