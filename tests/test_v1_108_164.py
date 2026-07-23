"""v1.108.164: a path-shaped `repo` arg no longer escapes as a raw storage ValueError.

Origin: #376 (split out of #375), reported from a daily Ubuntu install. An agent that
doesn't have an owner/name id at hand guesses a path. A two-segment path split on the
FIRST separator left a `name` still carrying one, which the store rejects at
`_safe_repo_component` — outside every caller's handler, so it surfaced as a hard
`ValueError: Path separator in name` instead of a routed tool error.
"""

from types import SimpleNamespace

import pytest

from jcodemunch_mcp.tools import _utils
from jcodemunch_mcp.tools import resolve_repo as rr


def _store_with(repos=(), present=False):
    return SimpleNamespace(
        inspect_index=lambda o, n: SimpleNamespace(index_present=present),
        list_repos=lambda: list(repos),
    )


class TestMultiSegmentRepoArg:
    def test_file_path_raises_actionable_error_not_storage_valueerror(
        self, monkeypatch
    ):
        monkeypatch.setattr(rr, "_compute_repo_id", lambda p, store=None: "")
        monkeypatch.setattr(
            _utils, "IndexStore", lambda base_path=None: _store_with()
        )
        with pytest.raises(ValueError) as exc:
            _utils.resolve_repo("src/auth/service.ts")
        message = str(exc.value)
        assert "Path separator in name" not in message
        assert "is a path, not a repository id" in message
        assert "resolve_repo" in message and "index_folder" in message

    def test_file_shaped_arg_names_file_pattern(self, monkeypatch):
        monkeypatch.setattr(rr, "_compute_repo_id", lambda p, store=None: "")
        monkeypatch.setattr(
            _utils, "IndexStore", lambda base_path=None: _store_with()
        )
        with pytest.raises(ValueError) as exc:
            _utils.resolve_repo("src/auth/service.ts")
        assert "file_pattern='src/auth/service.ts'" in str(exc.value)

    def test_directory_shaped_arg_omits_the_file_pattern_hint(self, monkeypatch):
        # No suffix means the agent meant a checkout, not a file to scope to.
        monkeypatch.setattr(rr, "_compute_repo_id", lambda p, store=None: "")
        monkeypatch.setattr(
            _utils, "IndexStore", lambda base_path=None: _store_with()
        )
        with pytest.raises(ValueError) as exc:
            _utils.resolve_repo("local/foo/bar")
        assert "file_pattern" not in str(exc.value)

    def test_backslash_tail_also_routed(self, monkeypatch):
        monkeypatch.setattr(rr, "_compute_repo_id", lambda p, store=None: "")
        monkeypatch.setattr(
            _utils, "IndexStore", lambda base_path=None: _store_with()
        )
        with pytest.raises(ValueError) as exc:
            _utils.resolve_repo("local/foo\\bar")
        assert "is a path, not a repository id" in str(exc.value)

    def test_relative_path_that_is_indexed_resolves(self, monkeypatch, tmp_path):
        # 'apps/web/src' is too conservative for _looks_like_path, but if it really
        # is an indexed checkout we should resolve it rather than error.
        sub = tmp_path / "apps" / "web" / "src"
        sub.mkdir(parents=True)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(rr, "_compute_repo_id", lambda p, store=None: "")
        monkeypatch.setattr(
            _utils,
            "IndexStore",
            lambda base_path=None: _store_with(
                repos=[{"repo": "local/web", "source_root": str(sub)}]
            ),
        )
        assert tuple(_utils.resolve_repo("apps/web/src")) == ("local", "web")


class TestNoRegressionOnRealIds:
    def test_owner_name_id_untouched(self):
        assert tuple(_utils.resolve_repo("local/gin")) == ("local", "gin")

    def test_single_slash_missing_repo_keeps_its_own_error_path(self, monkeypatch):
        # One separator is a well-formed id shape; it must NOT be re-routed as a
        # path, so the caller still gets the index-not-loadable error downstream.
        monkeypatch.setattr(
            _utils, "IndexStore", lambda base_path=None: _store_with()
        )
        assert tuple(_utils.resolve_repo("tools/agents.ts")) == (
            "tools",
            "agents.ts",
        )


class TestSearchSymbolsSurfacesItInBand:
    def test_path_shaped_repo_returns_error_dict(self, monkeypatch):
        from jcodemunch_mcp.tools.search_symbols import search_symbols

        monkeypatch.setattr(rr, "_compute_repo_id", lambda p, store=None: "")
        monkeypatch.setattr(
            _utils, "IndexStore", lambda base_path=None: _store_with()
        )
        result = search_symbols(repo="src/auth/service.ts", query="login")
        assert isinstance(result, dict)
        assert "is a path, not a repository id" in result["error"]
