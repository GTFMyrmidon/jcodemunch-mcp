"""v1.108.192 — #377 Phase 2 P3: one immutable snapshot, start to finish.

The two edges @mightydanp pinned adversarially on 2026-07-27:

1. An evidence receipt has a full snapshot-bound SHA-256 identity, but an
   ABSENCE receipt linked out to the mutable legacy `absent:<sha12>` record,
   whose key is derived from (tool, repo, query, scope) and carries no snapshot
   at all. A later scan of the same query over a different tree overwrites it,
   and finalization followed `envelope.absence_ref` back to whatever is there
   NOW. So an immutable receipt was validated against a record it was never
   minted from.

2. Validation resolved and accepted an envelope, then rendering performed a
   SECOND, independent receipt lookup. The store is bounded, so the receipt can
   be evicted between the two, and the body then hashes without the receipt
   detail while the attestation says it was proved.

Both are the same defect wearing different clothes: the pipeline read the
evidence more than once and assumed the reads agreed.
"""

import hashlib

import pytest

from jcodemunch_mcp import handoff
from jcodemunch_mcp.evidence import receipts


@pytest.fixture(autouse=True)
def _clean():
    handoff.clear_absences()
    handoff.clear_handoffs()
    receipts.clear_receipts()
    yield
    handoff.clear_absences()
    handoff.clear_handoffs()
    receipts.clear_receipts()


def _absent_verdict(**over):
    v = {
        "state": "absent",
        "scanned": {"symbols": 100, "files": 10},
        "channels": {"lexical": "ok", "semantic": "off", "index": "fresh"},
        "coverage": {"complete": True},
        "truncated": False,
        "scorer": 1,
    }
    v.update(over)
    return v


def _mint_absence_receipt(*, generation, query="widget_factory"):
    """Record a scan and mint a receipt bound to one snapshot generation."""
    ref, refusal = handoff.note_absence(
        "search_symbols", "local/demo", query, _absent_verdict(), arguments={}
    )
    assert refusal is None, refusal
    assert ref is not None
    # Mirrors evidence/producers.py's mint exactly, including the frozen record.
    # That the PRODUCER passes it is pinned separately below, so this helper
    # cannot make the fix look present when it is not.
    env = receipts.build_envelope(
        proof_kind="symbol_lookup_absence",
        subject={"query": query},
        effective_search={"tool": "search_symbols", "query": query, "scope": {}},
        snapshot={"index_generation": generation, "freshness": "fresh"},
        absence_ref=ref,
        absence_record=handoff.absence_record(ref),
    )
    eid = receipts.record_receipt(env)
    assert eid
    return ref, eid, env


# ── Edge 1: an absence receipt must not follow a mutable key ────────────────


class TestAbsenceReceiptIsSnapshotBound:
    def test_a_provable_receipt_is_not_unproved_by_a_later_stale_scan(self):
        """Direction 1 of what @mightydanp asked for.

        Receipt A is minted over a scan that COULD prove absence. The same
        query/scope is scanned again over a staler tree, overwriting the legacy
        record at the same key. Receipt A must still attest on its own frozen
        evidence, not inherit scan B's staleness.
        """
        ref, eid, _env = _mint_absence_receipt(generation=1)

        ref2, refusal2 = handoff.note_absence(
            "search_symbols", "local/demo", "widget_factory",
            _absent_verdict(channels={"lexical": "ok", "index": "stale"}),
            arguments={},
        )
        assert ref2 is None and refusal2, "scan 2 must be refused on its own"
        # Same key: scan 2 has overwritten scan 1's record. This is the defect.
        assert handoff.absence_record(ref)["channels"]["index"] == "stale"

        ref_uri = f"munch://evidence/{eid}"
        att = handoff._validate_evidence([ref_uri], served_ids=[])

        assert not att["refused"], (
            "the receipt was minted over a provable scan and must stand on it; "
            f"it inherited a later scan's state instead: {att['refused']}"
        )
        env = next(r["receipt"] for r in att["receipts"] if r["ref"] == ref_uri)
        assert env["absence_record"]["channels"]["index"] == "fresh"

    def test_an_unprovable_receipt_is_not_proved_by_a_later_fresh_scan(self):
        """Direction 2, and the dangerous one.

        A receipt minted over a scan that could NOT prove absence must not
        become provable because a later, cleaner scan landed on the same key.
        """
        query = "ghost_symbol"
        # Scan 1: stale index, so it cannot prove absence.
        ref1, refusal1 = handoff.note_absence(
            "search_symbols", "local/demo", query,
            _absent_verdict(channels={"lexical": "ok", "index": "stale"}),
            arguments={},
        )
        assert ref1 is None and refusal1
        legacy_ref = handoff.absence_ref_for("search_symbols", "local/demo", query, {})
        env = receipts.build_envelope(
            proof_kind="symbol_lookup_absence",
            subject={"query": query},
            effective_search={"tool": "search_symbols", "query": query, "scope": {}},
            snapshot={"index_generation": 1, "freshness": "stale"},
            absence_ref=legacy_ref,
            absence_record=handoff.absence_record(legacy_ref),
        )
        eid = receipts.record_receipt(env)
        assert eid

        # Scan 2 over the same key, this time clean.
        ref2, refusal2 = handoff.note_absence(
            "search_symbols", "local/demo", query, _absent_verdict(), arguments={}
        )
        assert refusal2 is None and ref2
        assert handoff.absence_record(legacy_ref)["channels"]["index"] == "fresh"

        ref_uri = f"munch://evidence/{eid}"
        att = handoff._validate_evidence([ref_uri], served_ids=[])

        assert att["refused"], (
            "a receipt minted over a stale scan was attested because a LATER "
            "scan of the same query was clean; that is borrowed proof"
        )
        assert "stale" in att["refused"][0]["reason"].lower()

    def test_receipt_survives_eviction_of_the_legacy_record(self):
        """A receipt is immutable; the legacy absence map is bounded and LRU.

        If the record it was minted from is gone, the receipt must still carry
        what it proved rather than degrading to 'no longer on record'.
        """
        ref, eid, _env = _mint_absence_receipt(generation=7)

        # Evict the legacy record out from under the receipt.
        with handoff._lock:
            handoff._absences.clear()
        assert handoff.absence_record(ref) is None

        att = handoff._validate_evidence([f"munch://evidence/{eid}"], served_ids=[])
        ref_uri = f"munch://evidence/{eid}"

        assert not att["unknown"], att["unknown"]
        assert ref_uri in {r["ref"] for r in att["receipts"]}
        assert not att["refused"], (
            "the receipt froze a provable scan at mint time; losing the mutable "
            "record must not retroactively unprove it"
        )

    def test_frozen_record_is_a_copy_not_a_reference(self):
        """Mutating the live record must not reach into the minted receipt."""
        ref, eid, _env = _mint_absence_receipt(generation=3)

        live = handoff.absence_record(ref)
        assert live is not None
        live["channels"]["index"] = "stale"
        live["state"] = "degraded"

        env, why = receipts.lookup(f"munch://evidence/{eid}")
        assert env is not None, why
        frozen = env.get("absence_record")
        assert frozen is not None
        assert frozen["channels"]["index"] == "fresh"
        assert frozen["state"] == "absent"


# ── Edge 2: one resolution, shared by validation, rendering and hashing ─────


class TestSingleAtomicResolution:
    def _served_receipt(self):
        env = receipts.build_envelope(
            proof_kind="symbol_definition",
            subject={
                "symbol_id": "src/a.py::f#function",
                "file": "src/a.py",
                "start_line": 1,
                "end_line": 5,
                "content_sha256": "a" * 64,
            },
            effective_search={"tool": "get_symbol_source", "scope": {}},
            snapshot={"index_generation": 2, "freshness": "fresh"},
        )
        eid = receipts.record_receipt(env)
        assert eid
        return eid

    def test_render_does_not_re_resolve_the_receipt(self):
        """Rendering must consume the envelope validation already resolved.

        A second lookup is a second chance to get a different answer, and the
        store is bounded. This asserts on the seam rather than the symptom, so
        it keeps holding if the eviction policy changes.
        """
        import inspect

        src = inspect.getsource(handoff._render_receipt_detail)
        assert "lookup(" not in src, (
            "_render_receipt_detail must take the resolved envelope, not "
            "re-resolve it"
        )

    def test_body_still_carries_receipt_detail_after_eviction(self, monkeypatch):
        """The symptom, end to end.

        Validation accepts the receipt, the store then loses it, and the body
        must still render and hash the evidence that was attested.
        """
        eid = self._served_receipt()
        ref = f"munch://evidence/{eid}"

        # Simulate the store losing the receipt AFTER validation resolved it.
        # No production test seam: patch the lookup so the first read (the
        # validation one) succeeds and every read after it fails, which is
        # exactly what eviction between the two looks like.
        real_lookup = receipts.lookup
        calls = {"n": 0}

        def _lookup_once(ref_arg):
            calls["n"] += 1
            if calls["n"] == 1:
                return real_lookup(ref_arg)
            return None, "evicted"

        monkeypatch.setattr(receipts, "lookup", _lookup_once)

        result = handoff.finalize_handoff(
            repo="local/demo",
            task="t",
            profile="p",
            sections=[{"heading": "s", "content": "c"}],
            evidence_refs=[ref],
            served_ids=["src/a.py::f#function"],
        )

        assert "error" not in result, result
        body = handoff.get_handoff(result["handoff_id"])["body"]
        assert "receipt:" in body, (
            "validation attested a receipt the body does not show; the "
            "attestation and the rendered proof came from different reads"
        )
        assert "symbol_definition" in body
        # And the hash is over exactly what was rendered.
        assert result["sha256"] == hashlib.sha256(body.encode("utf-8")).hexdigest()

    def test_no_receipts_cited_is_unchanged(self):
        """The v1 guarantee this must not break."""
        result = handoff.finalize_handoff(
            repo="local/demo",
            task="t",
            profile="p",
            sections=[{"heading": "s", "content": "c"}],
            evidence_refs=["src/a.py::f#function"],
            served_ids=["src/a.py::f#function"],
        )
        assert "error" not in result, result
        body = handoff.get_handoff(result["handoff_id"])["body"]
        assert "receipt:" not in body


class TestProducerFreezesTheRecord:
    """The helper above mirrors the producer. This proves the producer is what
    it mirrors, so a regression in evidence/producers.py cannot hide behind a
    test fixture that does the right thing on its own."""

    def test_mint_passes_the_record_not_only_the_ref(self):
        import inspect

        from jcodemunch_mcp.evidence import producers

        src = inspect.getsource(producers)
        assert "absence_record=record" in src, (
            "the absence producer must freeze the scan into the receipt; "
            "absence_ref alone is not snapshot-bound"
        )

    def test_build_envelope_deep_copies(self):
        """A shared nested dict would let a later scan reach into a receipt."""
        record = {"state": "absent", "channels": {"index": "fresh"}}
        env = receipts.build_envelope(
            proof_kind="symbol_lookup_absence",
            subject={"query": "q"},
            effective_search={"tool": "search_symbols", "scope": {}},
            snapshot={"index_generation": 1},
            absence_record=record,
        )
        record["channels"]["index"] = "stale"
        assert env["absence_record"]["channels"]["index"] == "fresh"

    def test_legacy_receipt_without_a_frozen_record_still_resolves(self):
        """Receipts minted before v1.108.192 must not become uncitable."""
        ref, refusal = handoff.note_absence(
            "search_symbols", "local/demo", "legacy_q", _absent_verdict(), arguments={}
        )
        assert refusal is None and ref
        env = receipts.build_envelope(
            proof_kind="symbol_lookup_absence",
            subject={"query": "legacy_q"},
            effective_search={"tool": "search_symbols", "scope": {}},
            snapshot={"index_generation": 1},
            absence_ref=ref,          # ref only, the pre-fix shape
        )
        eid = receipts.record_receipt(env)
        att = handoff._validate_evidence([f"munch://evidence/{eid}"], served_ids=[])
        assert not att["unknown"] and not att["refused"], att
