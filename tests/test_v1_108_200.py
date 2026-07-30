"""v1.108.200 — SCIP P3: per-file stale degradation + the digest line.

A moved git HEAD is necessary for SCIP evidence to be stale, not sufficient.
Before this release any commit — including one touching a file SCIP never
covered — marked every edge stale on every read across all five consumers.

The tests that matter here are the ones that fail if the refinement is reverted
AND the ones that prove it still fails toward stale when provenance is missing.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from jcodemunch_mcp.evidence.scip_ingest import _covered_files
from jcodemunch_mcp.tools._scip_consume import scip_meta_and_stale


def _db(tmp_path, *, ingest_head, live_head, files, ingest_hashes=None):
    """A minimal index carrying the two tables staleness reads."""
    path = tmp_path / "idx.db"
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE files (path TEXT PRIMARY KEY, hash TEXT);
        CREATE TABLE scip_meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE scip_edges (
            from_symbol_id TEXT, to_symbol_id TEXT, kind TEXT,
            PRIMARY KEY (from_symbol_id, to_symbol_id, kind)
        );
        """
    )
    conn.execute("INSERT INTO meta VALUES ('git_head', ?)", (live_head,))
    conn.executemany("INSERT INTO files VALUES (?, ?)", list(files.items()))
    rows = [("git_head", ingest_head), ("tool", "scip-typescript 0.3")]
    if ingest_hashes is not None:
        rows.append(("file_hashes", json.dumps(ingest_hashes, sort_keys=True)))
    conn.executemany("INSERT INTO scip_meta VALUES (?, ?)", rows)
    conn.commit()
    return conn


# --------------------------------------------------------------------------
# The behaviour change. These fail on revert.
# --------------------------------------------------------------------------

def test_head_moved_but_no_covered_file_changed_is_not_stale(tmp_path):
    """THE point of the release: an unrelated commit must not invalidate SCIP.

    Fails on revert — the old code returned True for exactly this input.
    """
    conn = _db(
        tmp_path,
        ingest_head="aaaa", live_head="bbbb",
        files={"src/a.ts": "h1", "src/unrelated.ts": "CHANGED"},
        ingest_hashes={"src/a.ts": "h1"},
    )
    try:
        _meta, stale = scip_meta_and_stale(conn)
    finally:
        conn.close()
    assert stale is False


def test_head_moved_and_a_covered_file_changed_is_stale(tmp_path):
    """The gate still fires when the evidence actually is invalidated."""
    conn = _db(
        tmp_path,
        ingest_head="aaaa", live_head="bbbb",
        files={"src/a.ts": "h2"},
        ingest_hashes={"src/a.ts": "h1"},
    )
    try:
        _meta, stale = scip_meta_and_stale(conn)
    finally:
        conn.close()
    assert stale is True


def test_a_covered_file_that_disappeared_counts_as_changed(tmp_path):
    """A deleted file has no current hash. Missing must not read as matching."""
    conn = _db(
        tmp_path,
        ingest_head="aaaa", live_head="bbbb",
        files={"src/other.ts": "h9"},
        ingest_hashes={"src/gone.ts": "h1"},
    )
    try:
        _meta, stale = scip_meta_and_stale(conn)
    finally:
        conn.close()
    assert stale is True


# --------------------------------------------------------------------------
# Fails toward stale. Absence of provenance is unknown, never clean.
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "hashes",
    [None, {}],
    ids=["pre-upgrade-ingest-has-no-map", "empty-map-is-not-proof-of-freshness"],
)
def test_missing_provenance_keeps_the_old_head_only_answer(tmp_path, hashes):
    conn = _db(
        tmp_path,
        ingest_head="aaaa", live_head="bbbb",
        files={"src/a.ts": "h1"},
        ingest_hashes=hashes,
    )
    try:
        _meta, stale = scip_meta_and_stale(conn)
    finally:
        conn.close()
    assert stale is True


def test_unreadable_provenance_fails_toward_stale(tmp_path):
    conn = _db(
        tmp_path, ingest_head="aaaa", live_head="bbbb", files={"src/a.ts": "h1"},
    )
    conn.execute(
        "INSERT OR REPLACE INTO scip_meta VALUES ('file_hashes', ?)", ("{not json",)
    )
    conn.commit()
    try:
        _meta, stale = scip_meta_and_stale(conn)
    finally:
        conn.close()
    assert stale is True


def test_unmoved_head_is_still_fresh_without_touching_the_files_table(tmp_path):
    """The cheap path is unchanged: HEAD equal short-circuits before any lookup."""
    conn = _db(
        tmp_path,
        ingest_head="aaaa", live_head="aaaa",
        files={"src/a.ts": "CHANGED"},
        ingest_hashes={"src/a.ts": "h1"},
    )
    try:
        _meta, stale = scip_meta_and_stale(conn)
    finally:
        conn.close()
    assert stale is False


# --------------------------------------------------------------------------
# Covered-file derivation
# --------------------------------------------------------------------------

def test_covered_files_takes_both_endpoints_of_every_edge(tmp_path):
    edges = {
        ("src/a.ts::foo#function", "src/b.ts::bar#function", "reference"): 1,
        ("src/b.ts::bar#function", "src/c.ts::baz#class", "implementation"): 2,
    }
    assert _covered_files(edges) == {"src/a.ts", "src/b.ts", "src/c.ts"}


def test_covered_files_ignores_ids_without_a_path_prefix(tmp_path):
    assert _covered_files({("local 1", "", "reference"): 1}) == set()
