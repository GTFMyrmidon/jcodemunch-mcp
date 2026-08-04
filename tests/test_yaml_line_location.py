"""A YAML symbol must be located at its own key's line, or refuse.

Three compounding defects, measured 2026-08-04 against
`.github/workflows/health-radar-comment.yml`, where a five-key step block
resolved to lines 31 / 32 / 33 / 34 / 35 and only the last was right:

1. **Substring matching.** `needle in line` let `id:` answer for `run-id:`.
2. **A cursor starting past the item's own key.** A list item is identified by
   its `name:` line and the walk then began on the following line, so the
   item's own `name` bound to a nested `name:` further down.
3. **A silent fabricated fallback.** Not-found returned `after + 1`, which is
   simply the next line presented as a located match. Two of the five keys were
   fabricated that way.

The fix reads true line numbers from YAML node marks and falls back to an
anchored search. Independently checked (without node marks, by asserting the
located line actually begins with that key): 0 of 4773 symbols land on a wrong
line, against roughly a quarter before.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.parser.extractor import (
    KEY_NOT_FOUND,
    _find_key_line,
    _yaml_line_map,
    parse_file,
)

STEP_BLOCK = """name: Health Radar Comment

on:
  workflow_run:
    workflows: [health-radar]

jobs:
  comment:
    runs-on: ubuntu-latest
    steps:
      - name: Download comment payload
        id: download
        uses: actions/download-artifact@d3f86a
        with:
          name: health-radar-payload
          run-id: 12345
          path: payload
        continue-on-error: true
"""

# Same key name at two depths: the shape that defeated a cursor-based walk.
COMPOSE = """services:
  app:
    image: demo
    volumes:
      - code-index:/data

volumes:
  code-index:
  caddy-data:
"""


def _syms(text: str, name: str = "f.yml"):
    out = parse_file(text, name, "yaml")
    if not out:
        pytest.skip("YAML parsing unavailable in this environment")
    return {s.qualified_name: s for s in out}


# --- the locator ----------------------------------------------------------


def test_key_match_is_anchored_not_substring():
    """`id` must not answer for `run-id:`."""
    lines = ["steps:\n", "  run-id: 12345\n", "  id: download\n"]
    assert _find_key_line(lines, "id", 0) == 3


def test_missing_key_refuses_instead_of_fabricating():
    """The old locator returned `after + 1`: the next line, dressed as a match."""
    lines = ["a: 1\n", "b: 2\n"]
    assert _find_key_line(lines, "nope", 0) == KEY_NOT_FOUND


def test_quoted_keys_are_matched():
    lines = ["'on':\n", '"off":\n']
    assert _find_key_line(lines, "on", 0) == 1
    assert _find_key_line(lines, "off", 0) == 2


def test_list_marker_prefixed_keys_are_matched():
    lines = ["steps:\n", "  - name: build\n"]
    assert _find_key_line(lines, "name", 0) == 2


# --- the node-mark map ----------------------------------------------------


def test_line_map_is_actually_populated():
    """⚠ The regression that hid the whole fix.

    The first version referenced `yaml.compose`, but this module imports yaml
    only as a local `_yaml`. That raised NameError, a broad `except Exception`
    turned it into an empty map, and the node-mark path silently never ran
    while its measurements still looked healthy. An empty map here means the
    fix is inert.
    """
    mapping = _yaml_line_map(COMPOSE)
    assert mapping, "line map is empty: the node-mark path is not running"
    assert mapping["services"] == 1
    assert mapping["volumes"] == 7


def test_line_map_distinguishes_same_name_at_different_depths():
    mapping = _yaml_line_map(COMPOSE)
    assert mapping["services.app.volumes"] == 4
    assert mapping["volumes"] == 7


def test_line_map_survives_undecodable_yaml_without_raising():
    assert _yaml_line_map("a: [unclosed\n") == {}


# --- end to end -----------------------------------------------------------


def test_step_keys_land_on_their_own_lines():
    """The original failing block. Every one of these was wrong before."""
    syms = _syms(STEP_BLOCK)
    step = "jobs.comment.steps.Download comment payload"
    assert syms[f"{step}.uses"].line == 13
    assert syms[f"{step}.with"].line == 14
    assert syms[f"{step}.id"].line == 12
    assert syms[f"{step}.continue-on-error"].line == 18


def test_nested_key_does_not_steal_its_parents_name():
    """A step's own `name` must not bind to its `with.name`."""
    syms = _syms(STEP_BLOCK)
    step = "jobs.comment.steps.Download comment payload"
    assert syms[f"{step}.name"].line == 11
    assert syms[f"{step}.with.name"].line == 15


def test_run_id_does_not_answer_for_id():
    syms = _syms(STEP_BLOCK)
    step = "jobs.comment.steps.Download comment payload"
    assert syms[f"{step}.id"].line == 12
    assert syms[f"{step}.with.run-id"].line == 16


def test_top_level_key_is_not_captured_by_a_nested_namesake():
    syms = _syms(COMPOSE)
    assert syms["volumes"].line == 7
    assert syms["services.app.volumes"].line == 4


def test_every_located_symbol_sits_on_a_line_holding_its_key():
    """The invariant, checked WITHOUT node marks so it is independent."""
    import re

    lines = STEP_BLOCK.splitlines()
    for sym in _syms(STEP_BLOCK).values():
        if sym.line == 0 or sym.name.startswith("["):
            continue
        text = lines[sym.line - 1]
        assert re.match(
            r"^\s*(?:-\s+)?(?:['\"])?" + re.escape(sym.name) + r"(?:['\"])?\s*:",
            text,
            re.IGNORECASE,
        ), f"{sym.qualified_name} at line {sym.line} is {text!r}"


def test_unlocatable_key_refuses_instead_of_fabricating():
    """A key that is not in the source yields no line, rather than a guess.

    ⚠ This used to be reachable through real YAML: `on:` was coerced to a
    boolean, so its key became `True`, which appears nowhere in the source. That
    coercion is fixed, so the path no longer has a natural trigger and is
    exercised directly here. Deleting the test with the bug would have removed
    the guard against the *next* unlocatable key.
    """
    assert _find_key_line(["a: 1\n", "b: 2\n"], "absent", 0) == KEY_NOT_FOUND


def test_an_unlocated_symbol_never_carries_bytes():
    """The invariant that makes a refusal safe: no line means no evidence."""
    from jcodemunch_mcp.parser.extractor import _byte_span

    offsets = [0, 5, 11]
    _, length = _byte_span(offsets, KEY_NOT_FOUND)
    assert length == 0


def test_workflow_trigger_is_locatable_now():
    """The former refusal case, as a regression guard in the other direction."""
    syms = _syms(STEP_BLOCK)
    assert "on" in syms, f"`on` missing; got {sorted(syms)[:8]}"
    assert syms["on"].line == 3
    assert syms["on"].byte_length > 0
    assert not [s for s in syms.values() if s.line == 0], "unexpected refusal"
