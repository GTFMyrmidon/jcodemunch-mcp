"""What a scan's answer depends on, captured cheaply enough to re-check.

#377 hardening item 3. The search result cache keys on the index's
``indexed_at``, so it invalidates on a REINDEX and on nothing else. That is
right for a positive result — the rows it returned were really in the index,
and stale disclosure is enough. It is wrong for a negative one:

    1. the query is absent against snapshot A
    2. the result and its `fresh` verdict are cached
    3. the source changes without the index being rebuilt
    4. the same query hits the cache
    5. the old fresh/absent verdict is replayed, and an absence proof is
       minted over a tree that no longer exists

A cached negative has to prove it still describes the subject it was measured
against. This module captures the subject's state at cache-write time and
compares it at cache-read time, so the refusal names WHAT moved rather than
asserting a generic doubt.

Deliberately cheap. The git HEAD lookup is the process-wide stat-signature
cache in ``freshness``; the index generation and the .db mtime are one stat.
The one genuinely expensive signal, the working tree, is captured ONLY for a
response that reached ``absent`` — the only case where it can change the
answer. A positive cache hit pays nothing.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Bursts of tool calls share one `git status`. Short enough that an edit is
# picked up within a couple of seconds, long enough that a fan-out of searches
# does not spawn a subprocess each.
_TREE_CACHE_TTL_S = 2.0
_tree_cache: dict[str, tuple[Optional[str], float]] = {}


def _clear_tree_cache() -> None:
    """Test hook: drop cached working-tree fingerprints."""
    _tree_cache.clear()


def working_tree_fingerprint(source_root: Optional[str]) -> Optional[str]:
    """Digest of the working tree's uncommitted state, or None when unknown.

    None means "could not be determined" (not a git checkout, git missing, the
    command failed) and must never be compared as equality with a real
    fingerprint — unknown is not unchanged.
    """
    if not source_root:
        return None
    now = time.monotonic()
    hit = _tree_cache.get(source_root)
    if hit is not None and (now - hit[1]) < _TREE_CACHE_TTL_S:
        return hit[0]
    fp: Optional[str] = None
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=source_root,
            capture_output=True,
            timeout=10,
            check=False,
        )
        if out.returncode == 0:
            fp = hashlib.sha256(out.stdout).hexdigest()[:16]
    except (OSError, subprocess.SubprocessError):
        logger.debug("Working-tree probe failed for %s", source_root, exc_info=True)
    _tree_cache[source_root] = (fp, now)
    return fp


def capture(index, *, include_tree: bool = False) -> dict:
    """Live state of the subject this index describes. Never raises."""
    state: dict = {}
    try:
        source_root = getattr(index, "source_root", "") or None
        state["generation"] = getattr(index, "indexed_at", "") or None
        state["index_sha"] = getattr(index, "git_head", None)
        try:
            from ..storage.sqlite_store import _db_mtime_ns

            db_path = getattr(index, "_db_path", None)
            state["db_mtime_ns"] = _db_mtime_ns(Path(db_path)) if db_path else None
        except Exception:
            state["db_mtime_ns"] = None
        live = None
        if source_root:
            try:
                from .freshness import _git_head

                live = _git_head(Path(source_root))
            except Exception:
                live = None
        state["live_sha"] = live
        if include_tree:
            state["tree"] = working_tree_fingerprint(source_root)
    except Exception:
        logger.debug("Subject-state capture failed", exc_info=True)
    return state


def changed(before: Optional[dict], after: Optional[dict]) -> Optional[str]:
    """Why *after* no longer describes the same subject as *before*.

    Returns None when nothing observable moved. An unknown value on either
    side is never reported as a change: guessing here would refuse every
    absence on a non-git checkout, where nothing was ever knowable and nothing
    changed either.
    """
    if not before or not after:
        return None
    if before.get("generation") != after.get("generation"):
        return "the index was rebuilt after this scan was cached"
    b_db, a_db = before.get("db_mtime_ns"), after.get("db_mtime_ns")
    if b_db is not None and a_db is not None and b_db != a_db:
        return "the index was rewritten after this scan was cached"
    b_sha, a_sha = before.get("live_sha"), after.get("live_sha")
    if b_sha and a_sha and b_sha != a_sha:
        return (
            "the checkout moved to a different commit after this scan was cached, "
            f"so the scan describes {b_sha[:12]} and the question is about {a_sha[:12]}"
        )
    b_tree, a_tree = before.get("tree"), after.get("tree")
    if b_tree and a_tree and b_tree != a_tree:
        return (
            "the working tree changed after this scan was cached, so the target may "
            "sit in an edit the scan never saw"
        )
    return None


def revalidate_verdict(verdict: Optional[dict], reason: str) -> None:
    """Apply a failed revalidation to a verdict being replayed from cache.

    Disclose on every state, refuse only the absence CLAIM — the same shape as
    the stale, truncated, rebuilding and partial gates. Results a cached `ok`
    scan returned were really in the index at that generation and are still the
    best available answer; only the claim that nothing exists is unfounded.

    Any evidence token minted on the earlier pass is stripped, because it was
    issued against the state that just failed to hold.
    """
    if not isinstance(verdict, dict):
        return
    verdict["revalidated"] = {"stale_cache": True, "reason": reason}
    verdict.pop("evidence_ref", None)
    verdict.pop("absence_citable", None)
    verdict.pop("absence_blocked_by", None)
    if verdict.get("state") == "absent":
        verdict["state"] = "degraded"
        verdict["note"] = (
            f"This result was replayed from cache, and {reason}. Absence is NOT "
            "proven against the current state; re-run the search to get a scan of it."
        )
