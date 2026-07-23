"""v1.108.156: Counter order() arg-name normalization.

Agents dispatching through order(action, args) work without resident schemas
(that's the Counter's design), so they guess arg names — order(action=
"get_file_outline", args={"path": ...}) — and the downstream tool raised an
internal ValueError instead of mapping or hinting. Measured cost: one wasted
error-turn per session in the 2026-07-22 codebench arm run (every rep).
"""

import json

import pytest

from jcodemunch_mcp import server


def _props(action):
    return server._order_action_properties(action)


class TestOrderActionProperties:
    def test_known_action_has_properties(self):
        props = _props("get_file_outline")
        assert "file_path" in props
        assert "repo" in props

    def test_unknown_action_empty(self):
        assert _props("no_such_action_xyz") == {}


class TestNormalizeOrderArgs:
    def test_path_maps_to_file_path(self):
        # The exact failure from the bench run: get_file_outline given 'path'.
        out = server._normalize_order_args(
            "get_file_outline", {"repo": "r", "path": "src/a.py"}
        )
        assert out == {"repo": "r", "file_path": "src/a.py"}

    def test_declared_keys_untouched(self):
        args = {"repo": "r", "file_path": "src/a.py"}
        assert server._normalize_order_args("get_file_outline", args) == args

    def test_alias_not_applied_when_target_present(self):
        # 'path' must NOT clobber an explicitly-given file_path.
        args = {"repo": "r", "file_path": "src/a.py", "path": "src/b.py"}
        out = server._normalize_order_args("get_file_outline", args)
        assert out["file_path"] == "src/a.py"
        assert out["path"] == "src/b.py"  # passes through untouched

    def test_unmappable_key_passes_through(self):
        args = {"repo": "r", "query": "q", "bogus_key": 1}
        out = server._normalize_order_args("search_symbols", args)
        assert out["bogus_key"] == 1

    def test_symbol_maps_to_symbol_id(self):
        out = server._normalize_order_args(
            "get_symbol_source", {"repo": "r", "symbol": "f.py::g#function"}
        )
        assert out == {"repo": "r", "symbol_id": "f.py::g#function"}

    def test_scalar_coerced_to_list_for_array_prop(self):
        # 'ids' alias -> symbol_ids (array): scalar value gets wrapped.
        out = server._normalize_order_args(
            "get_symbol_source", {"repo": "r", "ids": "f.py::g#function"}
        )
        assert out == {"repo": "r", "symbol_ids": ["f.py::g#function"]}

    def test_single_item_list_unwrapped_for_string_prop(self):
        out = server._normalize_order_args(
            "get_file_outline", {"repo": "r", "path": ["src/a.py"]}
        )
        assert out == {"repo": "r", "file_path": "src/a.py"}

    def test_unknown_action_returns_args_unchanged(self):
        args = {"anything": 1}
        assert server._normalize_order_args("no_such_action_xyz", args) is args

    def test_multi_item_list_on_singular_moves_to_plural(self):
        # v1.108.157: order("get_symbol_source", {"symbol_id": [a, b]}) raised
        # TypeError unhashable-list downstream (observed every gin rep in the
        # post-fix bench run). A multi-item list on a singular string prop with
        # an array plural sibling belongs on the plural.
        out = server._normalize_order_args(
            "get_symbol_source",
            {"repo": "r", "symbol_id": ["a.py::f#function", "b.py::g#function"]},
        )
        assert out == {
            "repo": "r",
            "symbol_ids": ["a.py::f#function", "b.py::g#function"],
        }

    def test_multi_item_list_not_moved_when_plural_provided(self):
        args = {
            "repo": "r",
            "symbol_id": ["a.py::f#function", "b.py::g#function"],
            "symbol_ids": ["c.py::h#function"],
        }
        out = server._normalize_order_args("get_symbol_source", args)
        assert out["symbol_ids"] == ["c.py::h#function"]
        assert out["symbol_id"] == ["a.py::f#function", "b.py::g#function"]


class TestHandleOrderNormalizes:
    @pytest.mark.asyncio
    async def test_order_dispatches_normalized_args(self, monkeypatch):
        seen = {}

        async def fake_call_tool(name, arguments):
            seen["name"] = name
            seen["arguments"] = arguments
            from mcp.types import TextContent

            return [TextContent(type="text", text=json.dumps({"ok": True}))]

        monkeypatch.setattr(server, "call_tool", fake_call_tool)
        await server._handle_order(
            {"action": "get_file_outline", "args": {"repo": "r", "path": "src/a.py"}}
        )
        assert seen["name"] == "get_file_outline"
        assert seen["arguments"] == {"repo": "r", "file_path": "src/a.py"}
