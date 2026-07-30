"""Shared SCIP-evidence consumption for the graph tools (compile-time evidence P2).

P1 (v1.108.96) ingested SCIP compiler-verified edges into the ``scip_*`` table
family and wired them into ``find_references``. P2 lets the graph consumers
(``get_blast_radius``, ``get_call_hierarchy``) read the same edges through one
honest-empty, staleness-aware entry point, so each tool only writes its own
union logic.

Every reader here is READ-ONLY (``mode=ro``), byte-identical no-op when the repo
has never ingested SCIP data (including pre-v17 databases), and idempotent —
re-annotating an already-annotated response neither duplicates rows nor changes
counts, so the result cache is safe.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Optional


def open_scip_reader(store, owner: str, name: str) -> Optional[sqlite3.Connection]:
    """Return an open read-only connection when the repo has ingested SCIP data,
    else ``None``. The caller owns the connection and must close it.

    ``None`` is the honest-empty signal: the ``scip_edges`` table is absent
    (pre-v17 database) or empty (never ran ``import-scip``). Callers treat that
    as "no compile-time evidence" and return their response unchanged.
    """
    try:
        db_path = store._sqlite._db_path(owner, name)
    except Exception:
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    conn.row_factory = sqlite3.Row
    try:
        if conn.execute("SELECT 1 FROM scip_edges LIMIT 1").fetchone() is None:
            conn.close()
            return None
    except sqlite3.OperationalError:
        conn.close()
        return None
    return conn


def scip_meta_and_stale(conn: sqlite3.Connection) -> tuple[dict, bool]:
    """Read the ``scip_meta`` rows and decide staleness.

    A moved git HEAD is NECESSARY for SCIP evidence to be stale but not
    SUFFICIENT. v1.108.200 adds the second half: when the ingest recorded
    per-file hashes, the evidence is stale only if a file it actually covers
    has changed since. A commit touching one unrelated file used to mark every
    edge stale on every read across all five consumers, and a warning that
    fires on nearly every answer is one people learn to click past — the same
    reasoning that narrowed the v1.108.181 working-tree gate.

    ⚠ Fails toward STALE. An ingest that predates the hash map, an unreadable
    map, or a failed lookup all keep the old head-only answer. Absence of the
    provenance is unknown, never clean.
    """
    scip_meta = {
        row["key"]: row["value"]
        for row in conn.execute("SELECT key, value FROM scip_meta").fetchall()
    }
    head_row = conn.execute(
        "SELECT value FROM meta WHERE key = 'git_head'"
    ).fetchone()
    live_head = head_row["value"] if head_row and head_row["value"] else ""
    ingest_head = scip_meta.get("git_head", "")
    stale = bool(ingest_head and live_head and ingest_head != live_head)
    if not stale:
        return scip_meta, False

    raw = scip_meta.get("file_hashes") or ""
    if not raw:
        return scip_meta, True
    try:
        ingested = json.loads(raw)
    except (ValueError, TypeError):
        return scip_meta, True
    if not isinstance(ingested, dict) or not ingested:
        return scip_meta, True
    try:
        current = {
            row["path"]: row["hash"]
            for row in conn.execute("SELECT path, hash FROM files")
        }
    except sqlite3.Error:
        return scip_meta, True

    for path, hashed_at_ingest in ingested.items():
        # A covered file that is gone, or whose bytes moved, invalidates the
        # edges that rest on it. A missing current hash counts as changed.
        if current.get(path) != hashed_at_ingest:
            return scip_meta, True
    return scip_meta, False


def scip_reference_files(store, owner: str, name: str, target_id: str):
    """Files carrying a compiler-verified ``reference`` edge INTO ``target_id``.

    Returns ``({file: count}, scip_meta, stale)`` — the files whose symbols the
    compiler proved reference the target, which is exactly the "who uses this"
    signal the delete/edit-safety preflights need (it catches dynamic-dispatch
    and barrel-re-export call sites the import graph and text search miss).
    Excluding the target's own file is the caller's job. Honest-empty
    ``({}, {}, False)`` when no SCIP data has been ingested.
    """
    if not target_id:
        return {}, {}, False
    conn = open_scip_reader(store, owner, name)
    if conn is None:
        return {}, {}, False
    try:
        scip_meta, stale = scip_meta_and_stale(conn)
        rows = conn.execute(
            """
            SELECT s_from.file AS ref_file, SUM(e.count) AS n
            FROM scip_edges e
            JOIN symbols s_from ON s_from.id = e.from_symbol_id
            WHERE e.kind = 'reference' AND e.to_symbol_id = ?
            GROUP BY s_from.file
            """,
            (target_id,),
        ).fetchall()
        files = {r["ref_file"]: int(r["n"] or 0) for r in rows if r["ref_file"]}
        return files, scip_meta, stale
    except Exception:
        return {}, {}, False
    finally:
        conn.close()


def scip_meta_block(scip_meta: dict, stale: bool, **counts) -> dict:
    """Build the ``_meta.scip`` summary block: caller-supplied counts + tool /
    ingested_at provenance + a staleness note."""
    block: dict = dict(counts)
    block["tool"] = scip_meta.get("tool", "")
    block["ingested_at"] = scip_meta.get("ingested_at", "")
    block["stale"] = stale
    if stale:
        block["note"] = (
            "SCIP evidence was ingested at a different index HEAD. Re-run your "
            "SCIP indexer and `jcodemunch-mcp import-scip` after reindexing."
        )
    return block
