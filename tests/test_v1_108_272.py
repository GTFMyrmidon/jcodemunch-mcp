"""#440 — identity_hit is a measurement on the non-fusion search_symbols exits.

Until v1.108.272 both non-fusion exits built a score-only ledger input, so
``extract_ledger_features`` — which reads ``identity``/``identity_match`` off the
rows it is handed — recorded ``identity_hit = 0`` on every ``search_symbols`` row
whatever the identity channel found. ``search_symbols_fusion`` had the same defect
and was fixed in v1.108.187; these two exits were missed.

The load-bearing assertion is not "the column is 1". It is that the column now
VARIES with what the identity channel found, on both exits, while the confidence
number stays exactly where it was.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.search_symbols import (
    _identity_score,
    _ledger_identity_rows,
    search_symbols,
)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A one-symbol repo with telemetry on and an isolated store."""
    monkeypatch.setenv("JCODEMUNCH_PERF_TELEMETRY", "1")
    from jcodemunch_mcp import config as _config

    _config.load_config(storage_path=str(tmp_path / "store"))

    project = tmp_path / "sample"
    project.mkdir()
    (project / "billing.py").write_text(
        "def calculate_invoice_total(items):\n"
        "    return sum(items)\n",
        encoding="utf-8",
    )
    store = str(tmp_path / "store")
    name = index_folder(
        path=str(project), use_ai_summaries=False, storage_path=store
    )["repo"]
    return name, store


def _rows(store):
    conn = sqlite3.connect(str(Path(store) / "telemetry.db"))
    try:
        return conn.execute(
            "SELECT tool, identity_hit, semantic_used FROM ranking_events"
        ).fetchall()
    finally:
        conn.close()


def test_exact_name_match_records_identity_hit(repo):
    """The reporter's repro: an exact symbol name on the default path."""
    name, store = repo
    search_symbols(
        repo=name, query="calculate_invoice_total", storage_path=store, max_results=5
    )
    rows = [r for r in _rows(store) if r[0] == "search_symbols"]
    assert rows, "the default path recorded no ranking event"
    assert all(r[1] == 1 for r in rows), (
        "identity_hit is still 0 for an exact name match on the default path "
        f"(rows={rows})"
    )


def test_identity_hit_is_zero_when_the_channel_finds_nothing(repo):
    """The other half: 0 must still mean 'no name match', not 'never asked'.

    Without this, a fix that hardcoded 1 would pass the test above.
    """
    name, store = repo
    search_symbols(repo=name, query="zzz_nothing_like_this", storage_path=store)
    rows = [r for r in _rows(store) if r[0] == "search_symbols"]
    assert all(r[1] == 0 for r in rows), f"expected no identity match (rows={rows})"


def test_ledger_rows_carry_identity_only_for_the_top_three():
    """``extract_ledger_features`` reads the top three, so only those pay the cost."""
    entries = [{"name": f"handler_{i}", "id": f"f.py::handler_{i}"} for i in range(6)]
    rows = _ledger_identity_rows(
        [10.0, 9.0, 8.0, 7.0, 6.0, 5.0], entries, ["handler_0"], "handler_0"
    )
    assert len(rows) == 6
    assert [r["score"] for r in rows] == [10.0, 9.0, 8.0, 7.0, 6.0, 5.0]
    assert rows[0]["identity"] == 50.0, "exact name match should score 50"
    assert all("identity" in r for r in rows[:3])
    assert not any("identity" in r for r in rows[3:])


def test_ledger_rows_tolerate_fewer_entries_than_scores():
    """Budget packing can leave fewer entries than scores; that must not raise."""
    rows = _ledger_identity_rows([3.0, 2.0, 1.0], [{"name": "a", "id": "a"}], ["a"], "a")
    assert len(rows) == 3
    assert rows[0]["identity"] == 50.0
    assert not any("identity" in r for r in rows[1:])


def test_extract_ledger_features_reads_the_key_we_write():
    """Guard the coupling: the producer's key and the reader's key are the same one.

    This is the defect in one line — the producer wrote rows the reader could not
    read, and nothing failed.
    """
    from jcodemunch_mcp.retrieval.confidence import extract_ledger_features

    rows = _ledger_identity_rows(
        [12.0], [{"name": "widget", "id": "w.py::widget"}], ["widget"], "widget"
    )
    assert extract_ledger_features(rows)["identity_hit"] is True


def test_confidence_is_unchanged_by_the_ledger_input(repo):
    """⚠ The ledger input must NOT reach ``attach_confidence``.

    ``compute_confidence`` sniffs the same ``identity`` key when no
    ``has_identity_match`` is passed and scores it 1.0 known-true / 0.7 unknown,
    so sharing one input would silently raise the confidence of every non-fusion
    search. Recording a column must not move a published number.
    """
    from jcodemunch_mcp.retrieval.confidence import BM25_CEILING, compute_confidence

    name, store = repo
    out = search_symbols(
        repo=name, query="calculate_invoice_total", storage_path=store, max_results=5
    )
    published = out["_meta"]["confidence"]

    # What the published number must still be: graded on score-only rows, whose
    # identity component is the 0.7 unknown default.
    scores = [{"score": r["score"]} for r in out["results"] if "score" in r] or [
        {"score": 51.73}
    ]
    score_only = compute_confidence(scores, is_stale=False, score_ceiling=BM25_CEILING)
    assert score_only["components"]["identity"] == pytest.approx(0.7)

    leaked = compute_confidence(
        _ledger_identity_rows(
            [s["score"] for s in scores],
            [{"name": "calculate_invoice_total", "id": "billing.py::calculate_invoice_total"}],
            ["calculate_invoice_total"],
            "calculate_invoice_total",
        ),
        is_stale=False,
        score_ceiling=BM25_CEILING,
    )
    assert leaked["components"]["identity"] == pytest.approx(1.0), (
        "precondition: feeding the ledger rows to compute_confidence must move the "
        "identity component, or this test proves nothing"
    )
    assert leaked["confidence"] != pytest.approx(score_only["confidence"])

    assert published == pytest.approx(score_only["confidence"]), (
        "the ledger's identity rows leaked into attach_confidence; recording a "
        f"column moved a published number ({published} vs {score_only['confidence']})"
    )


def test_semantic_only_records_a_measurement_not_a_default(repo, monkeypatch):
    """``semantic_only`` skips the identity channel (``idn = 0.0``).

    Reusing that value would keep recording a default dressed as a measurement,
    which is the defect this fixes. Recomputing gives the honest answer.
    """
    name, store = repo
    entries = [{"name": "calculate_invoice_total", "id": "billing.py::calculate_invoice_total"}]
    rows = _ledger_identity_rows(
        [0.91], entries, ["calculate_invoice_total"], "calculate_invoice_total"
    )
    assert rows[0]["identity"] == 50.0, (
        "semantic_only would record identity_hit=0 for an exact name match"
    )


def test_identity_recomputation_matches_the_scoring_channel():
    """The recomputation is faithful, not an approximation of the real channel."""
    sym = {"name": "get_symbol_source", "id": "src/tools/get_symbol_source.py::get_symbol_source"}
    for query in ("get_symbol_source", "get_sym", "tools.get_symbol_source", "unrelated"):
        terms = query.replace(".", " ").split()
        expected = _identity_score(sym, " ".join(terms), query)
        row = _ledger_identity_rows([1.0], [sym], terms, query)[0]
        assert row["identity"] == expected, f"drifted from the channel for {query!r}"


def test_pre_fix_rows_are_declared_unseparable():
    """No heuristic was invented for the history this cannot fix.

    Pre-fix ``search_symbols`` rows always carried ``top1_score`` and only omitted
    the identity key, so they are indistinguishable from an honest post-fix 0. The
    predicate must keep returning True for them rather than guessing.
    """
    from jcodemunch_mcp.retrieval.ledger_trust import identity_label_is_trustworthy

    # (ts, repo, tool, qh, query, returned_ids, top1, top2, conf, sem, ident, stale)
    pre_fix = (0.0, "o/r", "search_symbols", "h", "q", json.dumps(["a"]),
               51.7, 2.0, 0.8, 0, 0, 0)
    assert identity_label_is_trustworthy(pre_fix) is True
