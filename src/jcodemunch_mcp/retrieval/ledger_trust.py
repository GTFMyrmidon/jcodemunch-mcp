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
