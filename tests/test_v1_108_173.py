"""Exact-match honesty in search_symbols (jcm#375 correspondence, suggestion B).

A user searched the exact token `runPromiseFulfillmentCheck`, a function
committed that day and not yet indexed. Instead of "no exact match" they got ten
ranked hits on the substring "fulfillment" (order fulfilment, shipping routing,
PHP helpers) formatted exactly like real hits. In their words, a fuzzy near-miss
was indistinguishable from a hit, which turns a missing index into confident
misinformation.

Reproduced against this repo before the fix: `extractMountsFulfillmentCheck`
returned `_extract_mounts`, `TestExpressRouterMount` and `_JS_MOUNT` under
`state: "ok"` with the note "Confident matches returned." The verdict was
actively vouching for guesses.

BM25 tokenization is the cause and is not itself wrong: the query splits into
run/promise/fulfillment/check, so token overlap scores. Correct for prose,
misleading for an identifier. The fix labels rather than suppresses, because
those results are still the best available guesses.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.retrieval.query_shape import (
    exact_match_report,
    is_identifier_query,
)


# --- which queries are identifier-shaped ----------------------------------

@pytest.mark.parametrize(
    "query",
    [
        "runPromiseFulfillmentCheck",   # camel
        "extractMountsFulfillmentCheck",
        "build_verdict",               # snake
        "_extract_mounts",
        "Store.load_config",           # qualified
        "SqliteStore::save_index",
        "__init__",                    # dunder is still an exact identifier
    ],
)
def test_identifier_shaped_queries_are_recognised(query):
    assert is_identifier_query(query) is True


@pytest.mark.parametrize(
    "query",
    [
        "how does the express provider extract mounts",  # prose
        "mounts",              # bare lowercase word: topic or symbol? unknowable
        "fulfillment",
        "",
        "   ",
        "express provider",    # two words
        "config.py",           # filename, not a symbol
    ],
)
def test_non_identifier_queries_are_left_alone(query):
    """Anything not clearly an identifier must behave exactly as before.

    A bare lowercase word is deliberately excluded. Without an interior case
    change or an underscore there is nothing separating "I am naming a symbol"
    from "I am describing a topic", and guessing wrong would stamp a false
    negative on an ordinary keyword search.
    """
    assert is_identifier_query(query) is False
    assert exact_match_report(query, [{"name": "anything", "id": "a::anything"}]) is None


# --- the report itself ----------------------------------------------------

def test_missing_identifier_is_reported_as_not_found():
    results = [
        {"name": "_extract_mounts", "id": "express.py::_extract_mounts#method"},
        {"name": "_JS_MOUNT", "id": "express.py::_JS_MOUNT#constant"},
    ]
    report = exact_match_report("extractMountsFulfillmentCheck", results)
    assert report is not None
    assert report["found"] is False
    assert report["exact"] == 0 and report["prefix"] == 0
    assert "No symbol named" in report["note"]
    # The note must point at the actionable cause, not just state the negative.
    assert "re-index" in report["note"]


def test_exact_name_match_is_found():
    results = [{"name": "build_verdict", "id": "verdict.py::build_verdict#function"}]
    report = exact_match_report("build_verdict", results)
    assert report["found"] is True and report["exact"] == 1


def test_match_is_case_insensitive():
    """Someone retyping a symbol from memory gets the case wrong more often
    than they get the spelling wrong."""
    results = [{"name": "BuildVerdict", "id": "x.py::BuildVerdict#class"}]
    assert exact_match_report("buildVerdict", results)["found"] is True


def test_qualified_query_matches_on_the_leaf():
    results = [{"name": "load_config", "id": "config.py::load_config#function"}]
    report = exact_match_report("Store.load_config", results)
    assert report["found"] is True


def test_prefix_match_counts_as_found_but_is_reported_separately():
    results = [{"name": "build_verdict_for_index", "id": "v.py::build_verdict_for_index#function"}]
    report = exact_match_report("build_verdict", results)
    assert report["found"] is True
    assert report["exact"] == 0 and report["prefix"] == 1


def test_no_note_when_there_were_no_results_at_all():
    """Zero results is the absence contract's business, not this one's.

    Attaching a "these are guesses" note to an empty list would be nonsense,
    and the verdict already reaches `absent` on its own there.
    """
    report = exact_match_report("nothingLikeThis", [])
    assert report["found"] is False
    assert "note" not in report


# --- end-to-end through search_symbols ------------------------------------
#
# `small_index` (conftest) is a 1-file repo whose symbols are MAX_RETRIES, add
# and subtract. `addValuesTogetherNow` is camel-shaped, does not exist, and
# shares the "add" token, which is precisely the near-miss shape being tested.


def _search(fixture, query, **kw):
    from jcodemunch_mcp.tools.search_symbols import search_symbols

    return search_symbols(
        fixture["repo"], query, max_results=5, storage_path=fixture["store"], **kw
    )


def test_missing_identifier_downgrades_the_verdict(small_index):
    """The sharpest part: "Confident matches returned" must stop being said."""
    result = _search(small_index, "addValuesTogetherNow")
    verdict = result["_meta"]["verdict"]
    report = result["_meta"].get("exact_match")

    assert report is not None and report["found"] is False
    if result["result_count"] > 0:
        assert verdict["state"] == "low_confidence", (
            "an identifier query answered only by token-overlap neighbours "
            "must not be reported as a confident match"
        )
        assert "Confident matches returned" not in verdict.get("note", "")
        # NOT `absent`: results WERE returned, and under the absence contract
        # only `absent` proves absence. low_confidence cannot be cited as
        # evidence the symbol does not exist.
        assert verdict["state"] != "absent"


def test_real_identifier_keeps_its_confident_verdict(small_index):
    result = _search(small_index, "MAX_RETRIES")
    assert result["result_count"] > 0
    report = result["_meta"].get("exact_match")
    assert report is not None and report["found"] is True
    assert result["_meta"]["verdict"]["state"] == "ok"


def test_prose_query_carries_no_exact_match_block(small_index):
    """Byte-identical for the common case, and no tokens spent on it."""
    result = _search(small_index, "how do the math helpers work")
    assert "exact_match" not in result["_meta"]


def test_exact_match_survives_compact_encoding(small_index):
    """The v1.108.169 trap: a dict left off the encoder allowlist is SILENTLY
    dropped, which is how the entire verdict contract went invisible once."""
    from jcodemunch_mcp.encoding import encode_response
    from jcodemunch_mcp.encoding.decoder import decode

    result = _search(small_index, "addValuesTogetherNow")
    if not result["result_count"]:
        pytest.skip("fixture produced no results to encode")

    payload, info = encode_response("search_symbols", result, "compact")
    if info.get("encoding") == "json":
        pytest.skip("dispatcher chose JSON for this payload")

    restored = decode(payload)
    assert restored["_meta"].get("exact_match") is not None, (
        "exact_match was dropped by the compact encoder; add it to _META_JSON"
    )
    assert restored["_meta"]["exact_match"]["found"] is False
    assert restored["_meta"]["verdict"]["state"] == "low_confidence"
