"""Reported savings has two carriers, and one of them was invisible.

`baseline_tokens = actual_tokens x multiplier` is computed PER CALL, so
`savings_tokens` rises monotonically with the number of calls. A session that
made three times the calls reports three times the savings for identical work.
The number therefore cannot distinguish "did more with less" from "made more
calls", and it is not a cost-per-task figure.

⚠ That is not a bug to fix quietly. It is a property of a deliberately
counterfactual model, and the receipt says so. What was missing is the
disclosure that the total scales with calls, and a call-count-invariant figure
to read beside it.

Motivated by arXiv 2608.01347 (Weinberger and Hozez), which measures waste in
coding agents as having two distinct carriers. Branch tournaments are
token-borne; verification loops are TOOL-borne, with the most redundant runs
costing 18x the clean-run median and making 2.5x the tool calls at no success
gain. Optimising the visible carrier while the other one moves is the trap.

⚠⚠ Nothing here touches the shared telemetry payload, which stays exactly
{"delta", "total", "anon_id"}. Call counts are LOCAL reporting only. A new
field in the shared payload would be undisclosed egress, which is the thing
that got the packages quarantined.
"""
from __future__ import annotations

from jcodemunch_mcp.cli.receipt import aggregate, render_explain


def _call(tool: str, tokens: int) -> dict:
    return {"tool": tool, "result_tokens": tokens}


class TestSavingsScalesWithCallCount:
    """Pins the property rather than asserting it away."""

    def test_doubling_identical_calls_doubles_reported_savings(self):
        one = aggregate([_call("search_symbols", 500)])
        two = aggregate([_call("search_symbols", 500), _call("search_symbols", 500)])
        assert two["totals"]["savings_tokens"] == 2 * one["totals"]["savings_tokens"]
        assert two["totals"]["calls"] == 2 * one["totals"]["calls"]

    def test_per_call_savings_is_invariant_to_call_count(self):
        """The figure that does NOT inflate, which is why it is now printed."""
        one = aggregate([_call("search_symbols", 500)])
        ten = aggregate([_call("search_symbols", 500) for _ in range(10)])

        def per_call(agg):
            return agg["totals"]["savings_tokens"] / agg["totals"]["calls"]

        assert per_call(one) == per_call(ten)

    def test_more_calls_can_beat_fewer_on_the_headline(self):
        """The concrete misreading: the worse session wins on the total.

        Five small calls report more savings than one call that delivered more
        per call. Anyone reading the headline alone picks the wrong session.
        """
        chatty = aggregate([_call("search_symbols", 200) for _ in range(5)])
        lean = aggregate([_call("search_symbols", 700)])
        assert chatty["totals"]["savings_tokens"] > lean["totals"]["savings_tokens"]
        assert chatty["totals"]["calls"] > lean["totals"]["calls"]


class TestTheLimitationIsDisclosed:
    def test_explain_says_savings_rises_with_call_count(self):
        text = render_explain()
        assert "PER CALL" in text
        assert "rises with the" in text and "number of calls" in text

    def test_explain_names_the_second_carrier(self):
        text = render_explain()
        assert "TOOL-borne" in text
        assert "2608.01347" in text, "the claim needs its source, not just an assertion"

    def test_explain_does_not_promise_cost_per_task(self):
        text = render_explain()
        assert "not a cost-per-task figure" in text


class TestPerToolCallsAreTracked:
    def test_aggregate_reports_calls_beside_tokens(self):
        agg = aggregate([
            _call("search_symbols", 300),
            _call("search_symbols", 300),
            _call("get_symbol_source", 900),
        ])
        per_tool = agg["per_tool"]
        assert per_tool["search_symbols"]["calls"] == 2
        assert per_tool["get_symbol_source"]["calls"] == 1

    def test_session_stats_exposes_tool_calls_and_per_call(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / "storage"))
        monkeypatch.setenv("JCODEMUNCH_SHARE_SAVINGS", "0")
        from jcodemunch_mcp.storage import token_tracker as tt

        state = tt._State()
        state.add(100, str(tmp_path / "storage"), tool_name="search_symbols")
        state.add(300, str(tmp_path / "storage"), tool_name="search_symbols")
        state.add(600, str(tmp_path / "storage"), tool_name="get_symbol_source")
        stats = state.session_stats(str(tmp_path / "storage"))

        assert stats["tool_calls"] == {"search_symbols": 2, "get_symbol_source": 1}
        assert stats["tool_breakdown"] == {"search_symbols": 400, "get_symbol_source": 600}
        # 1000 tokens over 3 calls.
        assert stats["savings_per_call"] == round(1000 / 3, 1)

    def test_tool_breakdown_still_maps_to_tokens(self, tmp_path, monkeypatch):
        """tool_calls is a sibling, not a replacement. Existing consumers read
        tool_breakdown as tokens and must keep getting tokens."""
        monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / "storage"))
        monkeypatch.setenv("JCODEMUNCH_SHARE_SAVINGS", "0")
        from jcodemunch_mcp.storage import token_tracker as tt

        state = tt._State()
        state.add(250, str(tmp_path / "storage"), tool_name="search_text")
        stats = state.session_stats(str(tmp_path / "storage"))
        assert stats["tool_breakdown"]["search_text"] == 250
        assert stats["tool_calls"]["search_text"] == 1


def test_shared_telemetry_payload_is_unchanged():
    """⚠⚠ The egress guard. Call counts are local reporting only.

    The disclosed payload is exactly {"delta", "total", "anon_id"}. Adding a
    field here would be undisclosed egress on a package whose PyPI quarantine
    was caused by exactly that. Asserted on source text because the sender is a
    background thread this suite must never actually fire.
    """
    from pathlib import Path

    src = Path(tt_path()).read_text(encoding="utf-8")
    start = src.index("json={")
    payload = src[start:start + 120]
    assert payload.startswith('json={"delta": delta, "total": total, "anon_id": anon_id}'), (
        f"the shared telemetry payload changed: {payload!r}. Any new field is "
        "undisclosed egress until the README says otherwise."
    )


def tt_path() -> str:
    from jcodemunch_mcp.storage import token_tracker
    return token_tracker.__file__
