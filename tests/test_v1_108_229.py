"""v1.108.229 — get_dead_code_v2 keeps module-level callers (#409).

``CodeIndex.get_callers_by_name`` is built from per-symbol ``call_references``,
so a call written at module level belongs to no symbol and never enters it.
Which caller-detection path ran was decided by ``if callers_by_name:`` — a
repo-wide property — so a tree where no symbol called another fell through to
the text heuristic and answered correctly, and adding a single intra-file call
anywhere flipped the whole repository onto a path that structurally could not
see import-time calls.

⚠ The load-bearing fixture detail: every repo here contains BOTH an intra-symbol
call AND a module-level call. Either one alone passes on the pre-fix code, which
is why this defect survived — the obvious two-file fixture takes the fallback.
"""

import pytest

from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.get_dead_code_v2 import get_dead_code_v2


def _build(tmp_path, files: dict[str, str]):
    src = tmp_path / "src"
    store = tmp_path / "store"
    src.mkdir()
    store.mkdir()
    for rel, body in files.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert r["success"] is True, r
    return r["repo"], str(store)


def _signals(repo, store, symbol_name):
    """Signals recorded for *symbol_name*, or None when it was not flagged."""
    res = get_dead_code_v2(repo=repo, storage_path=store, min_confidence=0.0)
    for d in res.get("dead_symbols", []):
        if d["name"] == symbol_name:
            return d["signals"]
    return None


# The AST fast path is only reachable when some symbol has a call_reference.
# ``alpha`` calling ``beta`` is what arms it; without that line every assertion
# below passes on the broken code too.
_LIB_WITH_INTRA_CALL = (
    "def alpha(x):\n"
    "    return beta(x) + 1\n"
    "\n"
    "def beta(x):\n"
    "    return x * 2\n"
    "\n"
    "def never_called(x):\n"
    "    return x - 1\n"
)


class TestModuleLevelCallers:
    def test_module_level_caller_is_found(self, tmp_path):
        """A call at import time keeps its callee off the no_callers list."""
        repo, store = _build(tmp_path, {
            "lib.py": _LIB_WITH_INTRA_CALL,
            "main.py": (
                "from lib import alpha\n"
                "\n"
                "if __name__ == '__main__':\n"
                "    print(alpha(3))\n"
            ),
        })
        signals = _signals(repo, store, "alpha")
        assert signals is not None, "alpha should still be analysed"
        assert "no_callers" not in signals, (
            "alpha is called at module level in main.py; the AST caller index "
            "cannot record that, and the module-level sweep must recover it"
        )

    def test_intra_symbol_caller_still_found(self, tmp_path):
        """The AST fast path keeps working; the sweep only adds to it."""
        repo, store = _build(tmp_path, {
            "lib.py": _LIB_WITH_INTRA_CALL,
            "main.py": "from lib import alpha\n\nprint(alpha(3))\n",
        })
        signals = _signals(repo, store, "beta")
        assert signals is not None
        assert "no_callers" not in signals, "beta is called by alpha"

    def test_genuinely_dead_symbol_still_flagged(self, tmp_path):
        """The sweep must not rescue everything it looks at."""
        repo, store = _build(tmp_path, {
            "lib.py": _LIB_WITH_INTRA_CALL,
            "main.py": "from lib import alpha\n\nprint(alpha(3))\n",
        })
        signals = _signals(repo, store, "never_called")
        assert signals is not None, "never_called should be flagged"
        assert "no_callers" in signals, (
            "never_called has no caller anywhere; rescuing it would trade "
            "false positives for false negatives"
        )

    def test_self_mention_is_not_a_caller(self, tmp_path):
        """A symbol's own body is subtracted before the residue is scanned."""
        repo, store = _build(tmp_path, {
            "lib.py": (
                "def alpha(x):\n"
                "    return beta(x)\n"
                "\n"
                "def beta(x):\n"
                "    return x\n"
                "\n"
                "def recurse(n):\n"
                "    # mentions recurse, but only inside its own body\n"
                "    return recurse(n - 1) if n else 0\n"
            ),
            "main.py": "from lib import alpha\n\nprint(alpha(1))\n",
        })
        signals = _signals(repo, store, "recurse")
        assert signals is not None
        assert "no_callers" in signals, (
            "self-recursion is not a caller for the dead-code question"
        )

    @pytest.mark.parametrize("arm_fast_path", [True, False])
    def test_answer_does_not_depend_on_an_unrelated_call(self, tmp_path, arm_fast_path):
        """The defect in one sentence: an unrelated edge changed the verdict.

        ``alpha``'s caller is identical in both arms. The only difference is
        whether some other symbol in the tree calls a sibling, which is what
        selects the caller-detection path. Pre-fix these two arms disagreed.
        """
        lib = (
            "def alpha(x):\n"
            "    return beta(x)\n"
            "\n"
            "def beta(x):\n"
            "    return x\n"
        ) if arm_fast_path else (
            "def alpha(x):\n"
            "    return x + 1\n"
        )
        repo, store = _build(tmp_path, {
            "lib.py": lib,
            "main.py": "from lib import alpha\n\nprint(alpha(3))\n",
        })
        signals = _signals(repo, store, "alpha")
        assert signals is not None
        assert "no_callers" not in signals
