"""v1.108.159: response-size steering + path-shaped repo acceptance.

Origin: the 2026-07-22 benchmark-harness measurement of v1.108.158 — steering
cut turns but heavy-repo cost stayed flat because payload weight per turn rose
(fat ranked-context capsules). This release (1) adds compress=True / tight
token_budget guidance to all three steering surfaces and (2) makes the shared
resolve_repo helper accept a filesystem path (repo='.', an absolute path) —
an observed agent retry shape that previously dead-ended.
"""

from types import SimpleNamespace

import pytest

from jcodemunch_mcp import server
from jcodemunch_mcp.tools import _utils
from jcodemunch_mcp.tools import resolve_repo as rr


class TestLooksLikePath:
    def test_dot_and_relative_forms(self):
        for p in (".", "..", "./sub", "../up", ".\\sub", "~", "~/repo"):
            assert _utils._looks_like_path(p), p

    def test_windows_and_absolute_paths(self):
        for p in ("C:\\MCPs\\gin", "C:/MCPs/gin", "/c/MCPs/gin", "sub\\dir"):
            assert _utils._looks_like_path(p), p

    def test_bare_names_and_repo_ids_do_not_match(self):
        for p in ("gin", "jcodemunch-mcp", "local/gin", "owner/name"):
            assert not _utils._looks_like_path(p), p


class TestPathRepoResolution:
    def test_dot_resolves_via_identity_probe(self, monkeypatch, tmp_path):
        monkeypatch.setattr(rr, "_compute_repo_id", lambda p, store=None: "local/foo")
        store = SimpleNamespace(
            inspect_index=lambda o, n: SimpleNamespace(index_present=True),
            list_repos=lambda: [],
        )
        monkeypatch.setattr(_utils, "IndexStore", lambda base_path=None: store)
        assert _utils.resolve_repo(".") == ("local", "foo")

    def test_falls_back_to_source_root_match(self, monkeypatch, tmp_path):
        def _boom(p, store=None):
            raise RuntimeError("identity probe unavailable")

        monkeypatch.setattr(rr, "_compute_repo_id", _boom)
        store = SimpleNamespace(
            inspect_index=lambda o, n: SimpleNamespace(index_present=False),
            list_repos=lambda: [
                {"repo": "local/other", "source_root": str(tmp_path / "other")},
                {"repo": "local/target", "source_root": str(tmp_path)},
            ],
        )
        monkeypatch.setattr(_utils, "IndexStore", lambda base_path=None: store)
        assert tuple(_utils.resolve_repo(str(tmp_path))) == ("local", "target")

    def test_unindexed_path_raises_with_remedy(self, monkeypatch, tmp_path):
        monkeypatch.setattr(rr, "_compute_repo_id", lambda p, store=None: "local/nope")
        store = SimpleNamespace(
            inspect_index=lambda o, n: SimpleNamespace(index_present=False),
            list_repos=lambda: [],
        )
        monkeypatch.setattr(_utils, "IndexStore", lambda base_path=None: store)
        with pytest.raises(ValueError) as exc:
            _utils.resolve_repo(str(tmp_path / "missing"))
        assert "index_folder" in str(exc.value)

    def test_owner_name_ids_untouched(self):
        # The owner/name fast path must not gain path probing.
        assert tuple(_utils.resolve_repo("local/gin")) == ("local", "gin")


class TestResponseSizeSteeringText:
    def test_order_description_mentions_compress(self):
        tools = {t.name: t for t in server._counter_front_door_tools()}
        assert "compress" in tools["order"].description

    def test_escalation_hint_mentions_compress_and_path(self):
        # The hint literal lives inline in call_tool; assert on the module source
        # so a reword that drops the guidance fails loudly.
        import inspect

        src = inspect.getsource(server)
        idx = src.index("Several search/read hops")
        hint_region = src[idx : idx + 500]
        assert "compress=True" in hint_region
        assert "'.'" in hint_region

    def test_opening_move_mentions_compress(self, monkeypatch):
        status = SimpleNamespace(
            index_present=True,
            loadable=True,
            as_fields=lambda: {},
            source_root="/x",
            display_name="x",
            symbol_count=1,
            file_count=1,
            languages={},
            indexed_at="now",
        )
        store = SimpleNamespace(inspect_index=lambda o, n: status)
        monkeypatch.setattr(rr, "_read_repo_metadata", lambda s, o, n: {})
        out = rr._build_indexed_response(store, "local/x", None, 0.0, "test")
        assert "compress=True" in out["_meta"]["opening_move"]
