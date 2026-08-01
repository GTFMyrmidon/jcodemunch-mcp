"""SQLite-backed storage for symbol embeddings.

The symbol_embeddings table lives in the same .db file as the symbol index.
Embeddings are stored as float32 BLOBs serialised via the stdlib ``array`` module —
no numpy or other deps required.
"""

import array
import logging
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_EMBEDDINGS_SCHEMA = """\
CREATE TABLE IF NOT EXISTS symbol_embeddings (
    symbol_id TEXT PRIMARY KEY,
    embedding  BLOB NOT NULL
);
"""

# Bound on ids per `IN (...)` in get_many. SQLITE_MAX_VARIABLE_NUMBER is 32766
# on modern builds but 999 on older ones; 900 clears the floor with headroom.
_GET_MANY_CHUNK = 900

_EMBED_DIM_KEY = "embed_dimension"
_EMBED_MODEL_KEY = "embed_model"
_EMBED_TASK_TYPE_KEY = "embed_task_type"


def _encode_embedding(vec: list[float]) -> bytes:
    """Serialise a float list to bytes (float32, native byte order)."""
    return array.array("f", vec).tobytes()


def _decode_embedding(data: bytes) -> list[float]:
    """Deserialise bytes back to a float list."""
    a: array.array = array.array("f")
    a.frombytes(data)
    return list(a)


class EmbeddingStore:
    """Thin CRUD wrapper around the ``symbol_embeddings`` SQLite table."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    # ── Connection ─────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), isolation_level=None)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA journal_size_limit = 67108864")  # bound WAL growth under starved checkpoints
        conn.executescript(_EMBEDDINGS_SCHEMA)
        return conn

    # ── Dimension / model meta ─────────────────────────────────────────────

    def get_dimension(self) -> Optional[int]:
        """Return stored embedding dimension, or None if not set."""
        try:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT value FROM meta WHERE key = ?", (_EMBED_DIM_KEY,)
                ).fetchone()
                return int(row[0]) if row else None
            finally:
                conn.close()
        except Exception:
            logger.debug("EmbeddingStore.get_dimension failed", exc_info=True)
            return None

    def set_dimension(self, dim: int, model: str = "") -> None:
        """Persist embedding dimension (and optionally model name) to meta."""
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (_EMBED_DIM_KEY, str(dim)),
            )
            if model:
                conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                    (_EMBED_MODEL_KEY, model),
                )
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def get_task_type(self) -> Optional[str]:
        """Return stored embedding task type, or None if not set."""
        try:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT value FROM meta WHERE key = ?", (_EMBED_TASK_TYPE_KEY,)
                ).fetchone()
                return row[0] if row else None
            finally:
                conn.close()
        except Exception:
            logger.debug("EmbeddingStore.get_task_type failed", exc_info=True)
            return None

    def set_task_type(self, task_type: str) -> None:
        """Persist the embedding task type used when building the index."""
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (_EMBED_TASK_TYPE_KEY, task_type),
            )
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    # ── Read ───────────────────────────────────────────────────────────────

    def get(self, symbol_id: str) -> Optional[list[float]]:
        """Return the embedding for one symbol, or None."""
        try:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT embedding FROM symbol_embeddings WHERE symbol_id = ?",
                    (symbol_id,),
                ).fetchone()
                return _decode_embedding(row[0]) if row else None
            finally:
                conn.close()
        except Exception:
            logger.debug("EmbeddingStore.get failed for %s", symbol_id, exc_info=True)
            return None

    def get_all_ids(self) -> set[str]:
        """Return the set of symbol IDs that have stored embeddings."""
        try:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT symbol_id FROM symbol_embeddings"
                ).fetchall()
                return {row[0] for row in rows}
            finally:
                conn.close()
        except Exception:
            logger.debug("EmbeddingStore.get_all_ids failed", exc_info=True)
            return set()

    def get_all(self) -> dict[str, list[float]]:
        """Return every stored embedding as {symbol_id: vector}."""
        try:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT symbol_id, embedding FROM symbol_embeddings"
                ).fetchall()
                return {row[0]: _decode_embedding(row[1]) for row in rows}
            finally:
                conn.close()
        except Exception:
            logger.debug("EmbeddingStore.get_all failed", exc_info=True)
            return {}

    def get_all_readonly(self) -> dict[str, list[float]]:
        """Every stored embedding, WITHOUT touching the file (v1.108.185).

        ``_connect`` runs ``PRAGMA journal_mode = WAL`` and a CREATE-TABLE script
        on every connection, so even a pure read wrote to the database and bumped
        its mtime. That is the same defect ``runtime/confidence.py`` was fixed for,
        and on the fusion search path it had a sharper consequence than a wasted
        cache entry:

        ``_search_symbols_fusion`` probes for embeddings on every call, so the
        first fusion search in a process moved the ``.db`` mtime mid-scan. That
        made ``index_changed_since_load`` report ``channels.index: "rebuilding"``
        and ``moved_during_scan`` fire, which downgraded the verdict to
        ``degraded`` — so a genuine fusion absence could not reach ``absent`` at
        all, for a reason that was entirely self-inflicted.

        Returns an empty mapping when the file or the table is absent, which is
        the honest answer to "are there embeddings for this repo" and is exactly
        what the caller does with a failure anyway.

        ⚠ ``immutable=1`` is load-bearing and ``mode=ro`` alone is NOT enough,
        which cost an hour to establish: read-only in SQLite means "cannot modify
        the DATABASE", not "cannot touch the filesystem". A plain ``mode=ro``
        connection to a WAL-mode database still creates the ``-wal`` and ``-shm``
        sidecars, and ``_db_mtime_ns`` takes the max over the ``.db`` and the
        ``-wal`` — so the mtime moved anyway and the spurious `rebuilding` verdict
        came back. Measured: sidecars went None -> present across one fusion search.

        The trade-off ``immutable=1`` accepts, stated rather than hidden: vectors
        sitting in an un-checkpointed WAL are not read, so the similarity channel
        may be skipped when a watcher has just written embeddings. That can only
        weaken the RANKING — the channel adds candidates and never removes any — so
        it cannot manufacture a false absence, which is the opposite of what the
        mtime bump did.
        """
        try:
            conn = sqlite3.connect(
                f"file:{self._db_path}?mode=ro&immutable=1",
                uri=True,
                isolation_level=None,
            )
        except Exception:
            logger.debug("EmbeddingStore.get_all_readonly could not open %s",
                         self._db_path, exc_info=True)
            return {}
        try:
            rows = conn.execute(
                "SELECT symbol_id, embedding FROM symbol_embeddings"
            ).fetchall()
            return {row[0]: _decode_embedding(row[1]) for row in rows}
        except Exception:
            logger.debug("EmbeddingStore.get_all_readonly failed", exc_info=True)
            return {}
        finally:
            conn.close()

    def get_many(self, symbol_ids) -> dict[str, list[float]]:
        """Embeddings for a NAMED set of symbols, without touching the file.

        For callers that already know which symbols can participate in their
        result. ``find_similar_symbols`` prefilters its pairs on non-semantic
        evidence and then used ``count()`` + ``get_all()``, reading every vector
        in the repository to keep the few percent its own prefilter had already
        selected. On a large repository that is the dominant cost of the call
        (reported by @rknighton in #398: 2.63% to 7.65% of fetched vectors were
        actually used).

        Uses the same ``mode=ro&immutable=1`` connection as
        ``get_all_readonly`` and for the same reason: ``_connect`` runs a
        PRAGMA and a CREATE-TABLE on every connection, so a plain read bumps the
        database mtime and can make an unrelated scan report itself as
        rebuilding. See that method's docstring for why ``mode=ro`` alone is not
        enough, and for the stated trade-off (vectors sitting in an
        un-checkpointed WAL are not read, which can only weaken a ranking).

        Chunked: an ``IN (...)`` clause is bounded by SQLITE_MAX_VARIABLE_NUMBER,
        which is 999 on older builds. A candidate set large enough to matter here
        is exactly the one that would overflow it.

        Returns only the ids that are present. A missing id is not an error, it
        is a symbol that was never embedded.
        """
        ids = [s for s in dict.fromkeys(symbol_ids) if s]
        if not ids:
            return {}
        try:
            conn = sqlite3.connect(
                f"file:{self._db_path}?mode=ro&immutable=1",
                uri=True,
                isolation_level=None,
            )
        except Exception:
            logger.debug("EmbeddingStore.get_many could not open %s",
                         self._db_path, exc_info=True)
            return {}
        out: dict[str, list[float]] = {}
        try:
            for start in range(0, len(ids), _GET_MANY_CHUNK):
                chunk = ids[start:start + _GET_MANY_CHUNK]
                placeholders = ",".join("?" * len(chunk))
                rows = conn.execute(
                    "SELECT symbol_id, embedding FROM symbol_embeddings "
                    f"WHERE symbol_id IN ({placeholders})",
                    chunk,
                ).fetchall()
                for row in rows:
                    out[row[0]] = _decode_embedding(row[1])
            return out
        except Exception:
            logger.debug("EmbeddingStore.get_many failed", exc_info=True)
            return {}
        finally:
            conn.close()

    def count(self) -> int:
        """Return the number of stored embeddings."""
        try:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT COUNT(*) FROM symbol_embeddings"
                ).fetchone()
                return int(row[0]) if row else 0
            finally:
                conn.close()
        except Exception:
            return 0

    # ── Write ──────────────────────────────────────────────────────────────

    def set_many(self, embeddings: dict[str, list[float]]) -> None:
        """Upsert multiple symbol embeddings in one transaction."""
        if not embeddings:
            return
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            conn.executemany(
                "INSERT OR REPLACE INTO symbol_embeddings (symbol_id, embedding) VALUES (?, ?)",
                [(sid, _encode_embedding(vec)) for sid, vec in embeddings.items()],
            )
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def delete_many(self, symbol_ids: list[str]) -> None:
        """Remove embeddings for specific symbols (e.g. after incremental reindex)."""
        if not symbol_ids:
            return
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            placeholders = ",".join("?" * len(symbol_ids))
            conn.execute(
                f"DELETE FROM symbol_embeddings WHERE symbol_id IN ({placeholders})",
                symbol_ids,
            )
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def clear(self) -> None:
        """Delete all stored embeddings (used by embed_repo with force=True)."""
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            conn.execute("DELETE FROM symbol_embeddings")
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()
