"""Recurrence guard: every verdict that can prove absence must be able to see a rebuild.

The absence chokepoint in ``server.py`` is deliberately **generic** — it fires on
any ``_meta.verdict`` dict and mints a citable ``absent:<sha>`` ref whenever the
state is ``absent``. That generality is what makes the contract cheap to extend,
and it is also what makes it easy to break: a tool only has to *emit* a verdict
to start minting absence proofs, whether or not anyone wired it for the v1.108.168
rebuilding rule.

That has now gone wrong twice for the same structural reason:

* v1.108.168 wired ``search_symbols`` and ``get_ranked_context`` and missed
  ``search_text`` (fixed in v1.108.169).
* The index-aware wrappers ``symbol_verdict_for_index`` / ``file_verdict_for_index``
  accept an index but delegate to builders that have no ``index_changed`` parameter
  at all, so the file/symbol tools have never been able to refuse an absence claim
  over an index being rewritten.

These tests encode the invariant itself rather than re-checking the instances, so
the *next* verdict-emitting tool cannot quietly join the list.
"""

import ast
import pathlib

import pytest

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "jcodemunch_mcp"
TOOLS = SRC / "tools"
VERDICT_MODULE = SRC / "retrieval" / "verdict.py"

# Verdict builders that take a live index and can therefore, in principle,
# re-stat it to detect a rebuild underneath the scan.
INDEX_AWARE_WRAPPERS = {"symbol_verdict_for_index", "file_verdict_for_index"}

# KNOWN GAP — a ratchet, not an exemption. CLOSED in v1.108.170.
#
# Both wrappers now thread `index_changed` into their builders, so the file and
# symbol tools refuse an absence claim over an index being rewritten just as the
# search tools do. The set is kept (empty) rather than deleted: it is the
# mechanism that forces a future gap to be declared here instead of shipping
# silently, and `test_index_aware_wrapper_gap_does_not_widen` fails if a listed
# wrapper turns out to be wired — so a stale entry can never rot into an
# exemption.
KNOWN_UNWIRED_WRAPPERS: set[str] = set()


def _call_name(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ""


def _calls_in(path: pathlib.Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            yield node


def _aliases_for(path: pathlib.Path, target: str) -> set[str]:
    """Local names bound to ``target`` in this module.

    Every tool imports it lazily and renames it
    (``from ..retrieval.verdict import build_verdict as _build_verdict``), so a
    plain name match finds nothing and the scan passes vacuously. That is the
    exact failure `test_the_guard_is_not_vacuous` exists to catch.
    """
    names = {target}
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name == target:
                    names.add(a.asname or a.name)
    return names


def _verdict_calls(path: pathlib.Path):
    names = _aliases_for(path, "build_verdict")
    for call in _calls_in(path):
        if _call_name(call) in names:
            yield call


def _has_kwarg(node: ast.Call, name: str) -> bool:
    return any(kw.arg == name for kw in node.keywords)


def _tool_modules():
    return sorted(p for p in TOOLS.glob("*.py") if p.name != "__init__.py")


def test_every_build_verdict_call_passes_index_changed():
    """A tool that builds a verdict must say whether the index moved under it.

    Without this, a zero-result scan reaches `absent` and the generic chokepoint
    mints a citable ref, with the 5th refusal rule structurally unable to fire.
    """
    offenders = []
    for path in _tool_modules():
        for call in _verdict_calls(path):
            if not _has_kwarg(call, "index_changed"):
                offenders.append(f"{path.name}:{call.lineno}")
    assert not offenders, (
        "build_verdict() called without index_changed= at: "
        + ", ".join(offenders)
        + ". These can mint absence evidence over an index being rewritten. "
        "Pass index_changed=index_changed_since_load(index)."
    )


def test_build_verdict_still_accepts_index_changed():
    """Guard the guard: if the parameter is renamed, the test above goes vacuous."""
    import inspect

    from jcodemunch_mcp.retrieval.verdict import build_verdict

    assert "index_changed" in inspect.signature(build_verdict).parameters


def test_the_guard_is_not_vacuous():
    """At least one real build_verdict call must exist, or the scan proves nothing."""
    found = sum(1 for path in _tool_modules() for _ in _verdict_calls(path))
    assert found >= 3, f"expected the known verdict-emitting tools, found {found}"


@pytest.mark.parametrize("wrapper", sorted(INDEX_AWARE_WRAPPERS))
def test_index_aware_wrapper_gap_does_not_widen(wrapper):
    """The wrappers take an index; each must either use it or be a tracked gap."""
    tree = ast.parse(VERDICT_MODULE.read_text(encoding="utf-8"))
    fn = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == wrapper
        ),
        None,
    )
    assert fn is not None, f"{wrapper} not found — update this guard"

    wired = any(
        _has_kwarg(call, "index_changed")
        for call in ast.walk(fn)
        if isinstance(call, ast.Call)
    )
    if wrapper in KNOWN_UNWIRED_WRAPPERS:
        assert not wired, (
            f"{wrapper} now passes index_changed — remove it from "
            "KNOWN_UNWIRED_WRAPPERS so the ratchet tightens."
        )
    else:
        assert wired, (
            f"{wrapper} takes an index but never passes index_changed, so the "
            "tools using it can mint absence evidence over a rebuilding index."
        )


def test_known_gap_set_is_a_subset_of_real_wrappers():
    """A stale entry would silently exempt a wrapper that no longer exists."""
    assert KNOWN_UNWIRED_WRAPPERS <= INDEX_AWARE_WRAPPERS


def test_wrapper_verdicts_really_can_reach_absent():
    """Pins why the gap matters: these states feed note_absence's `absent` gate."""
    from jcodemunch_mcp.retrieval.verdict import (
        build_file_verdict,
        build_symbol_verdict,
    )

    assert build_symbol_verdict(found_count=0, requested_id="a.py::f")["state"] == "absent"
    assert build_file_verdict(present=False, requested_path="a.py")["state"] == "absent"
