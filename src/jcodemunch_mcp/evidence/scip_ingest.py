"""SCIP ingest orchestrator: parse → resolve → persist.

Mirrors ``runtime/ingest.py``'s pipeline shape. Occurrences resolve to jcm
symbol_ids through the same ``(file, line, name)`` machinery runtime traces
use (``runtime/resolve.py``); edges land in the ``scip_*`` table family —
deliberately separate from ``runtime_*`` so compile-time proof never
inflates runtime coverage statistics.

Edge semantics:
    reference       — an occurrence of symbol B inside the body of symbol A
                      (compiler-verified "A references B")
    implementation  — SymbolInformation relationship "A implements B"

Deliberate skips (counted, not silent):
    local symbols   — SCIP ``local N`` symbols are function-internal
    import role     — import-statement occurrences duplicate the existing
                      import graph (``find_importers``) and would attribute
                      edges to whole files rather than enclosing symbols
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..runtime.resolve import resolve_to_symbol_id
from .scip import (
    SYMBOL_ROLE_DEFINITION,
    SYMBOL_ROLE_IMPORT,
    display_name_from_symbol,
    is_local_symbol,
    parse_scip_file,
)

logger = logging.getLogger(__name__)

DEFAULT_SCIP_MAX_ROWS = 200_000

_SCIP_TABLES_SQL = """\
CREATE TABLE IF NOT EXISTS scip_edges (
    from_symbol_id TEXT NOT NULL,
    to_symbol_id   TEXT NOT NULL,
    kind           TEXT NOT NULL,
    count          INTEGER NOT NULL DEFAULT 0,
    first_seen     TEXT,
    last_seen      TEXT,
    PRIMARY KEY (from_symbol_id, to_symbol_id, kind)
);
CREATE INDEX IF NOT EXISTS idx_scip_edges_to ON scip_edges(to_symbol_id);

CREATE TABLE IF NOT EXISTS scip_unmapped (
    file_path    TEXT,
    line_no      INTEGER,
    scip_symbol  TEXT,
    reason       TEXT NOT NULL,
    count        INTEGER NOT NULL DEFAULT 0,
    last_seen    TEXT,
    PRIMARY KEY (file_path, line_no, scip_symbol, reason)
);

CREATE TABLE IF NOT EXISTS scip_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _covered_files(edges: dict) -> set:
    """Repo-relative paths the ingested edges actually touch.

    ``scip_edges`` stores no file column, but a symbol id carries its path as
    the prefix before ``::``. Both endpoints count: an edge is evidence about
    the caller's file and the callee's file alike, so a change to either can
    invalidate it.
    """
    files = set()
    for key in edges:
        for symbol_id in (key[0], key[1]):
            if symbol_id and "::" in symbol_id:
                files.add(symbol_id.split("::", 1)[0])
    return files


def _ensure_scip_tables(conn: sqlite3.Connection) -> None:
    """Idempotent table creation.

    Fresh v17 databases already carry these from the schema/migration; this
    guard covers pre-v17 databases that get an ``import-scip`` before any
    store ``_connect`` has run the migration ladder.
    """
    conn.executescript(_SCIP_TABLES_SQL)


def ingest_scip_file(
    *,
    db_path: str,
    file_path: str,
    max_rows: int = DEFAULT_SCIP_MAX_ROWS,
) -> dict[str, Any]:
    """Ingest one SCIP index file into the scip_* tables.

    Returns a counts dict; raises FileNotFoundError / ValueError for a
    missing or non-SCIP input (the CLI wrapper turns those into
    ``{"success": False}``).
    """
    index = parse_scip_file(file_path)
    dbp = Path(db_path)

    # Definition map: scip symbol string → (relative_path, 1-based line).
    # First definition wins (SCIP emits one canonical definition per symbol).
    definitions: dict[str, tuple[str, Optional[int]]] = {}
    for doc in index.documents:
        for occ in doc.occurrences:
            if occ.symbol and occ.symbol_roles & SYMBOL_ROLE_DEFINITION:
                definitions.setdefault(occ.symbol, (doc.relative_path, occ.start_line))

    ro = sqlite3.connect(f"file:{dbp}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row

    # scip symbol string → resolved jcm symbol_id (memoized; None = miss)
    target_cache: dict[str, Optional[str]] = {}

    def _resolve_target(scip_symbol: str) -> Optional[str]:
        if scip_symbol in target_cache:
            return target_cache[scip_symbol]
        resolved: Optional[str] = None
        definition = definitions.get(scip_symbol)
        if definition is not None:
            def_path, def_line = definition
            resolved = resolve_to_symbol_id(
                ro, def_path, def_line, display_name_from_symbol(scip_symbol)
            )
        target_cache[scip_symbol] = resolved
        return resolved

    edges: dict[tuple[str, str, str], int] = {}
    unmapped: dict[tuple[str, Optional[int], str, str], int] = {}
    occurrences_seen = 0
    skipped_local = 0
    skipped_import = 0

    try:
        for doc in index.documents:
            for occ in doc.occurrences:
                if not occ.symbol or occ.symbol_roles & SYMBOL_ROLE_DEFINITION:
                    continue
                occurrences_seen += 1
                if is_local_symbol(occ.symbol):
                    skipped_local += 1
                    continue
                if occ.symbol_roles & SYMBOL_ROLE_IMPORT:
                    skipped_import += 1
                    continue
                line = occ.start_line
                to_id = _resolve_target(occ.symbol)
                if to_id is None:
                    # External package symbol, or defined in code jcm has
                    # not indexed. Recorded, never guessed.
                    key = (doc.relative_path, line, occ.symbol, "target_unresolved")
                    unmapped[key] = unmapped.get(key, 0) + 1
                    continue
                from_id = resolve_to_symbol_id(ro, doc.relative_path, line, None)
                if from_id is None:
                    # Occurrence outside any indexed symbol body (module-level
                    # expression, comment-adjacent, path-frame mismatch).
                    key = (doc.relative_path, line, occ.symbol, "enclosing_not_found")
                    unmapped[key] = unmapped.get(key, 0) + 1
                    continue
                if from_id == to_id:
                    continue
                edge_key = (from_id, to_id, "reference")
                edges[edge_key] = edges.get(edge_key, 0) + 1

        # Implementation relationships (document-local + external symbol infos)
        all_infos = [info for doc in index.documents for info in doc.symbols]
        all_infos.extend(index.external_symbols)
        for info in all_infos:
            if not info.symbol:
                continue
            for rel in info.relationships:
                if not rel.is_implementation or not rel.symbol:
                    continue
                impl_id = _resolve_target(info.symbol)
                iface_id = _resolve_target(rel.symbol)
                if impl_id is None or iface_id is None or impl_id == iface_id:
                    key = (
                        definitions.get(info.symbol, ("", None))[0],
                        None,
                        rel.symbol if iface_id is None else info.symbol,
                        "implementation_unresolved",
                    )
                    unmapped[key] = unmapped.get(key, 0) + 1
                    continue
                edge_key = (impl_id, iface_id, "implementation")
                edges[edge_key] = edges.get(edge_key, 0) + 1
    finally:
        ro.close()

    now = _utc_now()
    evicted = _persist(
        dbp,
        index_tool=f"{index.tool_name} {index.tool_version}".strip(),
        project_root=index.project_root,
        edges=edges,
        unmapped=unmapped,
        now=now,
        max_rows=max_rows,
    )

    return {
        "records": occurrences_seen,
        "edges_written": sum(edges.values()),
        "unique_edges": len(edges),
        "reference_edges": sum(1 for k in edges if k[2] == "reference"),
        "implementation_edges": sum(1 for k in edges if k[2] == "implementation"),
        "unmapped": sum(unmapped.values()),
        "unmapped_reasons": _reason_counts(unmapped),
        "skipped_local": skipped_local,
        "skipped_import": skipped_import,
        "evicted": evicted,
        "tool": f"{index.tool_name} {index.tool_version}".strip(),
    }


def _reason_counts(unmapped: dict[tuple[str, Optional[int], str, str], int]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for (_file, _line, _symbol, reason), n in unmapped.items():
        counts[reason] = counts.get(reason, 0) + n
    return counts


def _persist(
    db_path: Path,
    *,
    index_tool: str,
    project_root: str,
    edges: dict[tuple[str, str, str], int],
    unmapped: dict[tuple[str, Optional[int], str, str], int],
    now: str,
    max_rows: int,
) -> int:
    """Bulk-write edges/unmapped/meta. Returns the FIFO-eviction count."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_scip_tables(conn)
        conn.execute("BEGIN")
        conn.executemany(
            """
            INSERT INTO scip_edges (from_symbol_id, to_symbol_id, kind, count, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(from_symbol_id, to_symbol_id, kind) DO UPDATE SET
                count = count + excluded.count,
                last_seen = excluded.last_seen
            """,
            [
                (from_id, to_id, kind, n, now, now)
                for (from_id, to_id, kind), n in edges.items()
            ],
        )
        conn.executemany(
            """
            INSERT INTO scip_unmapped (file_path, line_no, scip_symbol, reason, count, last_seen)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_path, line_no, scip_symbol, reason) DO UPDATE SET
                count = count + excluded.count,
                last_seen = excluded.last_seen
            """,
            [
                (file_path, line_no, scip_symbol, reason, n, now)
                for (file_path, line_no, scip_symbol, reason), n in unmapped.items()
            ],
        )
        # Staleness anchor: the index's git_head AT INGEST TIME. When the
        # repo is later reindexed at a new HEAD, consumers compare this
        # against the live meta git_head and flag the evidence stale.
        row = conn.execute("SELECT value FROM meta WHERE key = 'git_head'").fetchone()
        git_head_at_ingest = row["value"] if row and row["value"] else ""
        meta_rows = [
            ("tool", index_tool),
            ("project_root", project_root),
            ("ingested_at", now),
            ("git_head", git_head_at_ingest),
        ]
        # v1.108.200: per-file provenance so staleness can degrade per file
        # instead of repo-wide. A HEAD that moved is NECESSARY for the evidence
        # to be stale but not SUFFICIENT — a commit touching one unrelated file
        # used to mark every edge stale on every read, and a warning that fires
        # on nearly every answer is one people learn to click past (the same
        # reasoning that narrowed the v1.108.181 working-tree gate).
        #
        # Stored as one JSON value in the EXISTING scip_meta key-value table on
        # purpose: no new table, no new column, so no INDEX_VERSION bump and no
        # observatory cache-key bump. Consumers read it through
        # ``scip_meta_and_stale``, so no consumer changes either.
        covered = _covered_files(edges)
        if covered:
            ingest_hashes: dict[str, str] = {}
            try:
                for frow in conn.execute("SELECT path, hash FROM files"):
                    if frow["path"] in covered and frow["hash"]:
                        ingest_hashes[frow["path"]] = frow["hash"]
            except sqlite3.Error:
                ingest_hashes = {}
            # An empty map is NOT written. Absence means "unknown", which makes
            # the reader fall back to the head comparison — never to "fresh".
            if ingest_hashes:
                meta_rows.append(
                    ("file_hashes", json.dumps(ingest_hashes, sort_keys=True))
                )
        conn.executemany(
            "INSERT OR REPLACE INTO scip_meta (key, value) VALUES (?, ?)",
            meta_rows,
        )
        evicted = _apply_fifo_eviction(conn, max_rows)
        conn.execute("COMMIT")
        return evicted
    except sqlite3.Error:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def _apply_fifo_eviction(conn: sqlite3.Connection, max_rows: int) -> int:
    """Trim scip_edges + scip_unmapped down to ``max_rows`` each (oldest
    rowids first, 1k batches — same policy as the runtime tables)."""
    if max_rows <= 0:
        return 0
    evicted = 0
    for table in ("scip_edges", "scip_unmapped"):
        count = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        while count > max_rows:
            batch = min(1000, count - max_rows)
            conn.execute(
                f"DELETE FROM {table} WHERE rowid IN "
                f"(SELECT rowid FROM {table} ORDER BY rowid ASC LIMIT ?)",
                (batch,),
            )
            evicted += batch
            count -= batch
    return evicted
