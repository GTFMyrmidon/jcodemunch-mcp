"""TOML `end_line` must not run into the next table.

tree-sitter-toml runs a ``table`` node to the START of the following table, so
its ``end_point`` lands at column 0 of a row it holds no text on. The plain
``end_point[0] + 1`` therefore named a line belonging to a different symbol.

Found while measuring something else against ``fastapi`` at ``a64dfbbd``:
``[build-system]`` occupies lines 1-3 with a blank at 4, and reported
``end_line=5``, which is the line holding ``[project]``. ``byte_length`` was
correct throughout, so the index disagreed with itself and only the line number
was wrong.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.parser.extractor import parse_file

# The real shape from fastapi/pyproject.toml, trimmed. A blank line between
# tables is what puts the node boundary on its own row.
PYPROJECT = """[build-system]
requires = ["pdm-backend"]
build-backend = "pdm.backend"

[project]
name = "fastapi"

[tool.mypy]
plugins = ["pydantic.mypy"]
strict = true

[[tool.mypy.overrides]]
module = "fastapi.concurrency"
"""


def _symbols(text: str, name: str = "pyproject.toml"):
    syms = parse_file(text, name, "toml")
    if not syms:
        pytest.skip("TOML grammar unavailable in this environment")
    return {s.qualified_name: s for s in syms}


def test_table_end_line_stops_before_the_next_table():
    by_name = _symbols(PYPROJECT)

    build = by_name["build-system"]
    assert build.line == 1
    # Line 5 is `[project]`. Naming it here would attribute another symbol's
    # header to this one.
    assert build.end_line == 4, f"end_line ran into the next table: {build.end_line}"

    mypy = by_name["tool.mypy"]
    assert mypy.line == 8
    assert mypy.end_line == 11


def test_table_array_end_line_is_bounded():
    by_name = _symbols(PYPROJECT)
    overrides = by_name["tool.mypy.overrides"]
    assert overrides.line == 12
    assert overrides.end_line == 13


def test_end_line_never_names_a_line_owned_by_another_table():
    """The general invariant, not just the two cases above."""
    by_name = _symbols(PYPROJECT)
    starts = {s.line for s in by_name.values() if s.kind in ("type", "class")}
    for sym in by_name.values():
        if sym.kind not in ("type", "class"):
            continue
        for line in range(sym.line + 1, sym.end_line + 1):
            assert line not in starts, (
                f"{sym.qualified_name} spans line {line}, which starts another table"
            )


def test_line_span_agrees_with_byte_length():
    """The two must not disagree.

    ``byte_length`` was already right when ``end_line`` was wrong, which is how
    the defect was caught. Pinning the agreement is what stops it recurring in
    either field.
    """
    by_name = _symbols(PYPROJECT)
    raw = PYPROJECT.encode("utf-8")
    for sym in by_name.values():
        body = raw[sym.byte_offset : sym.byte_offset + sym.byte_length].decode("utf-8")
        rows = body.count("\n") + (0 if body.endswith("\n") else 1)
        expected = sym.end_line - sym.line + 1
        assert rows == expected, (
            f"{sym.qualified_name}: body covers {rows} rows, "
            f"line span claims {expected}"
        )


def test_single_line_table_keeps_its_line():
    """The guard must not collapse a node that starts and ends on one row."""
    by_name = _symbols('[a]\nx = 1\n[b]\ny = 2\n')
    assert by_name["a"].line == 1
    assert by_name["a"].end_line == 2
    assert by_name["b"].line == 3
    assert by_name["b"].end_line == 4


def test_pair_end_line_unaffected():
    """Pairs end mid-line, so the guard must be a no-op for them."""
    by_name = _symbols(PYPROJECT)
    pair = by_name["build-system.requires"]
    assert pair.line == pair.end_line == 2


def test_last_table_in_file_is_not_shortened():
    """A file ending without a trailing blank still bounds its final table."""
    by_name = _symbols("[only]\nx = 1\ny = 2")
    assert by_name["only"].line == 1
    assert by_name["only"].end_line == 3
