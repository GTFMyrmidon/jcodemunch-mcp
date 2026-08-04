"""A virtual symbol's byte extent must describe real source, not a reconstruction.

`_append_virtual_symbol` set `byte_offset` from the file and `byte_length` from
`len(signature)`. The signature is REBUILT from parsed data, not quoted from the
source, so the pair came from two different coordinate systems and their product
was a slice of arbitrary bytes.

Measured 2026-08-04 on jCodeMunch's own index: this held for 237 of 237 virtual
symbols. In `health-radar-comment.yml` the `uses` key, whose reconstructed
signature is 74 bytes, returned 74 bytes spanning two entirely different sibling
keys. Round-tripping also inflates: `name: Health Radar Comment` is 27 bytes of
source and reconstructs to 28, because the reconstruction adds quotes.

⚠ These tests deliberately do NOT assert that `line` is correct. A separate,
unfixed defect mislocates roughly a quarter of YAML symbols (`_find_line` scans
forward from a monotonic cursor, so a nested key finds a later sibling's line).
This file pins that the extent is a REAL, WHOLE, SELF-CONSISTENT line. Which
line it is belongs to that other fix, and conflating the two would let either
regression hide inside the other's test.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.parser.extractor import parse_file

WORKFLOW = """name: Health Radar Comment

on:
  workflow_run:
    workflows: [health-radar]
    types: [completed]

jobs:
  comment:
    runs-on: ubuntu-latest
    steps:
      - name: Download comment payload
        uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093
        with:
          name: health-radar-payload
          path: payload
"""


def _parsed(text: str = WORKFLOW, name: str = "workflow.yml"):
    syms = parse_file(text, name, "yaml")
    if not syms:
        pytest.skip("YAML parsing unavailable in this environment")
    return syms, text.encode("utf-8")


def test_byte_length_is_not_the_signature_length():
    """The regression itself.

    Held for 237 of 237 symbols before the fix. Asserting `!=` on every symbol
    would be wrong (a line can coincidentally match its signature length), so
    this asserts the population is no longer uniformly locked to it.
    """
    syms, _ = _parsed()
    locked = sum(1 for s in syms if s.byte_length == len(s.signature.encode("utf-8")))
    assert locked < len(syms), (
        "every symbol's byte_length still equals len(signature); the extent is "
        "still measuring the reconstruction rather than the source"
    )


def test_extent_slices_a_real_whole_line():
    syms, raw = _parsed()
    for s in syms:
        if s.byte_length == 0:
            continue
        body = raw[s.byte_offset : s.byte_offset + s.byte_length].decode("utf-8")
        assert body.endswith("\n") or s.byte_offset + s.byte_length == len(raw), (
            f"{s.qualified_name}: extent stops mid-line: {body!r}"
        )
        assert body.count("\n") <= 1, (
            f"{s.qualified_name}: extent spans multiple lines: {body!r}"
        )


def test_extent_agrees_with_the_declared_line_span():
    """`end_line == line` for a virtual symbol, so the slice must be one row."""
    syms, raw = _parsed()
    for s in syms:
        if s.byte_length == 0:
            continue
        body = raw[s.byte_offset : s.byte_offset + s.byte_length].decode("utf-8")
        rows = body.count("\n") + (0 if body.endswith("\n") else 1)
        assert rows == s.end_line - s.line + 1, (
            f"{s.qualified_name}: slice covers {rows} rows, span claims "
            f"{s.end_line - s.line + 1}"
        )


def test_extent_starts_at_the_line_it_names():
    syms, raw = _parsed()
    source_lines = WORKFLOW.splitlines(keepends=True)
    for s in syms:
        if s.byte_length == 0:
            continue
        body = raw[s.byte_offset : s.byte_offset + s.byte_length].decode("utf-8")
        assert body == source_lines[s.line - 1], (
            f"{s.qualified_name}: extent is {body!r} but line {s.line} is "
            f"{source_lines[s.line - 1]!r}"
        )


def test_reconstruction_inflation_does_not_reach_the_extent():
    """The case that first exposed it.

    `name: Health Radar Comment` is 27 source bytes; its signature reconstructs
    to 28 because the rebuild quotes the scalar. The extent must follow the
    source, not the rebuild.
    """
    syms, raw = _parsed()
    name_sym = next(s for s in syms if s.qualified_name == "name")
    assert name_sym.byte_length == 27
    body = raw[name_sym.byte_offset : name_sym.byte_offset + name_sym.byte_length]
    assert body.decode("utf-8") == "name: Health Radar Comment\n"
    assert len(name_sym.signature.encode("utf-8")) != name_sym.byte_length


def test_content_hash_covers_the_bytes_the_extent_names():
    """Otherwise `verify` can never succeed.

    The hash used to cover the signature while the extent named the file, so
    `content_verified` came back False for every YAML symbol while the response
    verdict still read `ok`.
    """
    from jcodemunch_mcp.parser.symbols import compute_content_hash

    syms, raw = _parsed()
    for s in syms:
        if s.byte_length == 0:
            continue
        body = raw[s.byte_offset : s.byte_offset + s.byte_length]
        assert s.content_hash == compute_content_hash(body), (
            f"{s.qualified_name}: content_hash does not cover its own extent"
        )


def test_unlocatable_symbol_gets_a_zero_extent_not_a_plausible_slice():
    """Refusing beats a confident slice of somebody else's text."""
    from jcodemunch_mcp.parser.extractor import _byte_span

    offsets = [0, 10, 20]
    assert _byte_span(offsets, 1) == (0, 10)
    assert _byte_span(offsets, 2) == (10, 10)
    for out_of_range in (0, -1, 3, 99):
        _, length = _byte_span(offsets, out_of_range)
        assert length == 0, f"line {out_of_range} produced a non-empty extent"


def test_last_line_without_trailing_newline_is_bounded():
    syms, raw = _parsed("a: 1\nb: 2", "tail.yml")
    for s in syms:
        end = s.byte_offset + s.byte_length
        assert end <= len(raw), f"{s.qualified_name}: extent runs past EOF"
