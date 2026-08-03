"""The dead-code false-positive harness's own logic (benchmarks/deadcode_eval).

Only the pure parts are tested here. Collecting the oracle means running the
whole suite under coverage, which is not something a unit test can do to itself.
"""

import importlib.util
import json
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parents[1] / "benchmarks" / "deadcode_eval" / "run_eval.py"


@pytest.fixture(scope="module")
def harness():
    spec = importlib.util.spec_from_file_location("deadcode_eval_run", _HARNESS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Index:
    def __init__(self, symbols):
        self.symbols = symbols


def _sym(sid, file, line, end_line, kind="function"):
    return {"id": sid, "file": file, "line": line, "end_line": end_line, "kind": kind}


class TestProvenAlive:
    def test_body_execution_proves_alive(self, harness):
        idx = _Index([_sym("m.py::f#function", "m.py", 10, 14)])
        executed = {"m.py": {11, 12}}
        assert harness.proven_alive_symbols(idx, executed) == {"m.py::f#function"}

    def test_def_line_alone_does_not_prove_alive(self, harness):
        """⚠ The load-bearing subtlety.

        A `def` line executes at import time for every function in an imported
        module, whether or not the body ever runs. Counting it would make the
        oracle mean "this module was imported", and since the harness IMPORTS
        the codebase to index it, essentially everything would read as alive and
        the false-positive rate would be a fiction.
        """
        idx = _Index([_sym("m.py::f#function", "m.py", 10, 14)])
        assert harness.proven_alive_symbols(idx, {"m.py": {10}}) == set()

    def test_uncovered_symbol_is_not_claimed_dead(self, harness):
        """Absence from the oracle is absence of evidence, and the harness
        only ever ADDS to the alive set - it never labels anything dead."""
        idx = _Index([
            _sym("m.py::f#function", "m.py", 10, 14),
            _sym("m.py::g#function", "m.py", 20, 24),
        ])
        alive = harness.proven_alive_symbols(idx, {"m.py": {11}})
        assert alive == {"m.py::f#function"}
        assert "m.py::g#function" not in alive

    def test_non_function_kinds_are_ignored(self, harness):
        idx = _Index([_sym("m.py::C#class", "m.py", 10, 14, kind="class")])
        assert harness.proven_alive_symbols(idx, {"m.py": {11, 12}}) == set()

    def test_degenerate_spans_are_skipped(self, harness):
        """A one-line or unspanned symbol has no body to attribute a hit to."""
        idx = _Index([
            _sym("m.py::a#function", "m.py", 10, 10),
            _sym("m.py::b#function", "m.py", 0, 0),
        ])
        assert harness.proven_alive_symbols(idx, {"m.py": {10, 11}}) == set()

    def test_path_separators_and_case_are_normalised(self, harness):
        idx = _Index([_sym("s#function", "src\\pkg\\Mod.py", 5, 9)])
        executed = {"src/pkg/mod.py": {6}}
        assert harness.proven_alive_symbols(idx, executed) == {"s#function"}


class TestCoverageLoading:
    def test_rejects_a_report_with_no_files(self, harness, tmp_path):
        p = tmp_path / "cov.json"
        p.write_text(json.dumps({"meta": {}}), encoding="utf-8")
        with pytest.raises(SystemExit):
            harness.load_executed_lines(p, tmp_path)

    def test_files_with_no_executed_lines_are_dropped(self, harness, tmp_path):
        p = tmp_path / "cov.json"
        p.write_text(json.dumps({"files": {
            "a.py": {"executed_lines": [1, 2]},
            "b.py": {"executed_lines": []},
        }}), encoding="utf-8")
        out = harness.load_executed_lines(p, tmp_path)
        assert "a.py" in out
        assert "b.py" not in out
