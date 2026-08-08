"""Unit tests for MUNCH format primitives."""

from jcodemunch_mcp.encoding.format import (
    Legends,
    assemble,
    parse_header,
    parse_scalars,
    read_table,
    split_sections,
    write_header,
    write_scalars,
    write_table,
)


def test_header_round_trip():
    h = write_header("get_call_hierarchy", "ch1")
    meta = parse_header(h)
    assert meta == {"tool": "get_call_hierarchy", "enc": "ch1"}


def test_scalars_quoting():
    pairs = {"repo": "foo", "note": "hello world", "n": 42, "flag": True, "none": None}
    line = write_scalars(pairs)
    parsed = parse_scalars(line)
    assert parsed["repo"] == "foo"
    assert parsed["note"] == "hello world"
    assert parsed["n"] == "42"
    assert parsed["flag"] == "T"
    assert parsed["none"] == ""


def test_scalars_embedded_quotes():
    pairs = {"msg": 'she said "hi"'}
    line = write_scalars(pairs)
    parsed = parse_scalars(line)
    assert parsed["msg"] == 'she said "hi"'


def test_table_round_trip():
    rows = [["a.py", 12, "call"], ["b.py", 44, "ref"]]
    text = write_table("c", rows)
    parsed = read_table(text, "c")
    assert parsed == [["a.py", "12", "call"], ["b.py", "44", "ref"]]


def test_legends_dedup_and_encode():
    leg = Legends(prefix="@")
    for v in ["src/foo/", "src/foo/", "src/foo/", "src/bar/", "x"]:
        leg.observe(v)
    leg.finalize(min_uses=2, min_chars_saved=1)
    assert leg.encode_prefix("src/foo/thing.py").startswith("@")
    out = leg.write()
    leg2 = Legends.read(out, prefix="@")
    encoded = leg.encode_prefix("src/foo/a.py")
    decoded = leg2.decode_prefix(encoded)
    assert decoded == "src/foo/a.py"


def test_legend_literal_at_digit_not_corrupted():
    """A literal value that starts with the prefix + a digit (e.g. a '@2x'
    retina asset path or a '@1.2.3' version token) must not be mistaken for a
    legend handle on decode. Regression for the encode/decode asymmetry:
    encode_prefix left such literals verbatim, but decode_prefix expanded any
    '@<digits>' token, silently rewriting the value to legend[N] + suffix.
    """
    leg = Legends(prefix="@")
    # Build a populated legend so handle indices exist to collide with.
    for v in ["src/module/handlers.py", "src/module/handlers.py", "src/module/"]:
        leg.observe(v)
    leg.finalize(min_uses=2, min_chars_saved=1)
    out = leg.write()
    leg2 = Legends.read(out, prefix="@")

    # Real legend prefix still interns + round-trips.
    handle = leg.encode_prefix("src/module/handlers.py")
    assert handle.startswith("@")
    assert leg2.decode_prefix(handle) == "src/module/handlers.py"

    # Literal @-leading values survive verbatim, including the double-prefix edge.
    for literal in ["@2x_asset", "@1abc", "@1.2.3", "@10", "@@already", "@foo"]:
        enc = leg.encode_prefix(literal)
        assert leg2.decode_prefix(enc) == literal, (
            f"{literal!r} corrupted: encoded {enc!r} decoded {leg2.decode_prefix(enc)!r}"
        )


def test_assemble_and_split():
    header = write_header("demo", "gen1")
    payload = assemble(header, "@1=x/", "k=v", "c,row1")
    head, blocks = split_sections(payload)
    assert head == header
    assert blocks == ["@1=x/", "k=v", "c,row1"]


def test_blank_line_inside_a_quoted_cell_does_not_split_the_section():
    """A blank line inside an RFC 4180 cell is not a section boundary.

    Regression: write_table emits real newlines inside quoted cells, and
    split_sections cut on "\n\n" unconditionally. Because generic.decode and
    schema_driven.decode read tables per block, the row count still matched while
    the cell silently lost everything after the blank line. A Python constant or
    docstring containing an empty line is enough to trigger it.
    """
    rows = [
        ["sym_a", "def f():\n\n    return 1"],
        ["sym_b", "ok"],
    ]
    payload = assemble(write_header("get_file_outline", "fo1"), write_table("s", rows))
    _head, blocks = split_sections(payload)

    assert len(blocks) == 1, f"quoted cell was split across {len(blocks)} blocks"

    # Decode the way real callers do: per block, not on a rejoined payload.
    decoded = []
    for block in blocks:
        decoded.extend(read_table(block, "s"))
    assert decoded == rows


def test_multiple_blank_lines_inside_one_cell_survive():
    rows = [["sym", "a\n\nb\n\nc"], ["next", "z"]]
    payload = assemble(write_header("t", "e"), write_table("s", rows))
    _head, blocks = split_sections(payload)
    decoded = []
    for block in blocks:
        decoded.extend(read_table(block, "s"))
    assert decoded == rows


def test_genuine_section_boundaries_still_split():
    """The repair must not merge real sections that happen to sit near quotes."""
    payload = assemble(
        write_header("t", "e"),
        "@1=x/",
        'k="quoted value"',
        write_table("s", [["a", "plain"], ["b", 'has "quotes" inside']]),
    )
    _head, blocks = split_sections(payload)
    assert len(blocks) == 3, blocks
    assert blocks[0] == "@1=x/"
    assert read_table(blocks[2], "s") == [["a", "plain"], ["b", 'has "quotes" inside']]


def test_unterminated_quote_degrades_without_dropping_text():
    payload = write_header("t", "e") + "\n\n" + 's,"never closed\n\nmore text'
    _head, blocks = split_sections(payload)
    assert any("more text" in b for b in blocks)


# ---------------------------------------------------------------------------
# Further edges on the same repair. The four cases above pin the reported
# defect; these pin the shapes a future encoder is most likely to produce
# once cells widen, which is the condition that makes this reachable at all.
# ---------------------------------------------------------------------------

def _roundtrip(rows, scalars=None):
    """Encode rows through a real payload and read them back."""
    sections = ([write_scalars(scalars)] if scalars else []) + [write_table("s", rows)]
    _head, blocks = split_sections(assemble(write_header("t", "e"), *sections))
    return [r for b in blocks for r in read_table(b, "s")]


def test_blank_line_at_the_very_end_of_a_cell():
    """Trailing blank line puts the open quote adjacent to the real boundary."""
    assert _roundtrip([["a", "x\n\n"]])[0][1] == "x\n\n"


def test_cell_that_is_only_blank_lines():
    assert _roundtrip([["a", "\n\n"]])[0][1] == "\n\n"


def test_embedded_quotes_and_a_blank_line_in_one_cell():
    """Doubled quotes are the escape form and must cancel in the parity count;
    if they did not, this cell would read as permanently unterminated."""
    val = 'say "hi"\n\nbye'
    assert _roundtrip([["a", val]])[0][1] == val


def test_two_separate_cells_each_carrying_a_blank_line():
    """Parity must re-open per boundary, not latch after the first repair."""
    rows = [["a", "p\n\nq"], ["b", "r\n\ns"]]
    assert [r[1] for r in _roundtrip(rows)] == ["p\n\nq", "r\n\ns"]


def test_a_scalars_block_before_the_table_is_unaffected():
    """Scalars escape their newlines (audit finding F1), so they contribute no
    open quote — the repair must not be confused by a quoted scalar value."""
    assert _roundtrip([["a", "p\n\nq"]], {"k": 'v "quoted" v'})[0][1] == "p\n\nq"


def test_crlf_blank_line_inside_a_cell():
    """A Windows-authored source file supplies \r\n\r\n, not \n\n."""
    val = "x\r\n\r\ny"
    assert _roundtrip([["a", val]])[0][1] == val
