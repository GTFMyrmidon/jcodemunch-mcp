"""Retrieval confidence grades ranking quality, not units.

Ported finding from jdocmunch #106 — but ported as a CONTRACT, not as code.
jcm's fusion weights are NOT normalized to sum to 1 (jdoc's are), so jdoc's
`1/(k+1)` constant would have been the wrong number here even though the
defect is identical. The ceiling has to come from the actual weight vector.

`compute_confidence`'s `strength` sub-signal squashes a RAW top-1 score, and
the curve was hardcoded to the BM25 scale for every caller. Measured on
identical relative separation between top-1 and top-2:

    BM25   top1=20.0    strength 0.9933   confidence 0.223
    fused  top1=0.0492  strength 0.0122   confidence 0.048
    cosine top1=0.82    strength 0.1854   confidence 0.356

Two consumers made that expensive rather than cosmetic:

  - `verdict.STATE_LOW_CONFIDENCE` gates whether a scan may assert an answer.
  - `WeightTuner` bumps `semantic_weight` on the mean-confidence difference
    between `semantic_used` groups. The scale gap dwarfs its ±0.05 threshold,
    so it read "semantic hurts" and stepped the weight DOWN on every
    mixed-mode ledger — measured end to end in jdoc before this port.

⚠ Four scales reach this function in jcm, not two: BM25, WRR fusion, cosine
(and the [0,1] hybrid blend), and PageRank via `sort_by="centrality"`.
"""

import math

import pytest

from jcodemunch_mcp.retrieval import confidence as conf
from jcodemunch_mcp.retrieval.signal_fusion import (
    ChannelResult,
    DEFAULT_SMOOTHING,
    fuse,
    fused_score_ceiling,
)


def _rows(top1, top2):
    return [{"score": top1}, {"score": top2}, {"score": top2 * 0.9}]


# ---------------------------------------------------------------------------
# The BM25 path must not move
# ---------------------------------------------------------------------------

def test_bm25_curve_is_algebraically_unchanged():
    """`1-exp(-3t/12)` IS the historical `1-exp(-t/4)`. If this fails, every
    lexical confidence in the product just moved."""
    for top1 in (0.5, 2.0, 5.6, 12.0, 20.0, 40.0):
        got = conf.compute_confidence(
            [{"score": top1}], score_ceiling=conf.BM25_CEILING
        )["components"]["strength"]
        assert got == pytest.approx(1.0 - math.exp(-top1 / 4.0))


def test_default_ceiling_is_bm25():
    """An un-updated caller must be unchanged, not newly wrong."""
    import inspect

    assert inspect.signature(
        conf.compute_confidence
    ).parameters["score_ceiling"].default == conf.BM25_CEILING
    assert inspect.signature(
        conf.attach_confidence
    ).parameters["score_ceiling"].default == conf.BM25_CEILING


def test_nonpositive_ceiling_falls_back_to_bm25():
    a = conf.compute_confidence([{"score": 5.0}], score_ceiling=0.0)
    b = conf.compute_confidence([{"score": 5.0}], score_ceiling=conf.BM25_CEILING)
    assert a["components"]["strength"] == b["components"]["strength"]


# ---------------------------------------------------------------------------
# The ceiling must equal what the scorer can actually produce
# ---------------------------------------------------------------------------

def _channels(n, weight=None):
    ids = [f"s{i}" for i in range(10)]
    names = ["lexical", "identity", "similarity", "structural"][:n]
    return [ChannelResult(name=nm, ranked_ids=ids, weight=weight, raw_scores={})
            for nm in names]


@pytest.mark.parametrize("n_channels", [1, 2, 3, 4])
def test_ceiling_equals_a_perfect_consensus_score(n_channels):
    """⚠⚠ The anti-drift test. A symbol ranked 1st in every channel scores
    exactly the ceiling — so `fused_score_ceiling` and `fuse` cannot disagree
    about weight resolution without this going red."""
    chans = _channels(n_channels, weight=1.0)
    top = fuse(chans)[0].score
    assert top == pytest.approx(fused_score_ceiling(chans))


def test_ceiling_tracks_explicit_weight_overrides():
    chans = _channels(2, weight=None)
    w = {"lexical": 2.0, "identity": 0.5}
    top = fuse(chans, weights=w)[0].score
    assert top == pytest.approx(fused_score_ceiling(chans, weights=w))


def test_ceiling_honours_a_literal_zero_weight():
    """A channel explicitly weighted 0.0 contributes nothing and must not
    inflate the ceiling."""
    chans = [
        ChannelResult(name="lexical", ranked_ids=["a"], weight=1.0, raw_scores={}),
        ChannelResult(name="similarity", ranked_ids=["a"], weight=0.0, raw_scores={}),
    ]
    assert fused_score_ceiling(chans) == pytest.approx(1.0 / (DEFAULT_SMOOTHING + 1))
    assert fuse(chans)[0].score == pytest.approx(fused_score_ceiling(chans))


def test_ceiling_is_not_jdocs_constant():
    """⚠ jdoc normalizes its weights to sum to 1, so its ceiling is
    `1/(k+1)`. Copying that constant here would be wrong by the channel
    count — the exact suite-parity trap this port had to avoid."""
    chans = _channels(3, weight=1.0)
    jdoc_constant = 1.0 / (DEFAULT_SMOOTHING + 1)
    assert fused_score_ceiling(chans) == pytest.approx(3 * jdoc_constant)
    assert fused_score_ceiling(chans) != pytest.approx(jdoc_constant)


def test_empty_or_zero_weight_channels_give_zero_ceiling():
    assert fused_score_ceiling([]) == 0.0
    chans = [ChannelResult(name="lexical", ranked_ids=["a"], weight=0.0, raw_scores={})]
    assert fused_score_ceiling(chans) == 0.0


def test_zero_ceiling_degrades_to_bm25_not_to_a_crash():
    """A zero ceiling reaches compute_confidence when every channel is
    weighted out; it must not divide by zero."""
    out = conf.compute_confidence([{"score": 0.01}], score_ceiling=0.0)
    assert 0.0 <= out["confidence"] <= 1.0


# ---------------------------------------------------------------------------
# Equal quality, equal verdict
# ---------------------------------------------------------------------------

def test_identical_separation_scores_alike_across_scalers():
    chans = _channels(3, weight=1.0)
    ceiling = fused_score_ceiling(chans)

    lex = conf.compute_confidence(
        _rows(20.0, 14.0), score_ceiling=conf.BM25_CEILING
    )["confidence"]
    fus = conf.compute_confidence(
        _rows(ceiling, ceiling * 0.7), score_ceiling=ceiling
    )["confidence"]
    cos = conf.compute_confidence(
        _rows(0.95, 0.665), score_ceiling=conf.COSINE_CEILING
    )["confidence"]

    assert fus == pytest.approx(lex, abs=0.15), f"lexical {lex} vs fused {fus}"
    assert cos == pytest.approx(lex, abs=0.15), f"lexical {lex} vs cosine {cos}"


def test_fusion_confidence_was_crushed_on_the_bm25_curve():
    """Pins the size of the bug so the fix cannot quietly regress."""
    chans = _channels(3, weight=1.0)
    ceiling = fused_score_ceiling(chans)
    rows = _rows(ceiling, ceiling * 0.7)
    old = conf.compute_confidence(rows, score_ceiling=conf.BM25_CEILING)["confidence"]
    new = conf.compute_confidence(rows, score_ceiling=ceiling)["confidence"]
    # Measured: 0.133 -> 0.611 at this separation. The penalty grows as the
    # top-1/top-2 gap narrows (a tighter fixture measured 0.048 -> 0.55).
    assert old < 0.15
    assert new > 0.50
    assert new / old > 4


def test_a_strong_cosine_is_not_a_weak_hit():
    weak = conf.compute_confidence(
        _rows(0.82, 0.55), score_ceiling=conf.BM25_CEILING
    )["components"]["strength"]
    real = conf.compute_confidence(
        _rows(0.82, 0.55), score_ceiling=conf.COSINE_CEILING
    )["components"]["strength"]
    assert weak < 0.25
    assert real > 0.85


def test_pagerank_centrality_needs_its_own_ceiling():
    """`sort_by="centrality"` ranks by PageRank, which sums to 1 across FILES.
    The most central file in a repo lands near 0.05."""
    top_pr = 0.05
    on_bm25 = conf.compute_confidence(
        _rows(top_pr, top_pr * 0.7), score_ceiling=conf.BM25_CEILING
    )["components"]["strength"]
    on_pr = conf.compute_confidence(
        _rows(top_pr, top_pr * 0.7), score_ceiling=top_pr
    )["components"]["strength"]
    assert on_bm25 < 0.02, "the most central symbol in the repo, graded at ~zero"
    assert on_pr > 0.9


# ---------------------------------------------------------------------------
# The call sites actually pass a ceiling
# ---------------------------------------------------------------------------

def test_every_non_bm25_call_site_passes_a_ceiling():
    """⚠ A guard, not a habit. Any `attach_confidence` call on a fused,
    cosine or centrality score must name its scale; BM25 callers may rely on
    the default. Fails if a new scorer is wired in without one."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1] / "src" / "jcodemunch_mcp"
    offenders = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"_attach_confidence\(", text):
            # Grab the call's argument text up to its closing paren.
            chunk = text[m.start(): m.start() + 600]
            head = chunk.split("\n\n")[0]
            scale_hint = re.search(r"fusion_score|_fused_ceiling|COSINE|centrality", head)
            if scale_hint and "score_ceiling" not in head:
                offenders.append(f"{path.name}: {head.splitlines()[0].strip()}")
    assert not offenders, "non-BM25 confidence call with no ceiling: " + "; ".join(offenders)
