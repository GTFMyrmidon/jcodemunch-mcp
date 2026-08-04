"""YAML mapping keys keep their source text.

PyYAML implements YAML 1.1, which resolves `on` / `off` / `yes` / `no` to
booleans **as keys**. A GitHub workflow's `on:` therefore arrived as the key
`True`, and every workflow we index carried a symbol literally named `True`.

⚠ The naming was the visible half. The silent half is key LOSS: `on` and `yes`
both resolve to `True`, `off` and `no` both to `False`, so four distinct keys
collapse into two and the later one overwrites the earlier without a word.

Only keys are affected. Values keep ordinary YAML semantics.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.parser.extractor import _load_yaml_data, parse_file

WORKFLOW = """name: CI

on:
  push:
    branches: [main]

jobs:
  build:
    runs-on: ubuntu-latest
"""


def _syms(text: str, name: str = "w.yml"):
    out = parse_file(text, name, "yaml")
    if not out:
        pytest.skip("YAML parsing unavailable in this environment")
    return {s.qualified_name: s for s in out}


# --- the loader -----------------------------------------------------------


def test_boolean_looking_keys_keep_their_text():
    data = _load_yaml_data("on: 1\noff: 2\nyes: 3\nno: 4\n")
    assert list(data.keys()) == ["on", "off", "yes", "no"]


def test_colliding_keys_are_no_longer_silently_merged():
    """The silent half: four source keys used to become two.

    `on`/`yes` -> True and `off`/`no` -> False, so two keys were destroyed and
    the survivors carried the *later* value.
    """
    data = _load_yaml_data("on: 1\noff: 2\nyes: 3\nno: 4\n")
    assert len(data) == 4, f"keys were merged away: {list(data)}"
    assert data["on"] == 1 and data["yes"] == 3
    assert data["off"] == 2 and data["no"] == 4


def test_values_keep_normal_yaml_semantics():
    """Only keys are affected. A value of `true` is still a boolean."""
    data = _load_yaml_data("strict: true\ndisabled: false\ncount: 42\nratio: 1.5\n")
    assert data["strict"] is True
    assert data["disabled"] is False
    assert data["count"] == 42
    assert data["ratio"] == 1.5


def test_merge_keys_still_resolve():
    """`flatten_mapping` must still run, exactly as in the stock constructor."""
    data = _load_yaml_data("base: &b\n  a: 1\nchild:\n  <<: *b\n  b: 2\n")
    assert data["child"] == {"a": 1, "b": 2}


def test_multi_document_yaml_still_loads():
    data = _load_yaml_data("a: 1\n---\nb: 2\n")
    assert isinstance(data, list) and len(data) == 2


def test_unparseable_yaml_returns_none():
    assert _load_yaml_data("a: [unclosed\n") is None


def test_nested_keys_are_preserved_too():
    data = _load_yaml_data("jobs:\n  build:\n    on: 1\n")
    assert list(data["jobs"]["build"].keys()) == ["on"]


# --- end to end -----------------------------------------------------------


def test_workflow_trigger_is_named_on_not_True():
    syms = _syms(WORKFLOW)
    assert "on" in syms, f"no `on` symbol; got {sorted(syms)}"
    assert "True" not in syms, "the YAML 1.1 boolean coercion is back"


def test_the_on_key_is_now_locatable():
    """It used to refuse, because `True` appears nowhere in the source."""
    syms = _syms(WORKFLOW)
    on = syms["on"]
    assert on.line == 3
    assert on.byte_length > 0, "a locatable key must carry its bytes"


def test_children_of_on_are_reachable_under_the_right_path():
    syms = _syms(WORKFLOW)
    assert "on.push" in syms
    assert syms["on.push"].line == 4


def test_no_symbol_is_named_after_a_python_bool():
    syms = _syms(WORKFLOW)
    for name in ("True", "False"):
        assert name not in syms
