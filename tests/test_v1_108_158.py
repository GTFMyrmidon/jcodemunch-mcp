"""v1.108.158: turn-economy steering (advisory, one nudge per session).

Origin: the 2026-07-22 benchmark-harness runs — exploration sessions hopped
search -> outline -> source 2-3x more than a raw baseline and never touched
the one-call openers (get_ranked_context / assemble_task_context). Steering
is advisory only: front-door description line, resolve_repo opening_move,
and a once-per-session escalation hint after N hop calls with no bundle call.
"""

from types import SimpleNamespace

import pytest

from jcodemunch_mcp import server
from jcodemunch_mcp.tools import resolve_repo as rr


@pytest.fixture(autouse=True)
def _reset_steer_state():
    saved = dict(server._steer_state)
    server._steer_state.update({"hops": 0, "bundles": 0, "nudged": False, "repos": []})
    yield
    server._steer_state.update(saved)


class TestSteerCounters:
    def test_hop_tools_count(self):
        for tool in ("search_symbols", "search_text", "get_file_outline", "get_symbol_source"):
            server._steer_note_call(tool, {}, {})
        assert server._steer_state["hops"] == 4
        assert server._steer_state["bundles"] == 0

    def test_bundle_tools_count(self):
        server._steer_note_call("get_ranked_context", {}, {})
        server._steer_note_call("assemble_task_context", {}, {})
        assert server._steer_state["bundles"] == 2
        assert server._steer_state["hops"] == 0

    def test_repo_recorded_from_args_and_resolve(self):
        server._steer_note_call("search_symbols", {"repo": "local/a"}, {})
        server._steer_note_call("resolve_repo", {"path": "."}, {"repo": "local/b", "found": True})
        assert server._steer_state["repos"] == ["local/a", "local/b"]

    def test_repo_list_deduped_and_capped(self):
        for i in range(8):
            server._steer_note_call("search_symbols", {"repo": f"local/r{i}"}, {})
        server._steer_note_call("search_symbols", {"repo": "local/r0"}, {})
        assert len(server._steer_state["repos"]) == 5


class TestSteerHintGate:
    def test_due_after_threshold_on_search(self):
        server._steer_state["hops"] = 3
        assert server._steer_hint_due("search_symbols", {"results": []})
        assert server._steer_hint_due("search_text", {"results": []})

    def test_not_due_below_threshold(self):
        server._steer_state["hops"] = 2
        assert not server._steer_hint_due("search_symbols", {"results": []})

    def test_not_due_when_bundle_used(self):
        server._steer_state["hops"] = 5
        server._steer_state["bundles"] = 1
        assert not server._steer_hint_due("search_symbols", {"results": []})

    def test_not_due_twice(self):
        server._steer_state["hops"] = 5
        server._steer_state["nudged"] = True
        assert not server._steer_hint_due("search_symbols", {"results": []})

    def test_not_due_on_non_search_or_error(self):
        server._steer_state["hops"] = 5
        assert not server._steer_hint_due("get_symbol_source", {"symbols": []})
        assert not server._steer_hint_due("search_symbols", {"error": "boom"})


class TestSteeringSurfaces:
    def test_order_description_names_ranked_context(self):
        tools = {t.name: t for t in server._counter_front_door_tools()}
        assert "get_ranked_context" in tools["order"].description

    def test_resolve_repo_opening_move_when_loadable(self, monkeypatch):
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
        assert "get_ranked_context" in out["_meta"]["opening_move"]

    def test_resolve_repo_no_hint_when_not_loadable(self, monkeypatch):
        status = SimpleNamespace(
            index_present=True,
            loadable=False,
            as_fields=lambda: {},
            source_root=None,
            display_name=None,
            symbol_count=0,
            file_count=0,
            languages={},
            indexed_at=None,
        )
        store = SimpleNamespace(inspect_index=lambda o, n: status)
        monkeypatch.setattr(rr, "_read_repo_metadata", lambda s, o, n: {})
        out = rr._build_indexed_response(store, "local/x", None, 0.0, "test")
        assert "opening_move" not in out["_meta"]
