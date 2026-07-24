"""Unified retrieval verdict — one honesty contract across the search tools.

An empty or weak retrieval result is positive, token-saving evidence: grounded
symbolic retrieval can prove "this is not here" where nearest-neighbour search
always returns its closest something. ``build_verdict`` centralises the logic
that ``search_symbols`` and ``get_ranked_context`` previously duplicated, and
extends it to ``search_text``.

The result carries two things:

* ``verdict`` — the unified ``_meta.verdict`` dict with a complete taxonomy
  (``ok`` / ``low_confidence`` / ``absent`` / ``degraded``), the scan counts that
  back an absence claim, per-channel status, and near-miss suggestions.
* ``negative_evidence`` — the legacy dict (or ``None``) with the same trigger and
  shape as before, so existing consumers and the injected agent policy keep
  working unchanged.
"""

from __future__ import annotations

from typing import Optional, Sequence


def index_truncation_meta(cap: Optional[dict]) -> Optional[dict]:
    """Query-time ``_meta.index_truncated`` block from a persisted cap status (#366).

    ``cap`` is ``CodeIndex.file_cap_status``. Returns None unless the index was
    truncated by the max_folder_files walk cap, in which case it warns that whole
    files are missing from the corpus these results were drawn from — so an empty
    or thin result may be truncation, not genuine absence.
    """
    if not cap or not cap.get("truncated"):
        return None
    return {
        "truncated": True,
        "files_discovered": cap.get("files_discovered"),
        "files_indexed": cap.get("files_indexed"),
        "files_skipped_cap": cap.get("files_skipped_cap"),
        "max_folder_files": cap.get("max_folder_files"),
        "note": (
            "This index is incomplete: the max_folder_files cap dropped "
            f"{cap.get('files_skipped_cap')} file(s) at index time, so entire files "
            "are absent from search. A missing or thin result may be truncation, not "
            "absence. Raise max_folder_files in config.jsonc (or set "
            "JCODEMUNCH_MAX_FOLDER_FILES) and re-index."
        ),
    }


# Version pin for the confidence/score heuristics the verdict reports.
# Bump whenever the scoring formula (BM25 blending, thresholds, seeding
# floors) changes, so calibration claims tie to the scorer that produced
# them: "0.8 under scorer v1" is a measurable statement, "0.8" is not.
SCORER_VERSION = 1


def index_coverage_meta(index) -> Optional[dict]:
    """Query-time coverage disclosure backing an absence claim.

    Pulls the persisted coverage contract (recorded at the last full
    discovery walk) plus generation metadata off the index. Returns None when
    the index predates coverage recording — absence of the block means
    "coverage unknown", never "nothing was excluded".
    """
    cov = getattr(index, "coverage", None)
    if not isinstance(cov, dict) or not cov:
        return None
    out: dict = {
        "generation": {
            "indexed_at": getattr(index, "indexed_at", "") or None,
            "index_version": getattr(index, "index_version", None),
        },
        "files_indexed": cov.get("files_indexed"),
    }
    head = getattr(index, "git_head", "") or ""
    if head:
        out["generation"]["git_head"] = head[:12]
    scopes = getattr(index, "source_roots", None)
    if scopes:
        out["included_scopes"] = scopes
    skips = cov.get("skip_counts") or {}
    if skips:
        out["excluded"] = skips
    if cov.get("no_symbols_count"):
        out["no_symbols_files"] = cov["no_symbols_count"]
    return out


def _attach_coverage(verdict: dict, coverage: Optional[dict]) -> None:
    """Attach coverage disclosure to absent/degraded verdicts (in place).

    Only the states where "what wasn't scanned" changes the meaning of the
    result carry the block; ok/low_confidence stay lean.
    """
    if coverage and verdict.get("state") in (STATE_ABSENT, STATE_DEGRADED):
        verdict["coverage"] = coverage


# Emitted as verdict["state"].
STATE_OK = "ok"
STATE_LOW_CONFIDENCE = "low_confidence"
STATE_ABSENT = "absent"
STATE_DEGRADED = "degraded"

_NOTES = {
    STATE_OK: "Confident matches returned.",
    STATE_LOW_CONFIDENCE: (
        "Matches are below the confidence threshold; verify before relying on them."
    ),
    STATE_ABSENT: (
        "No match found after scanning the index. Treat this as strong evidence the "
        "target is not present; do not reformulate the same query expecting a hit."
    ),
    STATE_DEGRADED: (
        "A requested retrieval channel was unavailable or the scan was cut short. "
        "Results are partial and absence is NOT proven."
    ),
}


def _semantic_provider_available() -> bool:
    """Return True when an embedding provider is actually configured.

    Reuses ``embed_repo``'s live detection so we do not drift from the encoder the
    semantic path would really use. Called only when semantic was requested.
    """
    try:
        from ..tools.embed_repo import _detect_provider

        detected = _detect_provider()
        if isinstance(detected, tuple):
            return bool(detected and detected[0])
        return bool(detected)
    except Exception:
        return False


def _did_you_mean(
    source_files: Optional[Sequence[str]],
    query_terms: Optional[Sequence[str]],
    cap: int = 5,
) -> list:
    """Files whose basename contains a query term (near-miss candidates)."""
    if not source_files or not query_terms:
        return []
    out: list = []
    seen: set = set()
    for f in source_files:
        base = f.lower().replace("\\", "/").rsplit("/", 1)[-1]
        if any(t in base for t in query_terms):
            if f not in seen:
                seen.add(f)
                out.append(f)
                if len(out) >= cap:
                    break
    return out


def build_verdict(
    *,
    result_count: int,
    scanned_symbols: int = 0,
    scanned_files: int = 0,
    best_score: Optional[float] = None,
    threshold: Optional[float] = None,
    query_terms: Optional[Sequence[str]] = None,
    source_files: Optional[Sequence[str]] = None,
    semantic_requested: bool = False,
    index_stale: bool = False,
    index_changed: bool = False,
    timed_out: bool = False,
    coverage: Optional[dict] = None,
) -> dict:
    """Compute the unified verdict plus the legacy negative_evidence dict.

    Returns ``{"verdict": <_meta.verdict>, "negative_evidence": <dict|None>}``.

    Backward compatibility: ``negative_evidence`` fires on exactly the historical
    trigger (empty result, or best score below threshold) with the historical keys
    and verdict names, so existing tests and the agent policy are unaffected. The
    new ``verdict`` is purely additive.
    """
    terms = [t for t in (query_terms or []) if t]
    did_you_mean = _did_you_mean(source_files, terms)

    semantic_available = _semantic_provider_available() if semantic_requested else True
    below_threshold = (
        threshold is not None and best_score is not None and best_score < threshold
    )

    # --- unified state (degraded takes precedence: partial scans can't prove absence) ---
    if timed_out:
        state = STATE_DEGRADED
    elif semantic_requested and not semantic_available:
        state = STATE_DEGRADED
    elif result_count == 0 and index_changed:
        # The index was rewritten underneath this scan, so "we looked and it is
        # not there" describes a tree that was moving while we read it — the
        # target may sit in rows written after we passed them. Same reasoning
        # as the stale and truncated gates: degraded cannot prove absence, so
        # the absence-evidence refusal rule falls out of the existing
        # "only `absent` proves absence" check with no new rule to keep in sync.
        # Deliberately scoped to the absence path: a scan that RETURNED results
        # still returns them (they were really in the index) and only discloses
        # the rebuild via channels.index below.
        state = STATE_DEGRADED
    elif result_count == 0:
        state = STATE_ABSENT
    elif below_threshold:
        state = STATE_LOW_CONFIDENCE
    else:
        state = STATE_OK

    if semantic_requested and not semantic_available:
        semantic_channel = "unavailable"
    elif semantic_requested:
        semantic_channel = "ok"
    else:
        semantic_channel = "off"

    verdict = {
        "state": state,
        "scanned": {"symbols": int(scanned_symbols), "files": int(scanned_files)},
        "best_score": round(best_score, 3) if best_score is not None else None,
        "channels": {
            "lexical": "ok",
            "semantic": semantic_channel,
            # "rebuilding" is disclosed on EVERY state, not just the degraded
            # one above: a caller reading an `ok` result still deserves to know
            # the index moved under it. Only the absence CLAIM is refused.
            "index": (
                "rebuilding" if index_changed else ("stale" if index_stale else "fresh")
            ),
        },
        "scorer": SCORER_VERSION,
        "note": _NOTES[state],
    }
    if did_you_mean:
        verdict["did_you_mean"] = did_you_mean
    _attach_coverage(verdict, coverage)

    # --- legacy negative_evidence: unchanged trigger + shape ---
    negative_evidence = None
    if result_count == 0 or below_threshold:
        negative_evidence = {
            "verdict": (
                "no_implementation_found" if result_count == 0 else "low_confidence_matches"
            ),
            "scanned_symbols": int(scanned_symbols),
            "scanned_files": int(scanned_files),
            "best_match_score": round(best_score, 3) if best_score else 0.0,
        }
        if did_you_mean:
            negative_evidence["related_existing"] = did_you_mean

    return {"verdict": verdict, "negative_evidence": negative_evidence}


def suggest_paths(
    requested_path: Optional[str],
    source_files: Optional[Sequence[str]],
    cap: int = 5,
) -> list:
    """Indexed paths that plausibly match a missing ``requested_path``.

    Exact-basename matches in a different directory come first (the agent had
    the filename right, the directory wrong), then stem substring matches. The
    requested path itself is never suggested.
    """
    if not requested_path or not source_files:
        return []
    req = str(requested_path).replace("\\", "/")
    req_base = req.rsplit("/", 1)[-1].lower()
    req_stem = req_base.rsplit(".", 1)[0] if "." in req_base else req_base
    exact: list = []
    partial: list = []
    seen: set = set()
    for f in source_files:
        norm = str(f).replace("\\", "/")
        if norm == req or f in seen:
            continue
        base = norm.rsplit("/", 1)[-1].lower()
        stem = base.rsplit(".", 1)[0] if "." in base else base
        if base == req_base:
            exact.append(f)
            seen.add(f)
        elif req_stem and len(req_stem) >= 3 and (req_stem in stem or stem in req_stem):
            partial.append(f)
            seen.add(f)
    return (exact + partial)[:cap]


def _symbol_name_of(symbol_id: Optional[str]) -> str:
    """Bare name from a symbol id like ``path::Name#kind`` (or a plain name)."""
    if not symbol_id:
        return ""
    s = str(symbol_id)
    if "::" in s:
        s = s.rsplit("::", 1)[-1]
    if "#" in s:
        s = s.split("#", 1)[0]
    return s.lower()


def suggest_symbol_ids(
    requested_id: Optional[str],
    symbols: Optional[Sequence[dict]],
    cap: int = 5,
) -> list:
    """Indexed symbol ids whose name matches a missing ``requested_id``.

    Same-name symbols (right name, wrong file/kind) rank ahead of substring
    matches. Operates on the index's raw symbol dicts.
    """
    name = _symbol_name_of(requested_id)
    if not name or not symbols:
        return []
    exact: list = []
    partial: list = []
    seen: set = set()
    for s in symbols:
        sid = s.get("id")
        if not sid or sid == requested_id or sid in seen:
            continue
        sname = str(s.get("name", "")).lower()
        if not sname:
            continue
        if sname == name:
            exact.append(sid)
            seen.add(sid)
        elif len(name) >= 3 and (name in sname or sname in name):
            partial.append(sid)
            seen.add(sid)
        if len(exact) >= cap:
            break
    return (exact + partial)[:cap]


def build_file_verdict(
    *,
    present: bool,
    requested_path: Optional[str] = None,
    source_files: Optional[Sequence[str]] = None,
    index_stale: bool = False,
    empty_symbols: bool = False,
) -> dict:
    """`_meta.verdict` for the file-read tools.

    * ``present=False`` — the path is not in the index: ``absent`` plus a
      ``did_you_mean`` list of near-miss paths.
    * ``present=True, empty_symbols=True`` — the file is indexed but yields no
      symbols (data/config file, or constructs the parser does not surface):
      ``absent`` with no suggestions, so the agent does not retry the outline.
    * otherwise — ``ok``.
    """
    if not present:
        state = STATE_ABSENT
        note = "Path is not in the index. " + _NOTES[STATE_ABSENT]
        suggestions = suggest_paths(requested_path, source_files)
    elif empty_symbols:
        state = STATE_ABSENT
        note = (
            "File is indexed but exposes no extractable symbols (a data/config "
            "file, or constructs the parser does not surface). Re-requesting the "
            "outline will not change this."
        )
        suggestions = []
    else:
        state = STATE_OK
        note = _NOTES[STATE_OK]
        suggestions = []
    verdict = {
        "state": state,
        "channels": {"index": "stale" if index_stale else "fresh"},
        "note": note,
    }
    if suggestions:
        verdict["did_you_mean"] = suggestions
    return verdict


def symbol_verdict_for_index(
    index,
    *,
    found_count: int,
    requested_id: Optional[str] = None,
) -> dict:
    """Index-aware wrapper over :func:`build_symbol_verdict`."""
    verdict = build_symbol_verdict(
        found_count=found_count,
        requested_id=requested_id,
        symbols=getattr(index, "symbols", None) if found_count == 0 else None,
        index_stale=_index_is_stale(index),
    )
    _attach_coverage(verdict, index_coverage_meta(index))
    return verdict


def _index_source_files(index) -> list:
    """Best-effort list of indexed source paths (keys of ``file_languages``)."""
    langs = getattr(index, "file_languages", None)
    if isinstance(langs, dict):
        return list(langs.keys())
    return []


def index_changed_since_load(index) -> bool:
    """Whether the .db was rewritten since this index was loaded (never raises).

    Answers a question ``_index_is_stale`` cannot: that probe compares the
    stored git SHA against live HEAD, so it sees a repo that moved ON DISK but
    is blind to a reindex of an UNCHANGED tree — a watcher rebuild after an
    uncommitted edit reports ``fresh`` while rows are being rewritten.

    Deliberately a filesystem signal, not ``reindex_state``: that module is
    in-memory and per-process, so a server answering a search cannot see a
    reindex driven by a separate ``watch-all`` service. The .db/.db-wal mtime
    crosses process boundaries.

    Unknown is NOT changed: an index with no stamped provenance (a test double,
    a hand-built CodeIndex) returns False rather than degrading every verdict.
    """
    try:
        from pathlib import Path

        from ..storage.sqlite_store import _db_mtime_ns

        db_path = getattr(index, "_db_path", None)
        loaded_at = getattr(index, "_loaded_mtime_ns", None)
        if not db_path or loaded_at is None:
            return False
        return _db_mtime_ns(Path(db_path)) != int(loaded_at)
    except Exception:
        return False


def _index_is_stale(index) -> bool:
    """Whether the index SHA lags the live git HEAD (never raises)."""
    try:
        from .freshness import FreshnessProbe

        probe = FreshnessProbe(
            source_root=getattr(index, "source_root", "") or None,
            indexed_at=getattr(index, "indexed_at", ""),
            index_sha=getattr(index, "git_head", None),
            file_mtimes=getattr(index, "file_mtimes", None),
        )
        return probe.repo_is_stale
    except Exception:
        return False


def file_verdict_for_index(
    index,
    *,
    present: bool,
    requested_path: Optional[str] = None,
    empty_symbols: bool = False,
) -> dict:
    """Index-aware wrapper over :func:`build_file_verdict` for the file tools."""
    verdict = build_file_verdict(
        present=present,
        requested_path=requested_path,
        source_files=_index_source_files(index) if not present else None,
        index_stale=_index_is_stale(index),
        empty_symbols=empty_symbols,
    )
    _attach_coverage(verdict, index_coverage_meta(index))
    return verdict


def build_symbol_verdict(
    *,
    found_count: int,
    requested_id: Optional[str] = None,
    symbols: Optional[Sequence[dict]] = None,
    index_stale: bool = False,
) -> dict:
    """`_meta.verdict` for ``get_symbol_source``.

    ``found_count == 0`` yields ``absent`` plus ``did_you_mean`` symbol ids that
    share the requested name; any resolved symbol yields ``ok`` (a partial batch
    is still a hit).
    """
    if found_count == 0:
        state = STATE_ABSENT
        note = "Symbol id is not in the index. " + _NOTES[STATE_ABSENT]
        suggestions = suggest_symbol_ids(requested_id, symbols)
    else:
        state = STATE_OK
        note = _NOTES[STATE_OK]
        suggestions = []
    verdict = {
        "state": state,
        "channels": {"index": "stale" if index_stale else "fresh"},
        "note": note,
    }
    if suggestions:
        verdict["did_you_mean"] = suggestions
    return verdict
