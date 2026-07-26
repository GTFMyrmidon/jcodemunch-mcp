"""A ranking cannot prove absence, and the semantic exit stops claiming it can
(v1.108.184).

`search_symbols`'s semantic exit hand-rolled the legacy `negative_evidence` block
and emitted NO `_meta.verdict` at all, so not one of the absence gates shipped
since v1.108.166 applied to it — while it asserted "Do not claim this feature
exists" anyway. Third instance of the class found in v1.108.179: an early return
is a second implementation of the answer and inherits nothing.

The reproduction, which is worse than the missing block:

    semantic=True, token_budget=1, over a repo CONTAINING the target
    -> result_count: 0
    -> _meta.truncated: True
    -> "No implementation found ... Do not claim this feature exists."
    -> best_match_score: 53.774

A strong match reported in the same breath as its own absence. That is the item-1
defect ("an empty RESPONSE is not an empty SEARCH") fixed on the lexical path in
v1.108.177 and left standing here.

Two things ship. The exit now builds the same verdict as the lexical one, so every
gate applies. And a zero result from an embedding ranking can never reach `absent`
at all: the lexical path's absence rests on a corpus fact (no symbol in the index
contains any query term), while a cosine ranking's zero result rests on embedding
geometry, and a symbol can sit in the corpus scoring at or below zero against the
query vector. That is a statement about the model, not the repository.

Helper names are prefixed `_r184` — a plain `_state` helper once outranked
`token_tracker._State` for a golden replay query and cost a red CI run.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import jsonschema
import pytest

from jcodemunch_mcp import handoff as _handoff_mod
from jcodemunch_mcp.evidence import producers as _r184_producers
from jcodemunch_mcp.evidence import receipts as _r184_receipts
from jcodemunch_mcp.retrieval.verdict import build_verdict
from jcodemunch_mcp.tools.embed_repo import embed_repo
from jcodemunch_mcp.tools.search_symbols import search_symbols

_R184_ROOT = Path(__file__).resolve().parents[1]
def _r184_toward(_texts, *_a, **_k):
    """Every symbol embedded on one axis; the query lands on it too."""
    return [[1.0] + [0.0] * 7 for _ in _texts]


def _r184_away(texts, *_a, **_k):
    """A query vector pointing away from every symbol, so no cosine is positive."""
    out = []
    for text in texts:
        out.append([-1.0] + [0.0] * 7 if "zzz" in text else [1.0] + [0.0] * 7)
    return out


@pytest.fixture()
def _r184_indexed(tmp_path, monkeypatch):
    """A REAL folder index with mocked embeddings, and no real encoder near it.

    Deliberately not a hand-built ``save_index``: a synthetic index has no source
    root, so ``repo_freshness`` reports `unknown` and THAT gate fires first — which
    would mask the gate under test and make a lexical absence unciteable for a
    reason having nothing to do with this change. A folder index reports
    `not_tracked`, which is disclosed and stays citable, so the lexical control
    case works and the semantic reason is the one that surfaces.
    """
    monkeypatch.setenv("JCODEMUNCH_EMBED_MODEL", "all-MiniLM-L6-v2")
    store = tmp_path / "idx"
    monkeypatch.setenv("CODE_INDEX_PATH", str(store))
    proj = tmp_path / "proj"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "f1.py").write_text(
        "def refresh_token(user):\n    return user\n", encoding="utf-8"
    )
    (proj / "src" / "f2.py").write_text(
        "def render_html(template):\n    return template\n", encoding="utf-8"
    )
    from jcodemunch_mcp.tools.index_folder import index_folder

    _handoff_mod.clear_absences()
    _handoff_mod.clear_handoffs()
    _r184_receipts.clear_receipts()
    repo = index_folder(
        path=str(proj), use_ai_summaries=False, storage_path=str(store)
    )["repo"]
    with patch("jcodemunch_mcp.tools.embed_repo.embed_texts", side_effect=_r184_toward):
        embed_repo(repo, storage_path=str(store))
    yield {"storage": str(store), "repo": repo}
    _handoff_mod.clear_absences()
    _handoff_mod.clear_handoffs()
    _r184_receipts.clear_receipts()


def _r184_search(env, embed=_r184_toward, **kwargs):
    with patch("jcodemunch_mcp.tools.embed_repo.embed_texts", side_effect=embed):
        return search_symbols(env["repo"], storage_path=env["storage"], **kwargs)


# --- the reproduction --------------------------------------------------------


class TestTheReproduction:
    """An empty RESPONSE is not an empty SEARCH, on this exit too."""

    def test_a_budget_that_empties_the_response_no_longer_reads_as_absence(
        self, _r184_indexed
    ):
        result = _r184_search(
            _r184_indexed, query="refresh_token", semantic=True, token_budget=1
        )
        assert result["result_count"] == 0
        # The response used to carry this sentence over a repo containing the
        # target, with no verdict to contradict it.
        assert "⚠ warning" not in result
        assert result.get("negative_evidence") is None

    def test_the_exit_now_carries_a_verdict_at_all(self, _r184_indexed):
        """Fails on the old code: there was no `_meta.verdict` to read."""
        result = _r184_search(
            _r184_indexed, query="refresh_token", semantic=True, token_budget=1
        )
        verdict = result["_meta"]["verdict"]
        assert verdict["state"] == "degraded"
        assert verdict["scanned"]["symbols"] > 0

    def test_the_dropped_matches_are_counted_rather_than_denied(self, _r184_indexed):
        result = _r184_search(
            _r184_indexed, query="refresh_token", semantic=True, token_budget=1
        )
        omitted = result["_meta"]["verdict"]["omitted"]
        assert omitted["matches_found"] > 0
        assert omitted["returned"] == 0

    def test_the_note_names_the_fixable_cause_first(self, _r184_indexed):
        """Precedence: a budget the caller can raise beats a permanent property."""
        result = _r184_search(
            _r184_indexed, query="refresh_token", semantic=True, token_budget=1
        )
        note = result["_meta"]["verdict"]["note"]
        assert "response budget" in note or "token_budget" in note

    def test_a_normal_semantic_search_still_returns_its_rows(self, _r184_indexed):
        """The fix must not cost the exit its actual job."""
        result = _r184_search(_r184_indexed, query="refresh_token", semantic=True)
        assert result["result_count"] >= 1
        assert result["_meta"]["search_mode"] == "hybrid"
        assert result["_meta"]["verdict"]["state"] in ("ok", "low_confidence")


# --- a ranking cannot prove absence -----------------------------------------


class TestARankingCannotProveAbsence:
    def test_a_semantic_zero_result_is_degraded_never_absent(self, _r184_indexed):
        result = _r184_search(
            _r184_indexed,
            query="zzz_nothing_like_this",
            semantic=True,
            semantic_only=True,
            embed=_r184_away,
        )
        assert result["result_count"] == 0
        assert result["_meta"]["verdict"]["state"] == "degraded"

    def test_the_reason_is_disclosed_and_names_the_mechanism(self, _r184_indexed):
        result = _r184_search(
            _r184_indexed,
            query="zzz_nothing_like_this",
            semantic=True,
            semantic_only=True,
            embed=_r184_away,
        )
        reason = result["_meta"]["verdict"]["absence_unprovable"]["reason"]
        assert "similarity" in reason
        assert "present in the index" in reason

    def test_no_evidence_token_is_offered_for_a_semantic_zero_result(self, _r184_indexed):
        """The whole point: this scan must not become citable proof."""
        result = _r184_search(
            _r184_indexed,
            query="zzz_nothing_like_this",
            semantic=True,
            semantic_only=True,
            embed=_r184_away,
        )
        assert "evidence_ref" not in result["_meta"]["verdict"]

    def test_it_is_disclosed_on_a_successful_result_too(self, _r184_indexed):
        """Disclose on every state; refuse only the claim. A caller reading an `ok`
        semantic result may be about to re-run it expecting an absence answer."""
        result = _r184_search(_r184_indexed, query="refresh_token", semantic=True)
        assert result["result_count"] >= 1
        assert result["_meta"]["verdict"]["absence_unprovable"]["reason"]

    def test_the_lexical_path_can_still_prove_absence(self, _r184_indexed):
        """Non-vacuity from the other side: the gate must be scoped to the mode
        that earned it, not applied to every search."""
        result = search_symbols(
            _r184_indexed["repo"],
            query="zzz_nothing_like_this",
            storage_path=_r184_indexed["storage"],
        )
        assert result["result_count"] == 0
        verdict = result["_meta"]["verdict"]
        assert verdict["state"] == "absent"
        assert "absence_unprovable" not in verdict


class TestTheVerdictUnit:
    def test_absence_unprovable_downgrades_only_an_empty_result(self):
        empty = build_verdict(result_count=0, absence_unprovable="because.")
        assert empty["verdict"]["state"] == "degraded"
        full = build_verdict(result_count=3, best_score=9.0, absence_unprovable="because.")
        assert full["verdict"]["state"] == "ok"
        assert full["verdict"]["absence_unprovable"]["reason"] == "because."

    def test_it_never_masks_a_fixable_cause(self):
        """Checked LAST among the degraded gates, so a cause the caller can act on
        is reported in preference to a permanent property of the mode."""
        verdict = build_verdict(
            result_count=0,
            matches_before_packing=7,
            absence_unprovable="because.",
        )["verdict"]
        assert "response budget" in verdict["note"]
        assert verdict["absence_unprovable"]["reason"] == "because."

    def test_the_legacy_prose_is_suppressed(self):
        """`no_implementation_found` renders as "Do not claim this feature
        exists", which is unfounded when the mode cannot establish absence."""
        assert (
            build_verdict(result_count=0, absence_unprovable="because.")[
                "negative_evidence"
            ]
            is None
        )
        assert build_verdict(result_count=0)["negative_evidence"] is not None

    def test_a_zero_result_that_is_not_degraded_is_not_marked_refused(self):
        assert "absence_refused" not in build_verdict(result_count=0)["verdict"]

    def test_a_degraded_result_with_rows_is_not_marked_refused(self):
        verdict = build_verdict(result_count=2, best_score=9.0, timed_out=True)["verdict"]
        assert verdict["state"] == "degraded"
        assert "absence_refused" not in verdict


# --- the refusal, and the one rule that does it -----------------------------


class TestTheRefusalRoutesThroughOneRule:
    def test_absence_refusal_names_the_semantic_reason(self):
        """Not "the verdict was degraded" — every other gate here describes
        something that could be different next time; this one cannot."""
        _handoff_mod.clear_absences()
        verdict = build_verdict(
            result_count=0,
            scanned_symbols=2,
            scanned_files=2,
            absence_unprovable="a semantic ranking scores similarity.",
        )["verdict"]
        ref, refusal = _handoff_mod.note_absence(
            "search_symbols", "test/semantic", "zzz", verdict
        )
        assert ref is None
        assert "scores similarity" in refusal
        assert "without the semantic channel" in refusal
        _handoff_mod.clear_absences()

    def test_the_recorded_scan_is_still_citable_and_returns_the_reason(self):
        """A refused scan is recorded, so citing it gets the reason rather than an
        unknown-ref error."""
        _handoff_mod.clear_absences()
        verdict = build_verdict(
            result_count=0, absence_unprovable="a semantic ranking scores similarity."
        )["verdict"]
        _handoff_mod.note_absence("search_symbols", "test/semantic", "zzz", verdict)
        ref = _handoff_mod.absence_ref_for("search_symbols", "test/semantic", "zzz", {})
        record = _handoff_mod.absence_record(ref)
        assert record is not None
        assert "scores similarity" in _handoff_mod.absence_refusal(record)
        _handoff_mod.clear_absences()

    def test_finalize_refuses_a_cited_semantic_scan_with_that_reason(self):
        _handoff_mod.clear_absences()
        _handoff_mod.clear_handoffs()
        verdict = build_verdict(
            result_count=0, absence_unprovable="a semantic ranking scores similarity."
        )["verdict"]
        _handoff_mod.note_absence("search_symbols", "test/semantic", "zzz", verdict)
        ref = _handoff_mod.absence_ref_for("search_symbols", "test/semantic", "zzz", {})
        out = _handoff_mod.finalize_handoff(
            repo="test/semantic",
            task="t",
            sections=[{"heading": "H", "content": "c"}],
            evidence_refs=[ref],
        )
        assert "error" in out
        assert "refused" in out["error"]
        assert "scores similarity" in out["refused_absence"][0]["reason"]
        _handoff_mod.clear_absences()
        _handoff_mod.clear_handoffs()


# --- a refused scan reaches a default-configured caller ---------------------


class TestARefusedScanIsDisclosed:
    """Every gate since v1.108.166 works by DOWNGRADING to `degraded`, and the
    carrier fired for `absent` alone — so the better a gate worked, the less the
    caller was told. jcodemunch ships `meta_fields: []` by DEFAULT, so on a default
    install a refused scan arrived as a bare empty response with no reason at all.
    """

    def _r184_call(self, tool, args):
        from jcodemunch_mcp import server

        res = asyncio.run(server.call_tool(tool, args))
        raw = res.content[0].text if hasattr(res, "content") else res[0].text
        return json.loads(raw)

    @staticmethod
    def _r184_refusal(body):
        """The refusal reason, from wherever this install leaves it.

        On a default install (`meta_fields: []`) the verdict is deleted before the
        agent sees it and the re-attached carrier is the only thing holding the
        reason. A test that reads only the verdict passes on a permissive config
        and proves nothing about the shipped path.
        """
        meta = body.get("_meta") or {}
        verdict = meta.get("verdict") or {}
        return verdict.get("absence_blocked_by") or (
            meta.get("absence_evidence") or {}
        ).get("blocked_by")

    def test_the_verdict_marks_a_refused_zero_result(self, _r184_indexed):
        result = _r184_search(
            _r184_indexed,
            query="zzz_nothing_like_this",
            semantic=True,
            semantic_only=True,
            embed=_r184_away,
        )
        assert result["_meta"]["verdict"]["absence_refused"] is True

    def test_the_reason_is_attached_in_band(self, _r184_indexed, monkeypatch):
        monkeypatch.setenv("CODE_INDEX_PATH", _r184_indexed["storage"])
        with patch(
            "jcodemunch_mcp.tools.embed_repo.embed_texts", side_effect=_r184_away
        ):
            body = self._r184_call(
                "search_symbols",
                {
                    "repo": _r184_indexed["repo"],
                    "query": "zzz_nothing_like_this",
                    "semantic": True,
                    "semantic_only": True,
                    "format": "json",
                },
            )
        assert "scores similarity" in self._r184_refusal(body)

    def test_the_carrier_survives_the_default_meta_fields_strip(
        self, _r184_indexed, monkeypatch
    ):
        """The half that matters on a real install."""
        monkeypatch.setenv("CODE_INDEX_PATH", _r184_indexed["storage"])
        from jcodemunch_mcp import config as config_module

        original = config_module.get

        def _r184_no_meta(key, default=None, **kwargs):
            if key == "meta_fields":
                return []
            return original(key, default, **kwargs)

        monkeypatch.setattr(config_module, "get", _r184_no_meta)
        with patch(
            "jcodemunch_mcp.tools.embed_repo.embed_texts", side_effect=_r184_away
        ):
            body = self._r184_call(
                "search_symbols",
                {
                    "repo": _r184_indexed["repo"],
                    "query": "zzz_nothing_like_this",
                    "semantic": True,
                    "semantic_only": True,
                    "format": "json",
                },
            )
        assert "verdict" not in body["_meta"]
        carrier = body["_meta"]["absence_evidence"]
        assert carrier["citable"] is False
        assert "scores similarity" in carrier["blocked_by"]

    def test_a_lexical_packed_empty_scan_is_disclosed_too(
        self, _r184_indexed, monkeypatch
    ):
        """Same widening, on the gate that has been silent since v1.108.177."""
        monkeypatch.setenv("CODE_INDEX_PATH", _r184_indexed["storage"])
        body = self._r184_call(
            "search_symbols",
            {
                "repo": _r184_indexed["repo"],
                "query": "refresh_token",
                "token_budget": 1,
                "format": "json",
            },
        )
        assert body["result_count"] == 0
        assert "omitted to fit" in self._r184_refusal(body)

    def test_a_citable_absence_still_gets_its_token(self, _r184_indexed, monkeypatch):
        """Non-vacuity: the widening must not have replaced the citable path."""
        monkeypatch.setenv("CODE_INDEX_PATH", _r184_indexed["storage"])
        body = self._r184_call(
            "search_symbols",
            {
                "repo": _r184_indexed["repo"],
                "query": "zzz_absent_lexically",
                "format": "json",
            },
        )
        meta = body["_meta"]
        carrier = (meta.get("verdict") or {}).get("evidence_ref") or (
            meta.get("absence_evidence") or {}
        ).get("ref")
        assert carrier and carrier.startswith("absent:")
        assert self._r184_refusal(body) is None


# --- the receipt contract follows the mode ----------------------------------


class TestTheReceiptFollowsTheMode:
    def test_a_semantic_receipt_declares_semantic_completeness(self, _r184_indexed, monkeypatch):
        """The gate caught this: gaining a verdict made the exit mintable under a
        declaration reading "lexical BM25 over the inverted index"."""
        monkeypatch.setenv("CODE_INDEX_PATH", _r184_indexed["storage"])
        from jcodemunch_mcp import server

        with patch(
            "jcodemunch_mcp.tools.embed_repo.embed_texts", side_effect=_r184_toward
        ):
            res = asyncio.run(
                server.call_tool(
                    "search_symbols",
                    {
                        "repo": _r184_indexed["repo"],
                        "query": "refresh_token",
                        "semantic": True,
                        "receipt": True,
                        "format": "json",
                    },
                )
            )
        body = json.loads(res[0].text if not hasattr(res, "content") else res.content[0].text)
        ids = ((body.get("_meta") or {}).get("receipts") or {}).get("ids") or []
        assert ids, "the registered producer minted nothing for the semantic exit"
        envelope = _r184_receipts.lookup(ids[0])[0]
        declared = envelope["capabilities"]["completeness"]
        assert "cosine" in declared
        assert "inverted-index narrowing" in declared
        # And identity records which mode ran, so a semantic and a lexical search
        # of one string cannot collide on one evidence id.
        assert envelope["effective_search"]["mode"]["search_mode"] == "hybrid"

    def test_the_default_completeness_is_unchanged_for_the_lexical_exit(self):
        producer = _r184_producers.PRODUCERS["search_symbols"]
        assert "BM25 over the inverted index" in producer.completeness_for(None)
        assert "BM25 over the inverted index" in producer.completeness_for("unlisted")

    def test_an_unreviewed_mode_falls_back_rather_than_inheriting_silently(self):
        """A mode absent from the table is a visible omission: it gets the default
        and the test above pins what the default says. `fusion` joined the table in
        v1.108.185 when that exit gained a verdict."""
        producer = _r184_producers.PRODUCERS["search_symbols"]
        assert set(producer.completeness_by_mode) == {"hybrid", "semantic_only", "fusion"}


class TestThePublishedSchema:
    def test_the_new_verdict_blocks_are_published(self):
        """Any new block is published in the same commit that emits it."""
        schema = json.loads(
            (_R184_ROOT / "schemas" / "retrieval-verdict.schema.json").read_text(
                encoding="utf-8"
            )
        )
        assert "absence_unprovable" in schema["properties"]
        assert "absence_refused" in schema["properties"]

    def test_a_live_semantic_verdict_validates(self, _r184_indexed):
        schema = json.loads(
            (_R184_ROOT / "schemas" / "retrieval-verdict.schema.json").read_text(
                encoding="utf-8"
            )
        )
        for kwargs, embed in (
            ({"semantic": True}, _r184_toward),
            ({"semantic": True, "semantic_only": True}, _r184_away),
            ({"semantic": True, "token_budget": 1}, _r184_toward),
        ):
            result = _r184_search(
                _r184_indexed, query="zzz_nothing_like_this", embed=embed, **kwargs
            )
            jsonschema.validate(result["_meta"]["verdict"], schema)

    def test_the_channels_report_that_semantic_actually_ran(self, _r184_indexed):
        result = _r184_search(_r184_indexed, query="refresh_token", semantic=True)
        assert result["_meta"]["verdict"]["channels"]["semantic"] == "ok"


class TestWhatWasNotFixed:
    """Stated as tests so the boundary is checkable rather than remembered."""

    def test_the_semantic_exit_is_the_only_one_that_cannot_prove_absence(self):
        """v1.108.185 fixed the fusion exits, and the review reached the OPPOSITE
        conclusion: fusion's lexical and identity channels are complete passes, so
        an empty fused set is a corpus fact and `absent` is reachable there. Only
        a ranking that stands ALONE cannot prove absence."""
        source = (
            _R184_ROOT / "src" / "jcodemunch_mcp" / "tools" / "search_symbols.py"
        ).read_text(encoding="utf-8")
        semantic = source.split("def _search_symbols_semantic")[1].split(
            "def _search_symbols_fusion"
        )[0]
        fusion = source.split("def _search_symbols_fusion")[1]
        # The keyword ARGUMENT, not the word: the fusion exit carries a comment
        # explaining why it does not pass one, and that comment should stay.
        assert "absence_unprovable=(" in semantic
        assert "absence_unprovable=" not in fusion
