"""The catalog moratorium — a convention needs a test, not a habit.

The 2026-08-02 deep-research report's blunt finding: *"the product has crossed
the point where adding a tool automatically adds value."* 91 catalog actions,
route@1 under 50%, and issue #397 where generated guidance named 25 tools while
the server exposed 6. An action `route` never proposes is functionally absent,
so catalog growth past the front door's reach adds schema, documentation,
compatibility surface and test matrix while adding no reachable capability.

A moratorium written only in CONTRIBUTING.md is a habit. This file is the
policy: the ceiling is pinned, and raising it is an explicit edit that shows in
a diff and needs a reviewer to agree. That is the whole mechanism — not to make
a new action impossible, but to make it deliberate.

⚠ **The exit bar is named HERE, before the work, deliberately.** Same discipline
as the #398 Arc 4 thresholds in ROADMAP.md: neither side picks the bar after
seeing results.

⚠⚠ **The leakage ceiling is not decoration — without it the exit gate rewards
corrupting the corpus.** "Raise route@1 to 60%" is trivially satisfiable by
writing queries that paraphrase tool descriptions, which is the exact failure
v1.108.218's target audit was conducted to avoid. A recall bar with no leakage
bar is an invitation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / "benchmarks" / "route_recall" / "results.json"

#: Catalog actions visible under `full`, pinned 2026-08-02 at v1.108.218.
#: Raising this is the deliberate act the moratorium exists to require.
CATALOG_CEILING = 91

#: Exit conditions. ALL must hold before CATALOG_CEILING may rise.
#:
#: The route bar is stated against the CORRECTED corpus (v1.108.218's target
#: audit). ⚠ The pre-audit baseline was 42.4 and the post-audit baseline is
#: 45.8; that 3.4-point move was a corpus correction and NOT a routing gain, so
#: progress toward this bar is measured from 45.8, never from 42.4.
EXIT_ROUTE_AT_1 = 60.0
EXIT_BASELINE_ROUTE_AT_1 = 45.8

#: Leakage may not drift upward while recall climbs. Measured mean name overlap
#: at the audit was 0.133; this allows normal corpus growth and refuses the
#: paraphrase shortcut.
EXIT_MAX_NAME_LEAKAGE = 0.15

#: Actions that exist, are tested, and are deliberately NOT exposed. Each entry
#: is a decision the moratorium made, not an oversight.
WITHHELD = {
    "investigate_deletion_safety": (
        "v1.108.214. Importable and covered by 19 tests. Exposing it would make "
        "it the 92nd action while route@1 is under 50% — it does not jump the "
        "queue merely because we wrote it."
    ),
    "explain_route": (
        "v1.108.217. A 92nd action whose job is explaining why the 91st is hard "
        "to reach would be self-refuting. Driven by the benchmark harness."
    ),
}


def _catalog():
    import sys

    sys.argv = [sys.argv[0]]  # server module inspects argv on import
    from jcodemunch_mcp.server import _catalog_names

    return _catalog_names()


class TestCeiling:
    def test_the_catalog_has_not_grown(self):
        names = _catalog()
        assert len(names) <= CATALOG_CEILING, (
            f"the catalog grew to {len(names)}, above the moratorium ceiling of "
            f"{CATALOG_CEILING}.\n\n"
            "This is not a bug in the test. Adding a top-level action is under "
            "moratorium until ALL of:\n"
            f"  1. route@1 >= {EXIT_ROUTE_AT_1}% on the corrected corpus "
            f"(baseline {EXIT_BASELINE_ROUTE_AT_1}%);\n"
            f"  2. mean name leakage <= {EXIT_MAX_NAME_LEAKAGE} at that "
            "measurement, so the bar was not met by paraphrasing descriptions;\n"
            "  3. generated guidance references only callable actions under the "
            "active surface (#397).\n\n"
            "If the new action is genuinely warranted, raise CATALOG_CEILING in "
            "this file in the same commit. The visible diff IS the policy."
        )

    def test_the_ceiling_is_not_slack(self):
        """A ceiling far above the real count enforces nothing."""
        names = _catalog()
        assert len(names) == CATALOG_CEILING, (
            f"catalog is {len(names)} but the pin says {CATALOG_CEILING}; a "
            "ceiling with headroom lets an action land without a decision"
        )


class TestWithheldActions:
    @pytest.mark.parametrize("action", sorted(WITHHELD))
    def test_a_withheld_action_is_not_exposed(self, action):
        assert action not in _catalog(), (
            f"{action} is exposed. {WITHHELD[action]}"
        )

    def test_withheld_actions_still_exist(self):
        """The moratorium withholds a SURFACE, never a capability. If these stop
        importing, the policy has quietly become deletion."""
        from jcodemunch_mcp.investigator import (  # noqa: F401
            explain_route,
            investigate_deletion_safety,
        )


class TestExitConditions:
    @pytest.fixture
    def measured(self):
        if not RESULTS.exists():
            pytest.skip("route_recall results.json not present")
        return json.loads(RESULTS.read_text(encoding="utf-8"))["summary"]

    def test_the_recall_bar_is_stated_against_the_corrected_corpus(self, measured):
        """Guards the one number most likely to be misquoted.

        v1.108.218 moved route@1 from 42.4 to 45.8 by fixing the CORPUS. If a
        future reader measures progress from 42.4 they will credit routing work
        with a correction it did not make.
        """
        assert measured["route_recall"]["@1"] >= EXIT_BASELINE_ROUTE_AT_1 - 0.1, (
            "route@1 fell below the post-audit baseline; the moratorium's "
            "progress measurement is anchored to it"
        )

    def test_moratorium_still_in_force(self, measured):
        """Fails on the day the moratorium may lift. That is the point.

        A policy that expires silently is a policy nobody notices has expired.
        When this fails, the exit conditions have been met — re-read them, decide
        deliberately, and either raise the ceiling or restate the bar.
        """
        at1 = measured["route_recall"]["@1"]
        leak = measured["leakage"]["mean_name_overlap"]
        met = at1 >= EXIT_ROUTE_AT_1 and leak <= EXIT_MAX_NAME_LEAKAGE
        assert not met, (
            f"route@1 is {at1}% (bar {EXIT_ROUTE_AT_1}%) at name leakage {leak} "
            f"(ceiling {EXIT_MAX_NAME_LEAKAGE}). The moratorium's exit conditions "
            "are MET. This failure is a prompt, not a defect: decide explicitly "
            "whether to lift it, then update this test."
        )

    def test_a_recall_bar_without_a_leakage_bar_would_be_gameable(self, measured):
        """Non-vacuous: the leakage ceiling has to be able to bind."""
        assert EXIT_MAX_NAME_LEAKAGE < 1.0
        assert measured["leakage"]["mean_name_overlap"] <= EXIT_MAX_NAME_LEAKAGE, (
            "corpus leakage already exceeds the ceiling the exit gate relies on"
        )


class TestGuidanceMatchesTheSurface:
    """Exit condition 3, measured rather than asserted (#397).

    The failure was not theoretical: generated `CLAUDE.md` named 25 tools while
    a default server exposed 6, so the policy meant to ensure jCodeMunch got
    used instead instructed the agent to call tools that were not there.
    """

    def test_counter_policy_names_only_front_door_actions(self, monkeypatch):
        import re
        import sys

        monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", "counter")
        sys.argv = [sys.argv[0]]
        from jcodemunch_mcp.cli import init as _init

        policy = _init.active_policy()
        referenced = set(_init._TOOL_REF_RE.findall(policy))

        from jcodemunch_mcp.server import _CANONICAL_TOOL_NAMES

        known = set(_CANONICAL_TOOL_NAMES)
        callable_now = {"order", "menu", "route"} | {
            "jcodemunch_guide", "announce_model", "set_tool_tier",
        }
        leaked = (referenced & known) - callable_now
        assert not leaked, (
            "the counter policy names actions the client will not offer the "
            f"model: {sorted(leaked)} (#397)"
        )

    def test_full_policy_names_only_real_actions(self, monkeypatch):
        import sys

        monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", "full")
        sys.argv = [sys.argv[0]]
        from jcodemunch_mcp.cli import init as _init
        from jcodemunch_mcp.server import _CANONICAL_TOOL_NAMES

        policy = _init.active_policy()
        referenced = set(_init._TOOL_REF_RE.findall(policy))
        known = set(_CANONICAL_TOOL_NAMES) | {"order", "menu", "route"}
        # Only judge tokens that LOOK like our actions; prose contains other
        # backticked identifiers (config keys, env vars) that are not tools.
        suspects = {r for r in referenced if r.startswith((
            "get_", "find_", "check_", "search_", "index_", "plan_", "list_",
            "assemble_", "winnow_", "render_", "suggest_", "digest",
        ))}
        assert suspects <= known, sorted(suspects - known)
