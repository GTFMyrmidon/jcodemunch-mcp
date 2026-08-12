"""Which ``ranking_events`` labels can be believed (v1.108.186).

The ledger is append-only history. When a producer records a column wrongly,
the fix stops NEW rows from lying but cannot make the OLD rows honest, and
every consumer that reads the column keeps reading the lie until the recency
window ages it out. This module is the one place that says which labels are
untrustworthy, so the three consumers (``retrieval.tuning``,
``retrieval.regret``, ``tools.analyze_perf``) share a single rule rather than
three copies drifting apart.

**The mislabel this exists for.** Until v1.108.186,
``_get_ranked_context_fusion`` recorded ``semantic_used=1`` on every row while
building only the lexical, identity and structural channels — never a
similarity channel. Its own ``_meta.channels`` listed the three, and from
v1.108.185 its verdict reported ``channels.semantic: "off"``, so the response
and the ledger disagreed about the same call.

**Why the rows are exactly identifiable and no heuristic is needed.** Those
rows carry ``tool = "get_ranked_context_fusion"``, a value no other producer
writes. A pre-fix row is therefore precisely
``(tool == "get_ranked_context_fusion", semantic_used == 1)``, and a post-fix
row from the same exit is the same tool with ``semantic_used == 0``.

**The label is UNKNOWN, not False.** An untrusted row is not evidence that
semantic was off; it is evidence that this row cannot say. Consumers put it in
a third bucket and disclose the count rather than folding it into either side
— treating unknown as False would be the same class of error in the opposite
direction.

⚠ **This rule expires when the exit gains a similarity channel.** If
``_get_ranked_context_fusion`` ever builds one, its ``semantic_used=1`` rows
become honest and this predicate would start discarding good evidence.
``tests/test_v1_108_186.py`` carries a drift guard that fails if that exit
grows a similarity channel while this rule is still unconditional.
"""

from __future__ import annotations

from typing import Sequence

# Column indices into a ranking_events row tuple, as SELECTed by
# ``token_tracker.ranking_db_query``:
#   0 ts  1 repo  2 tool  3 query_hash  4 query  5 returned_ids
#   6 top1_score  7 top2_score  8 confidence  9 semantic_used
#   10 identity_hit  11 repo_is_stale
_TOOL = 2
_RETURNED_IDS = 5
_TOP1_SCORE = 6
_SEMANTIC_USED = 9

# (tool, semantic_used-as-bool) pairs whose semantic label is not evidence.
# Keep this a frozenset of exact pairs: a tool-only rule would also discard the
# honest post-fix rows, which are the ones a consumer most wants to read.
_UNTRUSTED_SEMANTIC_LABELS = frozenset({
    # v1.108.186. Pre-fix rows from get_ranked_context's fusion exit. The exit
    # built no similarity channel, so a `1` here reflects a hardcoded argument
    # rather than anything the search did.
    ("get_ranked_context_fusion", True),
})

# The tool whose pre-fix rows the rule above covers. Exported so the drift
# guard names it once rather than restating the string.
MISLABELLED_FUSION_TOOL = "get_ranked_context_fusion"


def semantic_label_is_trustworthy(row: Sequence) -> bool:
    """True when ``row``'s ``semantic_used`` column is evidence of anything.

    A short row (fewer columns than the ledger SELECT returns) is treated as
    trustworthy: this predicate exists to refuse a KNOWN lie, and refusing rows
    it cannot classify would turn a shape surprise into silent data loss.
    """
    try:
        tool = row[_TOOL]
        semantic_used = bool(row[_SEMANTIC_USED])
    except (IndexError, TypeError, KeyError):
        return True
    return (tool, semantic_used) not in _UNTRUSTED_SEMANTIC_LABELS


def identity_label_is_trustworthy(row: Sequence) -> bool:
    """True when ``row``'s ``identity_hit`` column is evidence of anything.

    v1.108.187. Until then, `_get_ranked_context_fusion` passed no
    ``extract_ledger_features`` payload at all, so ``top1_score``, ``top2_score``
    and ``identity_hit`` were recorded as their defaults on every row it wrote.

    **The discriminator is different from the semantic one, and weaker on purpose.**
    ``identity_hit`` has no value a pre-fix row alone can carry — pre-fix is always
    `0`, and `0` is also a perfectly honest post-fix answer, so matching on the
    column would discard good evidence forever. What IS exact: a row that returned
    symbols while recording no ``top1_score`` can only have come from the exit that
    passed no features. Post-fix, a non-empty result always carries a fused score,
    and an empty result carries no ``returned_ids`` to match on.

    ⚠ **`search_symbols_fusion` is NOT covered, and cannot be.** It always passed
    ``top1_score``/``top2_score`` and only ever omitted the ``identity`` key its
    features read, so its pre-fix rows are indistinguishable from honest post-fix
    rows that genuinely had no identity match. There is no version or timestamp
    column to bound it by, so its history is unseparable: the recency window is the
    only remedy, and inventing a heuristic here would be worse than the gap.

    ⚠ **v1.108.272 (#440): `search_symbols` is NOT covered either, for the same
    reason and over a much larger share of the table.** Both non-fusion exits — the
    default path and the semantic one — built the same score-only ledger input, so
    every ``tool = "search_symbols"`` row written before v1.108.272 carries
    ``identity_hit = 0`` whatever the identity channel found. They too always passed
    ``top1_score``, so nothing on the row separates them from an honest post-fix 0.

    Two things follow, and the second is the uncomfortable one. The recency window
    is again the only remedy. And because ``search_symbols`` is the highest-volume
    producer in the ledger, the affected share is far larger than the fusion case
    this predicate was written for — a reader must not take "the fusion rows are
    handled" as "the ``identity_hit`` column is now clean". It is clean only for
    rows written after v1.108.272.
    """
    try:
        tool = row[_TOOL]
        if tool != MISLABELLED_FUSION_TOOL:
            return True
        if row[_TOP1_SCORE] is not None:
            return True
        returned = row[_RETURNED_IDS]
    except (IndexError, TypeError, KeyError):
        return True
    # `returned_ids` is stored as a JSON array string; "[]" and None both mean the
    # scan returned nothing, which is not a row this rule can speak about.
    if not returned or returned in ("[]", b"[]"):
        return True
    return False
