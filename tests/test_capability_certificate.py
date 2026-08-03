"""Corpus capability certificate — what this INSTALLATION could establish.

The claim under test is a distinction, not a feature: ``coverage`` says what a
SCAN did not see; the certificate says what this INSTALL was ABLE to see. Two
machines on the same commit with different grammar packs both report
``complete: true`` today.

⚠ Every test here is written against the failure mode that a certificate
OVERSTATES capability. A document whose purpose is honest capability reporting
is worse than nothing when it flatters — the first draft of this module reported
157 languages because ``LANGUAGE_EXTENSIONS`` maps extension -> language and
``len()`` of it is the extension count. The real number is 74.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.evidence import capability as cap


class _Idx:
    def __init__(self, **kw):
        self.indexed_at = "2026-08-02T00:00:00"
        self.git_head = "abc123"
        for k, v in kw.items():
            setattr(self, k, v)


class TestParserFingerprint:
    def test_language_count_is_languages_not_extensions(self):
        """The overstatement the first draft shipped with."""
        from jcodemunch_mcp.parser.languages import LANGUAGE_EXTENSIONS

        fp = cap.parser_fingerprint()
        assert fp["extensions"] == len(LANGUAGE_EXTENSIONS)
        assert fp["languages"] == len(set(LANGUAGE_EXTENSIONS.values()))
        assert fp["languages"] < fp["extensions"], (
            "a registry with multi-suffix languages must report fewer languages "
            "than extensions; equal counts mean one is the other"
        )

    def test_grammar_pack_version_is_recorded(self):
        """#382: 1.x stopped bundling grammars, dropped three of our languages
        and swapped nim's upstream. Two installs on different packs have
        different capability while reporting identical coverage."""
        fp = cap.parser_fingerprint()
        assert "tree-sitter-language-pack" in fp["packages"]
        assert fp["packages"]["tree-sitter-language-pack"]

    def test_a_missing_package_reads_as_absent_not_null(self):
        """An install without the pack parses nothing. `null` would read as
        'not measured', which is a different claim."""
        import importlib.metadata as md

        real = md.version

        def boom(pkg):
            if "tree-sitter" in pkg:
                raise md.PackageNotFoundError(pkg)
            return real(pkg)

        try:
            md.version = boom
            fp = cap.parser_fingerprint()
        finally:
            md.version = real
        assert fp["packages"]["tree-sitter-language-pack"] == "absent"


class TestChannelsAreTriState:
    def test_unprobeable_channel_stays_unknown(self):
        """⚠ The bug this caught: a local `store = EmbeddingStore(path)`
        shadowed the IndexStore parameter the SCIP probe needs, so the
        compiler-evidence answer depended on an overwritten variable."""
        idx = _Idx(_db_path=None)
        ch = cap.channel_generations(idx)
        assert ch["compiler_evidence"] == "unknown"
        assert ch["semantic"] == "unknown"

    def test_absent_and_unknown_are_never_collapsed(self, tmp_path):
        """Collapsing them lets a failed probe read as a clean 'no embeddings',
        which is the v1.108.215 false-absence shape one layer up."""
        db = tmp_path / "index.db"
        db.write_bytes(b"")
        idx = _Idx(_db_path=str(db))
        ch = cap.channel_generations(idx)
        # No store/owner/name supplied, so the compiler channel is genuinely
        # not established and must say so.
        assert ch["compiler_evidence"] == "unknown"


class TestCertificate:
    def test_is_content_addressed_and_deterministic(self):
        idx = _Idx(_db_path=None)
        a = cap.build_certificate(idx, {"complete": True})
        b = cap.build_certificate(idx, {"complete": True})
        assert a["certificate_id"] == b["certificate_id"]
        assert len(a["certificate_id"]) == 32

    def test_a_different_eligibility_policy_is_a_different_certificate(self):
        idx = _Idx(_db_path=None)
        strict = cap.build_certificate(
            idx, {"complete": True}, config_get=lambda k, d=None: 100000
        )
        loose = cap.build_certificate(
            idx, {"complete": True}, config_get=lambda k, d=None: 999999
        )
        assert strict["certificate_id"] != loose["certificate_id"], (
            "two installs whose file-size limits differ index different corpora "
            "from the same commit; the certificate must say so"
        )

    def test_it_carries_no_file_list(self):
        """Bounded by construction. A manifest would be large, would churn on
        every commit, and nothing consumes it."""
        import json

        idx = _Idx(_db_path=None)
        blob = json.dumps(cap.build_certificate(idx, {"complete": True}))
        assert len(blob) < 4000
        assert ".py" not in blob

    def test_it_surfaces_the_reason_coverage_cannot_justify(self):
        """⚠ The live case: `wrong_extension` conflates 'not source code' with
        'this install cannot parse that'. The walk has no way to tell which, so
        the count is surfaced separately rather than pooled with scope
        decisions."""
        cert = cap.build_certificate(
            _Idx(_db_path=None),
            {"complete": True, "skip_counts": {"wrong_extension": 59,
                                               "gitignore": 4267}},
        )
        cov = cert["coverage"]
        assert cov["unsupported_extension_skips"] == 59
        assert "gitignore" in cov["skip_reasons"]

    def test_never_raises_on_a_hostile_index(self):
        class Hostile:
            @property
            def indexed_at(self):
                raise RuntimeError("boom")

        cert = cap.build_certificate(Hostile())
        assert cert["version"] == cap.CERTIFICATE_VERSION
        assert cert["certificate_id"]

    def test_id_helper_matches_the_document(self):
        idx = _Idx(_db_path=None)
        assert cap.certificate_id(idx, {"complete": True}) == \
            cap.build_certificate(idx, {"complete": True})["certificate_id"]


class TestNotATool:
    def test_the_certificate_is_not_a_catalog_action(self):
        """The moratorium applies to us first."""
        import sys

        sys.argv = [sys.argv[0]]
        from jcodemunch_mcp.server import _catalog_names

        names = _catalog_names()
        for leaked in ("build_certificate", "capability", "certificate_id"):
            assert leaked not in names


class TestBoundIntoReceipts:
    """Otherwise it is the decorative metadata the report warned about."""

    def test_a_capability_difference_changes_the_fingerprint(self, monkeypatch):
        from jcodemunch_mcp.evidence import receipts

        idx = _Idx(_db_path=None)
        cov = {"complete": True, "files_indexed": 10}

        base = receipts.coverage_fingerprint(cov, idx)
        monkeypatch.setattr(
            cap, "parser_fingerprint",
            lambda: {"languages": 3, "extensions": 4, "packages": {}},
        )
        thin = receipts.coverage_fingerprint(cov, idx)
        assert base != thin, (
            "an install that can parse 3 languages and one that can parse 74 "
            "produce different corpora from the same commit; their receipts "
            "must not share a fingerprint"
        )

    def test_the_pre_binding_digest_is_preserved_when_no_index_is_given(self):
        """Backward compatibility is explicit, not incidental."""
        import hashlib
        import json

        from jcodemunch_mcp.evidence import receipts

        cov = {"complete": True, "files_indexed": 10}
        expected = hashlib.sha256(
            json.dumps(cov, sort_keys=True, separators=(",", ":"),
                       default=str).encode("utf-8")
        ).hexdigest()[:32]
        assert receipts.coverage_fingerprint(cov) == expected

    def test_empty_coverage_with_no_index_is_still_none(self):
        from jcodemunch_mcp.evidence import receipts

        assert receipts.coverage_fingerprint(None) is None
        assert receipts.coverage_fingerprint({}) is None
