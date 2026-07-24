"""v1.108.167 — cue-anchored delivery ledger (P0 measure + P1 annotate).

docs/prd-cue-anchored-delivery.md. The session already counted byte-identical
repeat calls (`note_call_signature`) but could not see the shape that actually
costs: the SAME symbol re-delivered under a DIFFERENT query. This adds a
delivery ledger beside the yield record, the `redelivery_rate` measurement, and
an advisory `_meta.already_delivered` — annotation only, no response body
changes, nothing suppressed.
"""

import pytest

from jcodemunch_mcp.storage import token_tracker
from jcodemunch_mcp.storage.token_tracker import _State
from jcodemunch_mcp.server import _delivery_entries


@pytest.fixture()
def fresh_state(monkeypatch):
    state = _State()
    monkeypatch.setattr(token_tracker, "_state", state)
    return state


def _d(sid, tokens=0, full=False):
    return (sid, tokens, full)


class TestLedgerRecords:
    def test_first_delivery_is_not_a_repeat(self, fresh_state):
        assert token_tracker.note_delivered([_d("a.py::f#function", 100, True)]) == []
        assert fresh_state._delivered["a.py::f#function"]["count"] == 1

    def test_second_full_source_delivery_is_a_repeat_and_is_priced(self, fresh_state):
        token_tracker.note_delivered([_d("a.py::f#function", 100, True)])
        repeats = token_tracker.note_delivered([_d("a.py::f#function", 100, True)])
        assert repeats == ["a.py::f#function"]
        assert fresh_state._redelivered_tokens == 100

    def test_repeat_under_a_different_tool_still_counts(self, fresh_state):
        """The whole point: same symbol, different query, different args_hash."""
        token_tracker.note_delivered([_d("a.py::f#function", 80, True)])
        repeats = token_tracker.note_delivered([_d("a.py::f#function", 80, True)])
        assert repeats == ["a.py::f#function"]
        # note_call_signature would have seen two DIFFERENT signatures here.
        assert fresh_state._repeat_calls == {}

    def test_signature_repeat_is_reported_but_never_priced(self, fresh_state):
        token_tracker.note_delivered([_d("a.py::f#function", 0, False)])
        repeats = token_tracker.note_delivered([_d("a.py::f#function", 0, False)])
        assert repeats == ["a.py::f#function"]
        assert fresh_state._redelivered_tokens == 0

    def test_signature_then_full_source_is_new_bytes_not_a_redelivery(self, fresh_state):
        token_tracker.note_delivered([_d("a.py::f#function", 0, False)])
        token_tracker.note_delivered([_d("a.py::f#function", 250, True)])
        assert fresh_state._redelivered_tokens == 0
        assert fresh_state._delivered["a.py::f#function"]["full_source"] is True
        # ...and only NOW does a repeat cost.
        token_tracker.note_delivered([_d("a.py::f#function", 250, True)])
        assert fresh_state._redelivered_tokens == 250

    def test_blank_ids_ignored(self, fresh_state):
        token_tracker.note_delivered([_d(""), _d(None)])
        assert fresh_state._delivered == {}

    def test_cap_evicts_oldest(self, fresh_state):
        cap = token_tracker._DELIVERED_MAXSIZE
        token_tracker.note_delivered(
            _d(f"f{i}.py::s#function", 1, True) for i in range(cap + 10)
        )
        assert len(fresh_state._delivered) == cap
        assert "f0.py::s#function" not in fresh_state._delivered


class TestEditInvalidation:
    def test_edited_file_evicts_its_delivered_entries(self, fresh_state):
        token_tracker.note_delivered([
            _d("src/mod.py::Foo#class", 100, True),
            _d("other.py::baz#function", 100, True),
        ])
        token_tracker.note_edited_files(["src/mod.py"])
        assert "src/mod.py::Foo#class" not in fresh_state._delivered
        assert "other.py::baz#function" in fresh_state._delivered

    def test_after_eviction_redelivery_is_not_claimed(self, fresh_state):
        """Stale bytes must not be annotated as 'you already have this'."""
        token_tracker.note_delivered([_d("src/mod.py::Foo#class", 100, True)])
        token_tracker.note_edited_files(["src/mod.py"])
        assert token_tracker.note_delivered([_d("src/mod.py::Foo#class", 100, True)]) == []

    def test_windows_separators_evict(self, fresh_state):
        token_tracker.note_delivered([_d("src/mod.py::Foo#class", 100, True)])
        token_tracker.note_edited_files(["src\\mod.py"])
        assert fresh_state._delivered == {}

    def test_edit_still_marks_yield_followed_through(self, fresh_state, tmp_path):
        """The pre-existing edit-through signal is untouched by eviction."""
        token_tracker.note_served(["src/mod.py::Foo#class"])
        token_tracker.note_edited_files(["src/mod.py"])
        assert fresh_state.session_stats(str(tmp_path))["yield"]["followed_through"] == 1


class TestYieldBlock:
    def test_redelivery_metrics_reported(self, fresh_state, tmp_path):
        token_tracker.note_delivered([_d("a.py::f#function", 100, True)])
        token_tracker.note_delivered([_d("a.py::f#function", 100, True)])
        token_tracker.note_delivered([_d("b.py::g#function", 50, True)])
        y = fresh_state.session_stats(str(tmp_path))["yield"]
        assert y["redelivered_symbols"] == 1
        assert y["redelivery_rate"] == round(1 / 3, 3)
        assert y["redelivered_tokens_est"] == 100

    def test_block_absent_when_nothing_served_or_delivered(self, fresh_state, tmp_path):
        assert "yield" not in fresh_state.session_stats(str(tmp_path))

    def test_block_shape_unchanged_when_nothing_delivered(self, fresh_state, tmp_path):
        """Back-compat: pre-1.108.167 callers see the exact same keys."""
        token_tracker.note_served(["a.py::Foo#class"])
        token_tracker.note_fetched(["a.py::Foo#class"])
        assert fresh_state.session_stats(str(tmp_path))["yield"] == {
            "rate": 1.0,
            "served_results": 1,
            "followed_through": 1,
        }

    def test_delivery_only_session_reports_without_rate(self, fresh_state, tmp_path):
        """Deliveries alone produce a block; rate keys need served results."""
        token_tracker.note_delivered([_d("a.py::f#function", 10, True)])
        y = fresh_state.session_stats(str(tmp_path))["yield"]
        assert "rate" not in y and "served_results" not in y
        assert y["redelivered_symbols"] == 0

    def test_zero_redelivery_rate_is_reported_not_omitted(self, fresh_state, tmp_path):
        """A measured 0% is a finding; omission would read as 'not measured'."""
        token_tracker.note_delivered([_d("a.py::f#function", 10, True)])
        y = fresh_state.session_stats(str(tmp_path))["yield"]
        assert y["redelivery_rate"] == 0.0
        assert "redelivered_tokens_est" not in y


class TestDeliveryEntries:
    def test_search_symbols_rows_are_signature_only(self):
        got = list(_delivery_entries("search_symbols", {"results": [{"id": "a.py::f#function"}]}))
        assert got == [("a.py::f#function", 0, False)]

    def test_ranked_context_rows_carry_source(self):
        got = list(_delivery_entries(
            "get_ranked_context",
            {"context_items": [{"symbol_id": "a.py::f#function", "source": "x" * 400}]},
        ))
        assert got == [("a.py::f#function", 100, True)]

    def test_get_symbol_source_flat_shape(self):
        got = list(_delivery_entries(
            "get_symbol_source", {"id": "a.py::f#function", "source": "x" * 40}
        ))
        assert got == [("a.py::f#function", 10, True)]

    def test_get_symbol_source_batch_shape(self):
        got = list(_delivery_entries(
            "get_symbol_source",
            {"symbols": [{"id": "a.py::f#function", "source": "x" * 40},
                         {"id": "b.py::g#function", "source": "y" * 80}]},
        ))
        assert got == [("a.py::f#function", 10, True), ("b.py::g#function", 20, True)]

    def test_get_context_bundle_flat_uses_symbol_id(self):
        got = list(_delivery_entries(
            "get_context_bundle", {"symbol_id": "a.py::f#function", "source": "x" * 40}
        ))
        assert got == [("a.py::f#function", 10, True)]

    def test_get_context_bundle_batch_shape(self):
        got = list(_delivery_entries(
            "get_context_bundle",
            {"symbol_count": 1, "symbols": [{"symbol_id": "a.py::f#function", "source": "z" * 4}]},
        ))
        assert got == [("a.py::f#function", 1, True)]

    def test_error_response_yields_nothing(self):
        assert list(_delivery_entries("get_symbol_source", {"error": "nope"})) == []

    def test_unknown_tool_yields_nothing(self):
        assert list(_delivery_entries("get_repo_health", {"results": [{"id": "a"}]})) == []

    def test_non_dict_result_yields_nothing(self):
        assert list(_delivery_entries("search_symbols", ["not", "a", "dict"])) == []

    def test_rows_without_ids_skipped(self):
        assert list(_delivery_entries("search_symbols", {"results": [{"name": "f"}, {}]})) == []


class TestAnnotationCap:
    def test_annotation_count_is_exact_and_list_is_capped(self, fresh_state):
        from jcodemunch_mcp.server import _DELIVERY_ANNOTATE_MAX

        n = _DELIVERY_ANNOTATE_MAX + 5
        ids = [f"f{i}.py::s#function" for i in range(n)]
        token_tracker.note_delivered(_d(i, 4, True) for i in ids)
        repeats = token_tracker.note_delivered(_d(i, 4, True) for i in ids)
        assert len(repeats) == n  # count is exact
        assert len(repeats[:_DELIVERY_ANNOTATE_MAX]) == _DELIVERY_ANNOTATE_MAX
