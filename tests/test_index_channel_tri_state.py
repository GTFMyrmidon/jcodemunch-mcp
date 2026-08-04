"""`verdict.channels.index` must never report `fresh` for unestablished freshness.

The defect, measured against `django/django` (whose index carries an empty
`source_root`, so freshness cannot be established at all): one
`get_symbol_source` call returned `_meta.freshness = {"unknown": 1}` while
`_meta.verdict.channels.index` said `"fresh"`. Two freshness signals in the
same payload, disagreeing, and the over-claiming one sat in the verdict block
agents actually read.

Cause: three copies of the channel expression had drifted. `build_verdict`
honoured the #377 item 4 tri-state; `build_file_verdict` and
`build_symbol_verdict` still carried `"stale" if index_stale else "fresh"`, so
`unknown` and `not_tracked` both rendered as proof of currency. Fixed by
extracting `index_channel` and routing all three through it.

The load-bearing tests here are the ones asserting a NON-`fresh` value. A
`fresh` that means "we could not find out" is the exact failure the tri-state
exists to prevent.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.retrieval.verdict import (
    build_file_verdict,
    build_symbol_verdict,
    build_verdict,
    index_channel,
)


def _chan(v: dict) -> str:
    return v["channels"]["index"]


# --- the shared expression -------------------------------------------------


@pytest.mark.parametrize("reading", ["fresh", "stale", "unknown", "not_tracked"])
def test_index_channel_passes_every_tri_state_reading_through(reading):
    assert index_channel(freshness=reading) == reading


def test_index_channel_defaults_to_fresh_only_without_a_reading():
    """Callers supplying just the Boolean keep their previous behaviour."""
    assert index_channel() == "fresh"
    assert index_channel(index_stale=True) == "stale"


def test_index_channel_ignores_an_unrecognised_reading():
    """A future vocabulary must not leak an unknown token into the channel."""
    assert index_channel(freshness="probably_fine") == "fresh"


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"index_changed": True, "freshness": "unknown"}, "rebuilding"),
        ({"index_stale": True, "freshness": "unknown"}, "stale"),
        ({"coverage": {"complete": False}, "freshness": "unknown"}, "partial"),
    ],
)
def test_index_channel_precedence_beats_the_reading(kwargs, expected):
    """Ordering is by how badly each condition undermines the answer.

    A rebuild in flight beats a proven coverage gap beats a known lag, and all
    three beat an unestablished reading.
    """
    assert index_channel(**kwargs) == expected


# --- the two builders that had drifted ------------------------------------


@pytest.mark.parametrize("reading", ["unknown", "not_tracked"])
def test_symbol_verdict_does_not_claim_fresh_when_freshness_is_unestablished(reading):
    v = build_symbol_verdict(found_count=1, freshness=reading)
    assert _chan(v) == reading
    assert _chan(v) != "fresh"


@pytest.mark.parametrize("reading", ["unknown", "not_tracked"])
def test_file_verdict_does_not_claim_fresh_when_freshness_is_unestablished(reading):
    v = build_file_verdict(present=True, freshness=reading)
    assert _chan(v) == reading
    assert _chan(v) != "fresh"


def test_search_verdict_still_honours_the_reading():
    """The one builder that was already correct must stay correct."""
    v = build_verdict(result_count=3, freshness="unknown")["verdict"]
    assert _chan(v) == "unknown"


# --- no regression for the established cases ------------------------------


@pytest.mark.parametrize(
    "builder,kwargs",
    [
        (build_symbol_verdict, {"found_count": 1}),
        (build_file_verdict, {"present": True}),
    ],
)
def test_a_genuinely_fresh_index_still_reads_fresh(builder, kwargs):
    assert _chan(builder(freshness="fresh", **kwargs)) == "fresh"
    assert _chan(builder(**kwargs)) == "fresh"


@pytest.mark.parametrize(
    "builder,kwargs",
    [
        (build_symbol_verdict, {"found_count": 1}),
        (build_file_verdict, {"present": True}),
    ],
)
def test_a_stale_reading_still_reads_stale(builder, kwargs):
    assert _chan(builder(freshness="stale", **kwargs)) == "stale"
    assert _chan(builder(index_stale=True, **kwargs)) == "stale"


@pytest.mark.parametrize(
    "builder,kwargs",
    [
        (build_symbol_verdict, {"found_count": 1}),
        (build_file_verdict, {"present": True}),
    ],
)
def test_a_rebuild_still_outranks_the_reading(builder, kwargs):
    assert _chan(builder(index_changed=True, freshness="fresh", **kwargs)) == "rebuilding"


# --- the probe fails closed ------------------------------------------------


def test_index_freshness_returns_unknown_when_the_probe_cannot_run():
    """⚠ Failure must render as `unknown`, never `fresh`.

    An exception here IS the "we could not find out" case, so returning a
    Boolean-shaped False and letting it render as proof of currency would
    reintroduce the defect through the error path.
    """
    from jcodemunch_mcp.retrieval import verdict as V

    class _Explodes:
        @property
        def source_root(self):
            raise RuntimeError("no")

    assert V._index_freshness(_Explodes()) == "unknown"


def test_index_freshness_reports_unknown_for_an_index_with_no_source_root():
    """django/django's real shape: indexed, but nothing on disk to compare to."""
    from jcodemunch_mcp.retrieval import verdict as V

    class _Rootless:
        source_root = ""
        indexed_at = "2026-01-01T00:00:00"
        git_head = None
        file_mtimes = None

    assert V._index_freshness(_Rootless()) == "unknown"


# --- non-vacuity -----------------------------------------------------------


def test_the_old_two_state_expression_would_fail_these():
    """Prove the tests above are not vacuous.

    Every assertion that `unknown`/`not_tracked` survives is only meaningful if
    the expression it replaced would have flattened them. This reproduces that
    expression and shows it does.
    """
    for reading in ("unknown", "not_tracked"):
        old = "rebuilding" if False else ("stale" if False else "fresh")
        assert old == "fresh"
        assert index_channel(freshness=reading) != old
