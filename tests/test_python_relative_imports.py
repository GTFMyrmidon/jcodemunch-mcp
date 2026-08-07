"""Python package-relative imports must build import-graph edges (jcm#423).

`from ..parser.fqn import x` is not a path. The leading dots count package
levels and the rest is a dotted module path, so joining it as a filesystem path
(the JS/TS reading) produces the single segment `tools/..parser.fqn` and matches
nothing. Package-relative Python imports therefore built no edge at all: 71 of
818 resolved on this repository, 8.7%.

Everything gated on the import graph inherited that -- `find_importers`,
`get_blast_radius`, `get_dependency_graph`, `get_call_hierarchy`'s callers
direction, and `check_delete_safe`. The reported symptom was a live production
symbol presenting as imported by tests only, which is exactly the bucket a
delete-safety check calls removable.

Reported by @faxik with two-ended evidence on a closed-source repo, and with the
section explaining why a small fixture could NOT reproduce it -- which is what
located the bug: a flat two-file fixture uses specifiers that already resolve.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.parser.imports import resolve_specifier


# A package deep enough to exercise multi-level climbs. A FLAT fixture cannot
# fail this class, which is the whole reason it went unseen.
PKG_FILES = frozenset({
    "src/pkg/__init__.py",
    "src/pkg/tools/__init__.py",
    "src/pkg/tools/_utils.py",
    "src/pkg/tools/resolve_repo.py",
    "src/pkg/parser/__init__.py",
    "src/pkg/parser/fqn.py",
    "src/pkg/parser/imports.py",
    "src/pkg/storage/__init__.py",
    "src/pkg/deep/a/b/leaf.py",
})

IMPORTER = "src/pkg/tools/_utils.py"


class TestPythonRelativeResolution:
    @pytest.mark.parametrize("specifier,expected", [
        # One dot: the importer's own package.
        (".resolve_repo", "src/pkg/tools/resolve_repo.py"),
        # Two dots: up one package, then a dotted module path.
        ("..parser.fqn", "src/pkg/parser/fqn.py"),
        ("..parser.imports", "src/pkg/parser/imports.py"),
        # A package rather than a module resolves to its __init__.
        ("..storage", "src/pkg/storage/__init__.py"),
        ("..parser", "src/pkg/parser/__init__.py"),
    ])
    def test_dotted_relative_resolves(self, specifier, expected):
        assert resolve_specifier(specifier, IMPORTER, PKG_FILES) == expected

    def test_bare_dot_resolves_to_own_package_init(self):
        """`from . import x` names the importer's own package."""
        assert resolve_specifier(".", IMPORTER, PKG_FILES) == "src/pkg/tools/__init__.py"

    def test_multi_level_climb(self):
        deep = "src/pkg/deep/a/b/leaf.py"
        assert resolve_specifier("...a.b.leaf", deep, PKG_FILES) == deep

    def test_climbing_past_the_root_returns_none_not_a_guess(self):
        """Refusing beats resolving to something plausible and wrong."""
        assert resolve_specifier("." * 12 + "parser.fqn", IMPORTER, PKG_FILES) is None

    def test_unknown_module_returns_none(self):
        assert resolve_specifier("..parser.nonexistent", IMPORTER, PKG_FILES) is None


class TestJsPathReadingIsUntouched:
    """The branch is gated on the IMPORTER's extension, not the specifier shape.

    `./foo` and `../lib/util` must keep the path reading exactly -- a Python
    reading of them would be wrong, and this fix must not reach them.
    """

    JS_FILES = frozenset({
        "src/a/b.ts", "src/a/foo.ts", "src/lib/util.js", "src/a/foo.service.ts",
    })

    @pytest.mark.parametrize("specifier,importer,expected", [
        ("./foo", "src/a/b.ts", "src/a/foo.ts"),
        ("../lib/util", "src/a/b.js", "src/lib/util.js"),
        # Dotted basename (the #284 TS convention) must still resolve as a path.
        ("./foo.service", "src/a/b.ts", "src/a/foo.service.ts"),
    ])
    def test_js_relative_specifiers_unchanged(self, specifier, importer, expected):
        assert resolve_specifier(specifier, importer, self.JS_FILES) == expected


class TestRegressionGuards:
    """Controls: these pass on BOTH sides of the fix, so a green run here cannot
    be produced by relative resolution quietly ceasing to work."""

    def test_absolute_python_specifier_still_resolves(self):
        files = frozenset({"pkg/parser/fqn.py", "tests/test_fqn.py"})
        assert resolve_specifier("pkg.parser.fqn", "tests/test_fqn.py", files) == "pkg/parser/fqn.py"

    def test_external_package_still_unresolved(self):
        """stdlib and third-party imports have no file in the index."""
        assert resolve_specifier("logging", IMPORTER, PKG_FILES) is None
        assert resolve_specifier("pathlib", IMPORTER, PKG_FILES) is None


class TestReportedScenario:
    """The shape @faxik reported: production importer invisible, tests visible.

    The production caller reaches the callee through a package-relative import;
    the test reaches it through an absolute one. Only the second resolved, so
    the symbol read as test-only.
    """

    def test_production_and_test_importers_both_resolve(self):
        files = frozenset({
            "src/pkg/parser/fqn.py",
            "src/pkg/tools/_utils.py",
            "tests/test_fqn.py",
        })
        production = resolve_specifier("..parser.fqn", "src/pkg/tools/_utils.py", files)
        test = resolve_specifier("src.pkg.parser.fqn", "tests/test_fqn.py", files)
        assert production == "src/pkg/parser/fqn.py", "production importer must be visible"
        assert test == "src/pkg/parser/fqn.py"


# --------------------------------------------------------------------------- #
# The disclosure half of #423: a callers answer must say when it could not
# search part of the graph, whether or not the count is zero.
# --------------------------------------------------------------------------- #

class TestCallerGateDisclosure:
    """`caller_count` alone cannot distinguish "nothing calls this" from "the
    query never looked where the caller lives"."""

    def _index(self):
        """Minimal index: prod.py calls target() but resolves no import edge."""
        class _Idx:
            source_files = ["pkg/target.py", "pkg/prod.py", "tests/test_target.py"]

            def get_callers_by_name(self):
                return {
                    ("pkg/prod.py", "target"): ["pkg/prod.py::caller#function"],
                    ("tests/test_target.py", "target"): ["tests/test_target.py::t#function"],
                }
        return _Idx()

    def test_discloses_excluded_files_when_answer_is_empty(self):
        from jcodemunch_mcp.tools.get_call_hierarchy import _attach_caller_gate_disclosure

        resp = {"callers": [], "caller_count": 0, "_meta": {}}
        _attach_caller_gate_disclosure(
            resp, self._index(), {"name": "target", "file": "pkg/target.py"}, {}
        )
        inc = resp["_meta"]["caller_graph_incomplete"]
        assert inc["excluded_file_count"] == 2
        assert inc["reason"] == "import_graph_did_not_reach"

    def test_empty_answer_is_not_citable_as_absence(self):
        from jcodemunch_mcp.tools.get_call_hierarchy import _attach_caller_gate_disclosure

        resp = {"callers": [], "caller_count": 0, "_meta": {}}
        _attach_caller_gate_disclosure(
            resp, self._index(), {"name": "target", "file": "pkg/target.py"}, {}
        )
        verdict = resp["_meta"]["verdict"]
        # The verdict layer spells this `absence_refused`, not `absence_citable`.
        assert verdict.get("absence_refused") is True, verdict
        assert verdict.get("state") == "degraded", verdict

    def test_discloses_even_when_callers_were_returned(self):
        """The report's second case: 16 callers, all tests, production excluded.

        Gating this on caller_count == 0 would have reported that one as clean.
        """
        from jcodemunch_mcp.tools.get_call_hierarchy import _attach_caller_gate_disclosure

        resp = {
            "callers": [{"id": "tests/test_target.py::t#function", "file": "tests/test_target.py"}],
            "caller_count": 1,
            "_meta": {},
        }
        _attach_caller_gate_disclosure(
            resp, self._index(), {"name": "target", "file": "pkg/target.py"},
            {"pkg/target.py": ["tests/test_target.py"]},
        )
        inc = resp["_meta"]["caller_graph_incomplete"]
        assert inc["excluded_files"] == ["pkg/prod.py"], inc
        # A non-empty answer still gets no absence verdict — there is nothing
        # absent to certify — but the incompleteness is on the record.
        assert "verdict" not in resp["_meta"]

    def test_silent_when_the_graph_reached_everything(self):
        """No disclosure to make when every calling file was searched."""
        from jcodemunch_mcp.tools.get_call_hierarchy import _attach_caller_gate_disclosure

        resp = {"callers": [], "caller_count": 0, "_meta": {}}
        _attach_caller_gate_disclosure(
            resp, self._index(), {"name": "target", "file": "pkg/target.py"},
            {"pkg/target.py": ["pkg/prod.py", "tests/test_target.py"]},
        )
        assert "caller_graph_incomplete" not in resp["_meta"]
        assert "verdict" not in resp["_meta"]
