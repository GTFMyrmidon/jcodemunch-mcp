"""v1.108.168 — a rebuild underneath a scan cannot prove absence.

v1.108.166 shipped four refusal rules for absence evidence (only `absent`;
never low_confidence/degraded; never stale; never truncated). None of them
covered an index being REWRITTEN while the scan reads it.

The gap was real: `channels.index` is fed by a git-SHA probe, which is blind to
a reindex of an UNCHANGED tree (a watcher rebuild after an uncommitted edit).
Such a scan reported `index: "fresh"`, state `absent`, and minted a citable
"proven not there" ref over a half-written index.

Detection is a filesystem signal, NOT `reindex_state` — that module is
in-memory and per-process, so a server answering a search cannot see a reindex
run by a separate `watch-all` service, which is the common deployment.
"""

import pytest

from jcodemunch_mcp import handoff
from jcodemunch_mcp.retrieval.verdict import build_verdict, index_changed_since_load


class _FakeIndex:
    """Bare object standing in for a CodeIndex (plain dataclass, no slots)."""


@pytest.fixture()
def clean_absences():
    handoff.clear_absences()
    yield
    handoff.clear_absences()


def _verdict(state, index_channel="fresh"):
    return {
        "state": state,
        "scanned": {"symbols": 10, "files": 2},
        "channels": {"lexical": "ok", "semantic": "off", "index": index_channel},
        "scorer": 1,
    }


class TestChangeDetection:
    def test_unstamped_index_is_not_changed(self):
        """Unknown must never mean changed, or every verdict degrades."""
        assert index_changed_since_load(_FakeIndex()) is False

    def test_unchanged_db_reports_false(self, tmp_path):
        db = tmp_path / "repo.db"
        db.write_bytes(b"x")
        idx = _FakeIndex()
        from jcodemunch_mcp.storage.sqlite_store import _db_mtime_ns

        idx._db_path = str(db)
        idx._loaded_mtime_ns = _db_mtime_ns(db)
        assert index_changed_since_load(idx) is False

    def test_rewritten_db_reports_true(self, tmp_path):
        db = tmp_path / "repo.db"
        db.write_bytes(b"x")
        idx = _FakeIndex()
        idx._db_path = str(db)
        idx._loaded_mtime_ns = 1  # a mtime the file cannot have
        assert index_changed_since_load(idx) is True

    def test_missing_db_never_raises(self, tmp_path):
        idx = _FakeIndex()
        idx._db_path = str(tmp_path / "gone.db")
        idx._loaded_mtime_ns = 12345
        assert index_changed_since_load(idx) is False

    def test_real_load_stamps_provenance(self, tmp_path):
        """End-to-end: a genuinely loaded index carries its own provenance."""
        from jcodemunch_mcp.storage.index_store import IndexStore

        store = IndexStore(base_path=str(tmp_path))
        store.save_index("o", "r", [], [], {}, source_root=str(tmp_path))
        idx = store.load_index("o", "r")
        assert idx is not None
        assert getattr(idx, "_db_path", None)
        assert getattr(idx, "_loaded_mtime_ns", None)
        assert index_changed_since_load(idx) is False


class TestVerdictGate:
    def test_zero_results_mid_rebuild_is_degraded_not_absent(self):
        v = build_verdict(result_count=0, index_changed=True)["verdict"]
        assert v["state"] == "degraded"
        assert v["channels"]["index"] == "rebuilding"

    def test_zero_results_on_a_settled_index_still_proves_absence(self):
        v = build_verdict(result_count=0, index_changed=False)["verdict"]
        assert v["state"] == "absent"
        assert v["channels"]["index"] == "fresh"

    def test_results_are_still_returned_mid_rebuild(self):
        """Only the absence CLAIM is refused; a real hit is still a real hit."""
        v = build_verdict(result_count=5, best_score=0.9, index_changed=True)["verdict"]
        assert v["state"] == "ok"
        assert v["channels"]["index"] == "rebuilding"

    def test_rebuilding_outranks_stale_in_the_channel(self):
        v = build_verdict(result_count=0, index_stale=True, index_changed=True)["verdict"]
        assert v["channels"]["index"] == "rebuilding"

    def test_default_is_byte_identical_to_pre_1_108_168(self):
        assert build_verdict(result_count=0) == build_verdict(
            result_count=0, index_changed=False
        )

    def test_legacy_negative_evidence_still_fires_mid_rebuild(self):
        """The zero-result trigger is unchanged; only the verdict state moved."""
        ne = build_verdict(result_count=0, index_changed=True)["negative_evidence"]
        assert ne is not None
        assert ne["verdict"] == "no_implementation_found"


class TestAbsenceRefusal:
    def test_rebuild_refusal_names_the_cause(self, clean_absences):
        reason = handoff.absence_refusal(
            {"state": "degraded", "channels": {"index": "rebuilding"}}
        )
        assert reason is not None
        assert "rewritten" in reason

    def test_rebuild_scan_yields_no_citable_ref(self, clean_absences):
        ref, refusal = handoff.note_absence(
            "search_symbols", "o/r", "widget",
            _verdict("degraded", index_channel="rebuilding"),
        )
        assert ref is None
        assert "rewritten" in refusal

    def test_the_refused_scan_is_still_recorded(self, clean_absences):
        """Citing it must return the reason, never a bare unknown-ref error."""
        handoff.note_absence(
            "search_symbols", "o/r", "widget",
            _verdict("degraded", index_channel="rebuilding"),
        )
        stored = [r for r in handoff._absences.values() if r["query"] == "widget"]
        assert len(stored) == 1
        assert handoff.absence_refusal(stored[0]) is not None

    def test_settled_index_still_mints_a_ref(self, clean_absences):
        ref, refusal = handoff.note_absence(
            "search_symbols", "o/r", "widget", _verdict("absent")
        )
        assert refusal is None
        assert ref and ref.startswith("absent:")

    def test_prior_four_rules_unaffected(self, clean_absences):
        assert handoff.absence_refusal(
            {"state": "absent", "channels": {"index": "stale"}}
        ) is not None
        assert handoff.absence_refusal(
            {"state": "low_confidence", "channels": {}}
        ) is not None
        assert handoff.absence_refusal(
            {"state": "absent", "channels": {}, "truncated": True}
        ) is not None
        assert handoff.absence_refusal(
            {"state": "absent", "channels": {"index": "fresh"}}
        ) is None

    def test_rendered_proof_discloses_the_rebuild(self, clean_absences):
        """channels render generically, so the audit body shows it with no new code."""
        handoff.note_absence(
            "search_symbols", "o/r", "widget",
            _verdict("degraded", index_channel="rebuilding"),
        )
        rec = next(iter(handoff._absences.values()))
        assert rec["channels"]["index"] == "rebuilding"


class TestPublishedSchema:
    def test_rebuilding_is_a_legal_index_channel_value(self):
        """The schema closes `channels` (additionalProperties: false) and
        enumerates `index`, so a new value MUST be published or every
        mid-rebuild response fails validation against our own contract."""
        import json
        from pathlib import Path

        import jsonschema

        root = Path(__file__).resolve().parents[1]
        schema = json.loads(
            (root / "schemas" / "retrieval-verdict.schema.json").read_text()
        )
        v = build_verdict(result_count=0, index_changed=True)["verdict"]
        assert v["channels"]["index"] == "rebuilding"
        jsonschema.validate(v, schema)
