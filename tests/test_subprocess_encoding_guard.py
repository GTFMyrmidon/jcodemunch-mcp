"""Recurrence guard: a text-mode subprocess call must name its encoding.

``subprocess.run(..., text=True)`` with no ``encoding=`` decodes the child's
output with ``locale.getpreferredencoding()``. On a stock Windows box that is
**cp1252**, and git speaks UTF-8 unconditionally. One non-ASCII byte in an
author name, a commit subject or a path and the decode raises.

The failure mode is worse than a normal exception, which is why this needs a
test rather than a habit:

* ``UnicodeDecodeError`` is raised inside ``subprocess``'s **reader thread**,
  so it prints an unhandled thread traceback nobody catches.
* ``result.stdout`` then comes back ``None``, and the caller dies somewhere
  else entirely with ``'NoneType' object has no attribute 'splitlines'`` — a
  message that names neither git nor encoding.
* Every ``try/except`` around the call still runs, so the symptom is a provider
  or tool that reports itself "skipped" rather than broken. Reproduced on
  ``GitBlameProvider``: one commit authored by ``Ývonne`` (UTF-8 ``c3 9d``;
  ``9d`` is undefined in cp1252) drops ``last_author``/``last_modified`` for
  **every file in the repository**, and the index still reports success.

None of this shows up on a UTF-8 dev box or in CI, which is Linux. The only
population that hits it is Windows users with a non-ASCII contributor — so the
convention cannot be maintained by noticing.

The rule: any call to ``run``/``Popen``/``check_output``/``check_call``/``call``
that passes ``text=`` or ``universal_newlines=`` must also pass ``encoding=``.
``errors="replace"`` alongside it is the house default — an author name is
decoration, and mangling one beats failing the index.
"""

import ast
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

# Trees that ship or run against a real user's repository.
GUARDED_TREES = ("src", "speedreview", "benchmarks", "scripts", "munch-bench")

# EXEMPT BY NAME, with a reason — not a blanket skip.
#
# `tests/` drives git against fixture repositories this suite creates itself,
# with ASCII authors and ASCII paths, so the kwarg would assert nothing about
# any code a user runs. It is listed here rather than merely left out of
# GUARDED_TREES so that adding a tree is a decision and not an oversight.
EXEMPT_TREES = ("tests",)

SUBPROCESS_FUNCS = {"run", "Popen", "check_output", "check_call", "call"}
TEXT_KWARGS = {"text", "universal_newlines"}

# KNOWN GAP — a ratchet, not an exemption. Empty as of v1.108.232, when all 36
# offending call sites were fixed. Kept (empty) rather than deleted: it is the
# mechanism that forces a future gap to be declared here instead of shipping
# silently, and `test_known_gap_does_not_rot` fails if a listed entry turns out
# to be compliant, so a stale entry cannot decay into a permanent excuse.
KNOWN_UNENCODED: set[str] = set()


def _call_name(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return ""


def _offenders_in_source(source: str, label: str) -> list[str]:
    """Return "label:lineno" for each text-mode call with no encoding."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node) not in SUBPROCESS_FUNCS:
            continue
        kwargs = {k.arg for k in node.keywords if k.arg}
        if not (kwargs & TEXT_KWARGS):
            continue
        if "encoding" in kwargs:
            continue
        out.append(f"{label}:{node.lineno}")
    return out


def _offenders_in(path: pathlib.Path) -> list[str]:
    return _offenders_in_source(
        path.read_text(encoding="utf-8", errors="replace"),
        path.relative_to(REPO_ROOT).as_posix(),
    )


def _guarded_files() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for tree in GUARDED_TREES:
        root = REPO_ROOT / tree
        if root.is_dir():
            files.extend(sorted(root.rglob("*.py")))
    return files


def _all_offenders() -> list[str]:
    found: list[str] = []
    for path in _guarded_files():
        found.extend(_offenders_in(path))
    return found


def test_guarded_trees_exist():
    """A renamed tree must break this test, not silently stop being scanned."""
    present = [t for t in GUARDED_TREES if (REPO_ROOT / t).is_dir()]
    assert "src" in present, "src/ must be scanned — it is the shipped package"
    assert _guarded_files(), "the guard scanned zero files; its scope is wrong"


def test_no_text_mode_subprocess_without_encoding():
    offenders = [o for o in _all_offenders() if o not in KNOWN_UNENCODED]
    assert not offenders, (
        "text-mode subprocess call with no encoding= — decodes git output with "
        "the platform default (cp1252 on Windows) and dies in subprocess's "
        "reader thread on any non-ASCII byte. Add "
        'encoding="utf-8", errors="replace":\n  ' + "\n  ".join(offenders)
    )


def test_known_gap_does_not_rot():
    """A listed gap that is actually fixed must be removed from the list."""
    live = set(_all_offenders())
    stale = KNOWN_UNENCODED - live
    assert not stale, (
        "KNOWN_UNENCODED lists call sites that are now compliant; delete them "
        "so the set cannot decay into a permanent exemption: " + ", ".join(sorted(stale))
    )


def test_exempt_trees_are_named_not_forgotten():
    """The exemption is a decision on the record, and it must not overlap scope."""
    assert not (set(EXEMPT_TREES) & set(GUARDED_TREES))
    for tree in EXEMPT_TREES:
        assert (REPO_ROOT / tree).is_dir(), f"exempt tree {tree!r} does not exist"


@pytest.mark.parametrize(
    "source,expected_hits",
    [
        ('subprocess.run(["git"], text=True)', 1),
        ('subprocess.run(["git"], universal_newlines=True)', 1),
        ('subprocess.run(["git"], text=True, encoding="utf-8")', 0),
        ('subprocess.Popen(["git"], text=True)', 1),
        ('subprocess.check_output(["git"], text=True)', 1),
        ('run(["git"], text=True)', 1),
        ('subprocess.run(["git"], capture_output=True)', 0),  # bytes mode is safe
        ('other.run(["git"], text=True)', 1),  # attribute match is deliberate
    ],
)
def test_detector_is_not_vacuous(source, expected_hits):
    """The scanner itself has to actually fire — a guard that finds nothing on
    a known-bad input passes forever. This drives the SAME function the
    repo-wide check uses, so the two cannot diverge."""
    assert len(_offenders_in_source(source, "sample.py")) == expected_hits
