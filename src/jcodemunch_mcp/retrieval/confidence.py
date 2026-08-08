"""Calibrated retrieval confidence (v1.75.0).

A single 0-1 score that summarizes how trustworthy a ranked result list is.
Designed so an agent can read ``_meta.confidence`` and decide whether to
follow up with ``get_symbol_source`` (high confidence) or widen the search
/ ask the user (low confidence).

Inputs are deliberately schema-agnostic — caller passes a list of entries
with at least a ``score`` field. We never reach into BM25/semantic
internals so this stays cheap and decoupled from the ranking pipeline.

⚠⚠ **Schema-agnostic is not SCALE-agnostic, and through v1.108.264 this
module conflated the two.** The ``strength`` sub-signal squashes a RAW
top-1 score, and the curve was hardcoded to the BM25 scale for every
caller. BM25 scores in the tens; a WRR fused score tops out at
``sum(weights)/(k+1)`` — about 0.049 for three unit-weight channels; a
cosine tops out at 1.0. Measured on identical relative separation between
top-1 and top-2:

    BM25   top1=20.0    strength 0.9933   confidence 0.223
    fused  top1=0.0492  strength 0.0122   confidence 0.048
    cosine top1=0.82    strength 0.1854   confidence 0.356

Same ranking quality, graded four to five times worse for its units.

Two consumers made that expensive rather than cosmetic:

  - ``verdict.STATE_LOW_CONFIDENCE`` gates whether a scan may assert an
    answer, so fusion and semantic searches were being downgraded for
    arithmetic rather than for evidence.
  - ``retrieval.tuning.WeightTuner`` bumps ``semantic_weight`` by comparing
    mean confidence between ``semantic_used`` groups. The scale gap dwarfs
    the ±0.05 decision threshold, so the tuner read "semantic hurts" and
    stepped the weight DOWN toward its floor on every mixed-mode ledger —
    the same defect measured end to end in jdocmunch #106 before the port.

Every caller now passes the ceiling of whichever scorer produced its
scores. ⚠ The BM25 curve is UNCHANGED — ``1-exp(-3t/12)`` is algebraically
identical to the old ``1-exp(-t/4)`` — so only the callers that were wrong
move, and the default keeps an un-updated caller exactly where it was.
"""

from __future__ import annotations

from typing import Iterable, Optional

#: Score at which `strength` reaches 1-exp(-3) ~= 0.95, per scorer.
#: BM25 keeps its historical value. Cosine is bounded at 1.0 by definition.
#: For WRR fusion, ask `signal_fusion.fused_score_ceiling(channels, ...)` —
#: it depends on which channels ran and cannot be a constant here.
BM25_CEILING = 12.0
COSINE_CEILING = 1.0


def compute_confidence(
    scored_results: list[dict],
    *,
    is_stale: bool = False,
    has_identity_match: Optional[bool] = None,
    score_field: str = "score",
    score_ceiling: float = BM25_CEILING,
) -> dict:
    """Return ``{"confidence": float, "components": {...}}``.

    The confidence number is the product of four 0-1 signals:
      * **gap**       — top-1 vs top-2 relative score gap (1.0 = top result
                        dominates; near 0 = many results tied at the top).
      * **strength**  — soft squash of the top-1 score against
                        ``score_ceiling``, the scale of the scorer that
                        produced it. A score of 0 yields 0. ⚠ Pass the
                        right ceiling or this grades units, not quality;
                        see the module docstring.
      * **identity**  — 1.0 if any of the top results was an exact-name
                        identity match; otherwise 0.7 (no penalty when
                        unknown).
      * **freshness** — 1.0 fresh, 0.6 stale.

    Returns the components alongside the final number so debug clients
    can see *why* a number was low.
    """
    components = {
        "gap": 0.0,
        "strength": 0.0,
        "identity": 1.0 if has_identity_match else (0.7 if has_identity_match is None else 0.6),
        "freshness": 0.6 if is_stale else 1.0,
    }

    if not scored_results:
        return {"confidence": 0.0, "components": components}

    scores = [_extract_score(r, score_field) for r in scored_results]
    scores = [s for s in scores if s is not None]
    if not scores:
        # Results exist but no scores attached — neutral mid-confidence
        components["gap"] = 0.5
        components["strength"] = 0.5
        confidence = _combine(components)
        return {"confidence": confidence, "components": components}

    top1 = scores[0]
    top2 = scores[1] if len(scores) > 1 else 0.0

    # Relative gap: how dominant is the top-1?
    if top1 <= 0:
        components["gap"] = 0.0
    else:
        components["gap"] = max(0.0, min(1.0, (top1 - top2) / top1))

    # Strength: saturates at ~0.95 when top1 reaches the scorer's ceiling.
    # ⚠ For BM25 (ceiling 12) this is EXACTLY the historical `1-exp(-t/4)`.
    _ceiling = score_ceiling if score_ceiling > 0 else BM25_CEILING
    components["strength"] = 1.0 - _approx_exp(-3.0 * top1 / _ceiling)
    components["strength"] = max(0.0, min(1.0, components["strength"]))

    # If caller didn't pass has_identity_match, sniff for it
    if has_identity_match is None:
        sniffed = any(
            (r.get("identity") or r.get("identity_match") or False)
            for r in scored_results[:3]
            if isinstance(r, dict)
        )
        if sniffed:
            components["identity"] = 1.0

    confidence = _combine(components)
    return {"confidence": confidence, "components": components}


def _combine(components: dict) -> float:
    # Weighted geometric mean — any single weak signal pulls the number down.
    weights = {"gap": 0.35, "strength": 0.35, "identity": 0.15, "freshness": 0.15}
    log_sum = 0.0
    for k, w in weights.items():
        v = max(1e-6, float(components.get(k, 0.0)))
        log_sum += w * _approx_log(v)
    return round(_approx_exp(log_sum), 3)


def _extract_score(entry, field: str) -> Optional[float]:
    if not isinstance(entry, dict):
        return None
    val = entry.get(field)
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _approx_exp(x: float) -> float:
    """math.exp without importing math at module load (kept tiny)."""
    import math
    return math.exp(x)


def _approx_log(x: float) -> float:
    import math
    return math.log(x)


def attach_confidence(
    result: dict,
    scored_results: Optional[Iterable[dict]] = None,
    *,
    is_stale: bool = False,
    has_identity_match: Optional[bool] = None,
    include_components: bool = False,
    score_ceiling: float = BM25_CEILING,
) -> dict:
    """Mutate ``result`` to include ``_meta.confidence`` (and optionally
    ``_meta.confidence_components``). Returns ``result`` for convenience.

    If ``scored_results`` is None, defaults to ``result["results"]``.
    """
    if scored_results is None:
        scored_results = result.get("results", []) or []
    payload = compute_confidence(
        list(scored_results),
        is_stale=is_stale,
        has_identity_match=has_identity_match,
        score_ceiling=score_ceiling,
    )
    meta = result.setdefault("_meta", {})
    meta["confidence"] = payload["confidence"]
    if include_components:
        meta["confidence_components"] = payload["components"]
    return result


def extract_ledger_features(scored_results: list[dict]) -> dict:
    """Pull (top1_score, top2_score, identity_hit) out of a result list.

    Used by the v1.78.0 ranking ledger so each retrieval tool can record
    a uniform feature set without duplicating the score-extraction logic.
    """
    top1 = None
    top2 = None
    if scored_results:
        s0 = scored_results[0].get("score") if isinstance(scored_results[0], dict) else None
        try:
            top1 = float(s0) if s0 is not None else None
        except (TypeError, ValueError):
            top1 = None
        if len(scored_results) > 1:
            s1 = scored_results[1].get("score") if isinstance(scored_results[1], dict) else None
            try:
                top2 = float(s1) if s1 is not None else None
            except (TypeError, ValueError):
                top2 = None
    identity_hit = any(
        bool(r.get("identity") or r.get("identity_match"))
        for r in scored_results[:3]
        if isinstance(r, dict)
    )
    return {
        "top1_score": top1,
        "top2_score": top2,
        "identity_hit": identity_hit,
    }
