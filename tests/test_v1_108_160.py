"""v1.108.160: multi-checkout "different working tree" responses.

Origin: the 2026-07-22 bench run — a second working tree of an already-indexed
repo resolved (via git identity) to the sibling checkout's index, and
index_file's generic "run index_folder on the parent" remedy led the agent to
re-index ~3k files in-run (\\$5.64 vs \\$2.90 for the tainted rep). Both surfaces
now name the situation and the cheap remedies.
"""

import subprocess
from pathlib import Path
from types import SimpleNamespace

from jcodemunch_mcp.tools import index_file as index_file_mod
from jcodemunch_mcp.tools import resolve_repo as rr


class TestResolveRepoMismatchFlag:
    def _base(self, source_root):
        return {"repo": "gh/proj", "source_root": source_root, "_meta": {}}

    def test_mismatch_attaches_warning(self, tmp_path):
        indexed = tmp_path / "checkout_a"
        queried = tmp_path / "checkout_b"
        indexed.mkdir()
        queried.mkdir()
        result = self._base(str(indexed))
        rr._attach_working_tree_mismatch(result, queried.resolve())
        assert result["working_tree_mismatch"] is True
        assert "Different working tree" in result["warning"]
        assert "identity_mode='local'" in result["warning"]
        assert result["_meta"]["working_tree"]["indexed_root"] == str(indexed)

    def test_contained_path_untouched(self, tmp_path):
        indexed = tmp_path / "checkout_a"
        sub = indexed / "src"
        sub.mkdir(parents=True)
        result = self._base(str(indexed))
        rr._attach_working_tree_mismatch(result, sub.resolve())
        assert "working_tree_mismatch" not in result
        assert "warning" not in result

    def test_no_source_root_untouched(self, tmp_path):
        result = {"repo": "gh/proj", "_meta": {}}
        rr._attach_working_tree_mismatch(result, tmp_path.resolve())
        assert "working_tree_mismatch" not in result


class TestIndexFileSiblingCheckout:
    def _git_dir(self, root: Path) -> None:
        subprocess.run(
            ["git", "init", "-q", str(root)],
            check=True, stdin=subprocess.DEVNULL, capture_output=True,
        )

    def test_sibling_checkout_detected(self, tmp_path, monkeypatch):
        checkout_a = tmp_path / "a"
        checkout_b = tmp_path / "b"
        checkout_a.mkdir()
        checkout_b.mkdir()
        self._git_dir(checkout_b)
        f = checkout_b / "mod.py"
        f.write_text("x = 1\n")

        monkeypatch.setattr(rr, "_compute_repo_id", lambda p, store=None: "gh/proj")
        store = SimpleNamespace(
            inspect_index=lambda o, n: SimpleNamespace(
                index_present=True, source_root=str(checkout_a)
            )
        )
        err = index_file_mod._sibling_checkout_error(f.resolve(), store)
        assert err is not None
        assert "Different working tree" in err
        assert "gh/proj" in err
        assert "identity_mode='local'" in err

    def test_same_checkout_returns_none(self, tmp_path, monkeypatch):
        checkout = tmp_path / "a"
        checkout.mkdir()
        self._git_dir(checkout)
        f = checkout / "mod.py"
        f.write_text("x = 1\n")

        monkeypatch.setattr(rr, "_compute_repo_id", lambda p, store=None: "gh/proj")
        store = SimpleNamespace(
            inspect_index=lambda o, n: SimpleNamespace(
                index_present=True, source_root=str(checkout)
            )
        )
        assert index_file_mod._sibling_checkout_error(f.resolve(), store) is None

    def test_unindexed_identity_returns_none(self, tmp_path, monkeypatch):
        checkout = tmp_path / "b"
        checkout.mkdir()
        self._git_dir(checkout)
        f = checkout / "mod.py"
        f.write_text("x = 1\n")

        monkeypatch.setattr(rr, "_compute_repo_id", lambda p, store=None: "gh/proj")
        store = SimpleNamespace(
            inspect_index=lambda o, n: SimpleNamespace(index_present=False, source_root="")
        )
        assert index_file_mod._sibling_checkout_error(f.resolve(), store) is None

    def test_non_git_path_returns_none(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text("x = 1\n")
        store = SimpleNamespace(inspect_index=lambda o, n: SimpleNamespace(index_present=True))
        assert index_file_mod._sibling_checkout_error(f.resolve(), store) is None

    def test_index_file_surfaces_sibling_error(self, tmp_path, monkeypatch):
        """End-to-end: index_file with an empty store returns the sibling hint."""
        checkout_a = tmp_path / "a"
        checkout_b = tmp_path / "b"
        checkout_a.mkdir()
        checkout_b.mkdir()
        self._git_dir(checkout_b)
        f = checkout_b / "mod.py"
        f.write_text("x = 1\n")

        store_dir = tmp_path / "store"
        store_dir.mkdir()
        monkeypatch.setattr(rr, "_compute_repo_id", lambda p, store=None: "gh/proj")
        monkeypatch.setattr(
            index_file_mod.IndexStore, "list_repos", lambda self: [], raising=False
        )
        monkeypatch.setattr(
            index_file_mod.IndexStore,
            "inspect_index",
            lambda self, o, n: SimpleNamespace(
                index_present=True, source_root=str(checkout_a)
            ),
            raising=False,
        )
        out = index_file_mod.index_file(str(f), storage_path=str(store_dir))
        assert out["success"] is False
        assert out.get("skipped") == "different_working_tree"
        assert "Different working tree" in out["error"]
