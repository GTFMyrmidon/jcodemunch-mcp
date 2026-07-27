"""Exact immutable evidence receipts (``jcodemunch.evidence/v1``, #377 Phase 2).

Phase 1 (v1.108.165) answered *which reference is attached to which claim*. It
did not narrow **what a reference proves**, and we said so publicly when we
shipped it: ``handoff._validate_evidence`` attests a ref that matches a served
symbol id **or the file component of one**, so

    served this session:  src/auth/session.py::refresh_token#function
    attests:              src/auth/session.py::refresh_token#function   correct
    attests:              src/auth/session.py                           over-broad

and the reader of a finalized handoff cannot tell those two citations apart.

A receipt is the narrow answer. It binds one canonical subject (symbol id, line
range, content hash) to one snapshot (index generation, revisions, freshness,
working-tree state, coverage digest, scorer pin) and to the operation that
actually ran (``effective_search``), then derives its identity from exactly
those three. Different snapshot means a different id, so a receipt cannot
silently stand in for a scan of a different state.

Four boundaries are load-bearing:

* **A resource, not a tool.** Receipts are read at ``munch://evidence/<id>``
  alongside ``munch://runtime/identity`` (#371) and ``munch://handoff/<id>``
  (#374). The tool-schema budget is a real constraint and no tool is added.
* **The response carries an id, not the receipt.** The body is read from the
  resource on demand, so opting in costs a handful of bytes per response.
* **The server attests, the assistant concludes.** A receipt says what was
  retrieved, from what snapshot, with what limitations. It never says what that
  means; requirement matching is Phase 4.
* **Minting is gated on registration.** A tool cannot mint by returning the
  right-looking dict — see ``evidence/producers.py``. An unregistered producer
  produces no receipts, silently and safely.

``limitations`` is the field that makes a weak receipt usable rather than
discardable: a receipt that knows what it cannot prove is worth more than one
that omits it.

Session-scoped by design (the #374 charter): process lifetime == session
lifetime, in memory only, never written to the user's repository or index store.
Session keying for shared HTTP transports, and the full expiry taxonomy, are
Phase 2's P3 stage and are NOT implemented here; ``lookup`` distinguishes
``never_recorded`` from ``evicted`` because a bounded store makes that one bit
unavoidable, and reporting the wrong reason would be worse than reporting none.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import threading
from collections import OrderedDict
from typing import Optional

logger = logging.getLogger(__name__)

EVIDENCE_SCHEMA = "jcodemunch.evidence/v1"
EVIDENCE_URI_PREFIX = "munch://evidence/"
EVIDENCE_CONTENT_TYPE = "application/json"
PRODUCT = "jcodemunch"

#: Full digest, never a 12-hex prefix. 48 bits of displayed identity is a
#: birthday problem waiting for a large session; humans get a short prefix in
#: rendered prose, while identity, comparison and storage always use the whole
#: thing.
_ID_ALGO = "sha256"

# Proof kinds a producer may declare. A producer may mint only the kinds its
# operation actually supports (see producers.PRODUCERS), and the sibling kinds
# are listed here so the suite-parity contract (jdoc sections, jdata columns)
# names the same vocabulary rather than inventing a second one.
PROOF_KINDS = frozenset(
    {
        "symbol_definition",
        "symbol_lookup_absence",
        "literal_text_absence",
        "declaration_kind_absence",
        "reference_graph_absence",
        "implementation_graph_absence",
        "document_section",
        "document_topic_absence",
        "column_definition",
        "column_identity_absence",
    }
)

_ABSENCE_KINDS = frozenset(k for k in PROOF_KINDS if k.endswith("_absence"))

# Session store. Bounded, because a fan-out of receipt-enabled calls over a
# large repo would otherwise grow without limit; an evicted id reports
# `evicted` rather than `never_recorded`, so a citing caller is told the receipt
# existed and aged out instead of being told it never happened.
_MAXSIZE = 500
_EVICTED_MAXSIZE = 5000

_lock = threading.Lock()
_receipts: "OrderedDict[str, dict]" = OrderedDict()
_evicted: "OrderedDict[str, bool]" = OrderedDict()
#: absence ref (`absent:<sha12>`, #377 phase 3) -> evidence_id, so a Phase 3
#: token a shipped agent is already holding can also resolve to a receipt.
_absence_links: "OrderedDict[str, str]" = OrderedDict()
#: ids whose payload was contested. A citing ref must fail closed, not pick one.
_collisions: "OrderedDict[str, bool]" = OrderedDict()


def _canonical(payload) -> str:
    """Byte-stable JSON for hashing and for payload comparison."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def evidence_id(subject: dict, effective_search: dict, snapshot: dict) -> str:
    """Identity over exactly ``(subject, effective_search, snapshot)``.

    Nothing else is in it, and all three are in it: two scans that differ in a
    way that changes the answer must not collide on one id, and two readings of
    the same scan against the same snapshot must produce the same one.
    """
    digest = hashlib.sha256(
        _canonical(
            {
                "subject": subject,
                "effective_search": effective_search,
                "snapshot": snapshot,
            }
        ).encode("utf-8")
    ).hexdigest()
    return f"{_ID_ALGO}:{digest}"


def coverage_fingerprint(coverage: Optional[dict]) -> Optional[str]:
    """Opaque digest of the coverage block backing this receipt.

    Deliberately opaque and deliberately NOT a corpus manifest: source-universe
    identity, per-file parse outcomes and parser capability fingerprints are
    Phase 5 (#385). This is the extension point, and filling it in is that
    issue's job. What it does buy today is that two otherwise-identical scans
    over corpora with different coverage cannot share an evidence id.
    """
    if not coverage:
        return None
    return hashlib.sha256(_canonical(coverage).encode("utf-8")).hexdigest()[:32]


def build_envelope(
    *,
    proof_kind: str,
    subject: dict,
    effective_search: dict,
    snapshot: dict,
    channels: Optional[dict] = None,
    coverage: Optional[dict] = None,
    integrity: Optional[dict] = None,
    capabilities: Optional[dict] = None,
    limitations: Optional[list] = None,
    absence_ref: Optional[str] = None,
    absence_record: Optional[dict] = None,
) -> Optional[dict]:
    """Assemble one receipt. Returns None on an undeclarable proof kind.

    ``absence_record`` is a deep copy of the recorded scan this receipt was
    minted from (#377 P3, v1.108.192). The ``absence_ref`` alone is NOT
    snapshot-bound: its key is ``sha256(tool, repo, query, scope)[:12]`` with no
    snapshot in it, so re-running the same query over a different tree
    overwrites the record at the same key. A receipt that resolved through that
    key would be validated against a scan it was never minted from. The ref is
    still carried, for provenance and for the live-response path, but it is no
    longer what the receipt STANDS on.
    """
    if proof_kind not in PROOF_KINDS:
        logger.debug("Refusing to build a receipt for unknown proof kind %r", proof_kind)
        return None
    eid = evidence_id(subject, effective_search, snapshot)
    envelope = {
        "schema": EVIDENCE_SCHEMA,
        "evidence_id": eid,
        "proof_kind": proof_kind,
        "product": PRODUCT,
        "subject": subject,
        "effective_search": effective_search,
        # A view over the narrowing half of effective_search, kept beside it so
        # a reader can see the scope without reconstructing it. It is not a
        # separate identity input; everything in it is already in
        # effective_search.
        "scope": dict(effective_search.get("scope") or {}),
        "snapshot": snapshot,
        "channels": dict(channels or {}),
        "coverage": coverage,
        "integrity": dict(integrity or {}),
        "capabilities": dict(capabilities or {}),
        "limitations": list(limitations or []),
        "resource_uri": f"{EVIDENCE_URI_PREFIX}{eid}",
    }
    if absence_ref:
        envelope["absence_ref"] = absence_ref
    if absence_record is not None:
        # Deep copy: the live record is a mutable dict in another module's map,
        # and a shared nested `channels` dict would let a later scan reach into
        # a minted receipt.
        envelope["absence_record"] = copy.deepcopy(absence_record)
    return envelope


def record_receipt(envelope: Optional[dict]) -> Optional[str]:
    """Store a receipt immutably; return its id, or None when it cannot be stored.

    Fail closed on id reuse over differing content. The canonical payload is
    retained precisely so this comparison is possible: an id that has ever named
    two different receipts names neither afterwards, because silently keeping
    one of them would make every later citation of it a coin flip.
    """
    if not isinstance(envelope, dict):
        return None
    eid = envelope.get("evidence_id")
    if not isinstance(eid, str) or not eid:
        return None
    payload = _canonical({k: v for k, v in envelope.items() if k != "evidence_id"})
    with _lock:
        if eid in _collisions:
            return None
        existing = _receipts.get(eid)
        if existing is not None:
            if existing["payload"] != payload:
                # Never overwrite different content under one id.
                _collisions[eid] = True
                while len(_collisions) > _EVICTED_MAXSIZE:
                    _collisions.popitem(last=False)
                _receipts.pop(eid, None)
                logger.error(
                    "Evidence id collision on differing payloads (%s); both refused", eid
                )
                return None
            return eid
        _receipts[eid] = {"envelope": envelope, "payload": payload}
        ref = envelope.get("absence_ref")
        if isinstance(ref, str) and ref:
            _absence_links[ref] = eid
            while len(_absence_links) > _MAXSIZE:
                _absence_links.popitem(last=False)
        while len(_receipts) > _MAXSIZE:
            old_id, _old = _receipts.popitem(last=False)
            _evicted[old_id] = True
            while len(_evicted) > _EVICTED_MAXSIZE:
                _evicted.popitem(last=False)
    return eid


def is_evidence_ref(ref: str) -> bool:
    """Whether *ref* is shaped like a receipt citation.

    Both forms are accepted: the self-describing resource URI, which is what
    rendered proofs and Phase 4 requirements will name, and the bare id, which
    is shorter in a handoff body. Neither could previously attest anything, so
    accepting both is strictly additive.
    """
    if not isinstance(ref, str):
        return False
    s = ref.strip()
    return s.startswith(EVIDENCE_URI_PREFIX) or s.startswith(f"{_ID_ALGO}:")


def normalize_ref(ref: str) -> str:
    """Bare evidence id for either citation form."""
    s = str(ref).strip()
    if s.startswith(EVIDENCE_URI_PREFIX):
        s = s[len(EVIDENCE_URI_PREFIX):]
    return s


def lookup(ref: str):
    """``(envelope, reason)`` for a receipt citation.

    ``reason`` is None on a hit and otherwise names WHY the lookup failed, so
    the citing caller gets "this receipt aged out of the session store" rather
    than "we have never heard of this". Each failure has its own message; the
    remaining states in the Phase 2 taxonomy (wrong_session, wrong_subject,
    expired, snapshot_invalidated) arrive with session keying in P3.
    """
    eid = normalize_ref(ref)
    with _lock:
        if eid in _collisions:
            return None, "collision"
        rec = _receipts.get(eid)
        if rec is not None:
            # A copy: a caller must not be able to mutate a stored receipt, and
            # finalization deep-freezes what it validates.
            return json.loads(json.dumps(rec["envelope"])), None
        if eid in _evicted:
            return None, "evicted"
    return None, "never_recorded"


def receipt_for_absence_ref(ref: str) -> Optional[dict]:
    """The receipt minted alongside a Phase 3 ``absent:`` token, when there is one."""
    with _lock:
        eid = _absence_links.get(str(ref).strip())
    if not eid:
        return None
    envelope, _reason = lookup(eid)
    return envelope


def envelope_json(envelope: dict) -> str:
    """Deterministic body for the resource read.

    Repeated reads of one receipt are byte-identical, which is the whole point:
    a proof that renders differently on the second read is not a proof.
    """
    return json.dumps(envelope, sort_keys=True, indent=2) + "\n"


def short_id(eid: str) -> str:
    """Short prefix for human prose. Never for identity, comparison or storage."""
    bare = normalize_ref(eid)
    body = bare.split(":", 1)[-1]
    return body[:12]


def is_absence_kind(proof_kind: Optional[str]) -> bool:
    return proof_kind in _ABSENCE_KINDS


def list_evidence_resources() -> list[dict]:
    """Rows for ``list_resources()``: one per receipt minted this session."""
    with _lock:
        items = list(_receipts.items())
    rows = []
    for eid, rec in items:
        env = rec["envelope"]
        subject = env.get("subject") or {}
        target = subject.get("symbol_id") or subject.get("file") or env.get("proof_kind")
        rows.append(
            {
                "uri": env["resource_uri"],
                "name": f"evidence-{short_id(eid)}",
                "description": (
                    f"{env['proof_kind']} receipt ({EVIDENCE_SCHEMA}) for {target}; "
                    f"immutable, bound to one subject and one snapshot."
                ),
            }
        )
    return rows


def evidence_for_uri(uri: str) -> Optional[dict]:
    """Resolve a ``munch://evidence/<id>`` URI to its stored receipt, or None."""
    s = str(uri)
    if not s.startswith(EVIDENCE_URI_PREFIX):
        return None
    envelope, _reason = lookup(s)
    return envelope


def clear_receipts() -> None:
    """Test hook: drop the session receipt store."""
    with _lock:
        _receipts.clear()
        _evicted.clear()
        _absence_links.clear()
        _collisions.clear()
