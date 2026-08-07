"""An unrecognized tool_surface is reported as what is actually in force (#424 follow-up).

`JCODEMUNCH_TOOL_SURFACE=countr` used to report itself back verbatim while
serving the full 91-tool surface, because only "counter" is ever special-cased.
Someone debugging "why didn't my token cost drop" then read a receipt that
CONFIRMED their typo.

⚠ Fifth occurrence of the diagnostic-disagrees-with-the-runtime class (v1.108.250
and v1.108.255 were the two most recent). The rule that keeps being relearned: a
diagnostic must resolve its value the same way the runtime does, or it is worse
than no diagnostic, because it is believed.

⚠ Resolving quietly to "full" would not be enough on its own. That hides the typo
in the other direction, so the receipt names the rejected value instead of
silently normalising it away.

Behaviour is deliberately UNCHANGED: an unrecognized value has always served the
full surface. Only the reported value moves, which is the entire defect.
"""

import pytest

from jcodemunch_mcp import server as S


@pytest.fixture(autouse=True)
def _clean_surface_env(monkeypatch):
    monkeypatch.delenv("JCODEMUNCH_TOOL_SURFACE", raising=False)
    S._UNRECOGNIZED_SURFACES_LOGGED.clear()
    yield
    S._UNRECOGNIZED_SURFACES_LOGGED.clear()


class TestResolution:
    @pytest.mark.parametrize("value", ["counter", "full"])
    def test_valid_values_pass_through(self, value, monkeypatch):
        monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", value)
        assert S._surface_resolution() == (value, value, True)

    @pytest.mark.parametrize("value", ["countr", "Counter2", "cnter", "true", "1", "off"])
    def test_unrecognized_values_resolve_to_full(self, value, monkeypatch):
        monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", value)
        effective, requested, recognized = S._surface_resolution()
        assert effective == "full"
        assert requested == value.strip().lower()
        assert recognized is False

    def test_case_and_whitespace_are_normalized(self, monkeypatch):
        monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", "  COUNTER ")
        assert S._surface_resolution() == ("counter", "counter", True)

    def test_unset_defaults_to_full(self):
        assert S._surface_resolution() == ("full", "full", True)

    def test_effective_surface_is_always_a_valid_value(self, monkeypatch):
        """The contract every caller relies on. Three call sites branch on this."""
        for value in ["counter", "full", "countr", "", "  ", "COUNTER", "nonsense"]:
            monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", value)
            assert S._effective_surface() in S.VALID_TOOL_SURFACES, value

    def test_config_values_are_validated_too(self, monkeypatch):
        """Env is not the only way in; the config key shares the hazard."""
        monkeypatch.delenv("JCODEMUNCH_TOOL_SURFACE", raising=False)
        monkeypatch.setattr(
            S.config_module, "get",
            lambda key, default=None, **kw: "countr" if key == "tool_surface" else default,
        )
        assert S._surface_resolution() == ("full", "countr", False)

    def test_env_still_wins_over_config(self, monkeypatch):
        monkeypatch.setattr(
            S.config_module, "get",
            lambda key, default=None, **kw: "counter" if key == "tool_surface" else default,
        )
        monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", "full")
        assert S._effective_surface() == "full"


class TestTheReportedValueMatchesTheRuntime:
    """The defect itself. The receipt and the served surface must agree."""

    def test_a_typo_serves_the_full_surface(self, monkeypatch):
        monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", "countr")
        typo_count = len(S._build_tools_list())
        monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", "full")
        full_count = len(S._build_tools_list())
        assert typo_count == full_count, "behaviour changed; only the report should move"

    def test_counter_and_full_actually_differ(self, monkeypatch):
        """Non-vacuity floor: if these ever matched, every assertion in this
        class would pass without measuring anything."""
        monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", "counter")
        counter_count = len(S._build_tools_list())
        monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", "full")
        full_count = len(S._build_tools_list())
        assert counter_count < full_count, "the two surfaces are indistinguishable"

    def test_the_receipt_no_longer_echoes_the_typo(self, monkeypatch):
        monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", "countr")
        receipt = S._tool_surface_stats()
        assert receipt["surface"] == "full", (
            "the receipt reported a surface that is not in force"
        )
        assert receipt["surface"] != "countr"

    def test_the_receipt_names_the_rejected_value(self, monkeypatch):
        """Normalising silently would hide the typo in the other direction."""
        monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", "countr")
        receipt = S._tool_surface_stats()
        assert receipt["surface_requested"] == "countr"
        assert receipt["surface_unrecognized"] is True
        assert "not recognized" in receipt["surface_note"]
        assert "counter" in receipt["surface_note"], "the note must list valid values"

    @pytest.mark.parametrize("value", ["counter", "full"])
    def test_a_clean_setting_pays_nothing(self, value, monkeypatch):
        """Omit-when-empty: a correct config must not carry warning fields."""
        monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", value)
        receipt = S._tool_surface_stats()
        assert "surface_requested" not in receipt
        assert "surface_unrecognized" not in receipt
        assert "surface_note" not in receipt

    def test_the_receipt_still_carries_its_original_fields(self, monkeypatch):
        """1.x contract: additive only, no key may disappear."""
        monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", "countr")
        receipt = S._tool_surface_stats()
        for key in (
            "surface", "profile", "visible_tools", "catalog_tools",
            "schema_tokens_visible", "schema_tokens_catalog",
            "schema_tokens_avoided", "heaviest_tools", "estimator",
        ):
            assert key in receipt, key


class TestWarning:
    def test_an_unrecognized_value_warns_once(self, monkeypatch, caplog):
        monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", "countr")
        with caplog.at_level("WARNING", logger=S.logger.name):
            for _ in range(5):
                S._effective_surface()
        hits = [r for r in caplog.records if "Unrecognized tool_surface" in r.message]
        assert len(hits) == 1, f"warned {len(hits)} times; a per-call warning is noise"

    def test_the_warning_names_the_value_and_the_valid_set(self, monkeypatch, caplog):
        monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", "countr")
        with caplog.at_level("WARNING", logger=S.logger.name):
            S._effective_surface()
        msg = " ".join(r.getMessage() for r in caplog.records)
        assert "countr" in msg
        assert "counter" in msg and "full" in msg
        assert "JCODEMUNCH_TOOL_SURFACE" in msg, "name the knob that sets it"

    def test_a_valid_value_is_silent(self, monkeypatch, caplog):
        monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", "counter")
        with caplog.at_level("WARNING", logger=S.logger.name):
            S._effective_surface()
        assert not [r for r in caplog.records if "Unrecognized tool_surface" in r.message]

    def test_distinct_bad_values_each_warn(self, monkeypatch, caplog):
        with caplog.at_level("WARNING", logger=S.logger.name):
            for value in ("countr", "cntr"):
                monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", value)
                S._effective_surface()
        hits = [r for r in caplog.records if "Unrecognized tool_surface" in r.message]
        assert len(hits) == 2


class TestNoEgress:
    """Verified while answering #424 and pinned here so it stays true.

    The savings telemetry payload is three fields. The surface value is a LOCAL
    receipt only. Whether it should ever be sent is #424 and is the maintainer's
    call; this test exists so that decision stays a decision instead of drifting
    in by accident.
    """

    def test_the_telemetry_payload_carries_no_surface_field(self):
        import inspect
        from jcodemunch_mcp.storage import token_tracker
        src = inspect.getsource(token_tracker)
        idx = src.find('json={"delta"')
        assert idx != -1, "telemetry payload shape changed; re-read this test"
        payload_line = src[idx:idx + 200]
        assert "surface" not in payload_line, (
            "a surface dimension reached the telemetry payload; that is #424 and "
            "it is the maintainer's decision, not a refactor's side effect"
        )
