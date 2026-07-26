"""The fusion exits get a verdict, and fusion CAN prove absence (v1.108.185).

Last two exits in the suite without one. Unlike the semantic exit fixed in
v1.108.184, neither asserted anything false — no verdict and no absence prose
either — so a zero-result fusion search came back as a bare empty response:
nothing wrong, and nothing honest.

**The review reached the opposite conclusion to v1.108.184's, which is why modes
get reviewed rather than having a rule ported onto them.** A semantic zero result
cannot prove absence because it rests on embedding geometry. A fusion zero result
can: ``build_lexical_channel`` and ``build_identity_channel`` each score EVERY
eligible candidate with no cap, and ``fuse`` keeps every symbol appearing in ANY
channel, so an empty fused set means every candidate scored zero on both BM25 and
identity — the same corpus fact the lexical path's absence rests on. The
similarity channel can only ADD symbols, never remove one, so its absence weakens
the ranking and cannot manufacture a false absence.

**Getting there required fixing a defect that made `absent` unreachable anyway.**
``build_structural_channel`` treated an EMPTY ``candidate_ids`` set ("restrict to
nothing") the same as ``None`` ("no restriction"), because the guard was a falsy
check. Both callers pass ``lexical ∪ identity``, so a query matching nothing
produced an empty set, skipped the filter, and let every symbol with nonzero
PageRank into the channel. A no-match fusion query therefore returned the whole
repository ranked by centrality — and said "Confident matches returned", because
fusion had no verdict for anyone to check.

Helper names are prefixed ``_r185``; a plain ``_state`` helper once outranked
``token_tracker._State`` for a golden replay query and cost a red CI run.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import jsonschema
import pytest

from jcodemunch_mcp import handoff as _handoff_mod
from jcodemunch_mcp.evidence import producers as _r185_producers
from jcodemunch_mcp.evidence import receipts as _r185_receipts
from jcodemunch_mcp.retrieval.signal_fusion import build_structural_channel
from jcodemunch_mcp.retrieval.verdict import retrieval_verdict_for_index
from jcodemunch_mcp.tools.get_ranked_context import get_ranked_context
from jcodemunch_mcp.tools.search_symbols import search_symbols

_R185_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def _r185_env(tmp_path, monkeypatch):
    """A real two-file folder index. Real folder, not a synthetic save_index: a
    hand-built index has no source root, so freshness reads `unknown` and that
    gate fires first, masking whatever is under test."""
    store = tmp_path / "idx"
    monkeypatch.setenv("CODE_INDEX_PATH", str(store))
    proj = tmp_path / "proj"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "a.py").write_text(
        "import os\n\n\ndef refresh_token(user):\n    return user\n", encoding="utf-8"
    )
    (proj / "src" / "b.py").write_text(
        "import os\n\n\ndef render_html(template):\n    return template\n", encoding="utf-8"
    )
    from jcodemunch_mcp.tools.index_folder import index_folder

    _handoff_mod.clear_absences()
    _handoff_mod.clear_handoffs()
    _r185_receipts.clear_receipts()
    repo = index_folder(path=str(proj), use_ai_summaries=False, storage_path=str(store))["repo"]
    yield {"repo": repo, "storage": str(store)}
    _handoff_mod.clear_absences()
    _handoff_mod.clear_handoffs()
    _r185_receipts.clear_receipts()


def _r185_fusion(env, **kwargs):
    return search_symbols(env["repo"], fusion=True, storage_path=env["storage"], **kwargs)


def _r185_context(env, **kwargs):
    return get_ranked_context(env["repo"], fusion=True, storage_path=env["storage"], **kwargs)


def _r185_call(tool, args):
    from jcodemunch_mcp import server

    res = asyncio.run(server.call_tool(tool, args))
    raw = res.content[0].text if hasattr(res, "content") else res[0].text
    return json.loads(raw)


def _r185_token(body):
    """The absence token or the refusal reason, under either meta_fields setting."""
    meta = body.get("_meta") or {}
    verdict = meta.get("verdict") or {}
    carrier = meta.get("absence_evidence") or {}
    return (
        verdict.get("evidence_ref") or carrier.get("ref"),
        verdict.get("absence_blocked_by") or carrier.get("blocked_by"),
    )


# --- the defect that made `absent` unreachable -------------------------------


class TestTheStructuralBackfill:
    """An empty candidate set means "nothing", not "everything"."""

    def test_none_means_no_restriction(self):
        symbols = [{"id": "a.py::f#function", "file": "a.py"}]
        assert build_structural_channel(symbols, {"a.py": 0.5}, None).ranked_ids == [
            "a.py::f#function"
        ]

    def test_an_empty_set_means_restrict_to_nothing(self):
        """Fails on the old code, which returned every symbol here."""
        symbols = [{"id": "a.py::f#function", "file": "a.py"}]
        assert build_structural_channel(symbols, {"a.py": 0.5}, set()).ranked_ids == []

    def test_a_populated_set_still_restricts_to_it(self):
        symbols = [
            {"id": "a.py::f#function", "file": "a.py"},
            {"id": "b.py::g#function", "file": "b.py"},
        ]
        ranked = build_structural_channel(
            symbols, {"a.py": 0.5, "b.py": 0.4}, {"a.py::f#function"}
        ).ranked_ids
        assert ranked == ["a.py::f#function"]

    def test_a_no_match_fusion_query_returns_nothing_at_all(self, _r185_env):
        """The live consequence. This used to return the whole repository ranked by
        PageRank, with the verdict — had there been one — reading `ok`."""
        result = _r185_fusion(_r185_env, query="zzz_nothing_like_this_anywhere")
        assert result["result_count"] == 0
        assert result["results"] == []

    def test_the_same_query_without_fusion_also_returns_nothing(self, _r185_env):
        """Control: the lexical path never had the backfill, so the two paths now
        agree about what "no match" means."""
        result = search_symbols(
            _r185_env["repo"],
            query="zzz_nothing_like_this_anywhere",
            storage_path=_r185_env["storage"],
        )
        assert result["result_count"] == 0


class TestAReadThatWrote:
    """The embeddings probe moved the .db mtime, which blocked `absent` anyway.

    ``EmbeddingStore._connect`` runs ``PRAGMA journal_mode = WAL`` and a
    CREATE-TABLE script on EVERY connection, so the fusion exit's "are there
    embeddings for this repo" probe wrote to the database it was reading. That
    bumped ``_db_mtime_ns``, which made ``index_changed_since_load`` report
    `rebuilding` and ``moved_during_scan`` fire — so the FIRST fusion search in any
    process downgraded to `degraded` and could not reach `absent` however carefully
    the verdict was wired.

    ⚠ ``mode=ro`` alone does NOT fix it: read-only in SQLite means "cannot modify
    the DATABASE", not "cannot touch the filesystem", and a plain read-only
    connection to a WAL database still creates the ``-wal`` and ``-shm`` sidecars.
    ``_db_mtime_ns`` maxes over the ``.db`` AND the ``-wal``, so the mtime moved
    regardless. ``immutable=1`` is the flag that guarantees no sidecar.
    """

    @staticmethod
    def _r185_sidecars(db):
        return {
            suffix: (Path(str(db) + suffix).exists())
            for suffix in ("", "-wal", "-shm")
        }

    def _r185_db(self, env):
        from jcodemunch_mcp.storage import IndexStore
        from jcodemunch_mcp.tools._utils import resolve_repo

        owner, name = resolve_repo(env["repo"], env["storage"])
        return Path(IndexStore(base_path=env["storage"])._sqlite._db_path(owner, name))

    def test_a_fusion_search_does_not_move_the_index_mtime(self, _r185_env):
        from jcodemunch_mcp.storage.sqlite_store import _db_mtime_ns

        db = self._r185_db(_r185_env)
        before = _db_mtime_ns(db)
        _r185_fusion(_r185_env, query="zzz_nothing_like_this_anywhere")
        assert _db_mtime_ns(db) == before

    def test_a_fusion_search_creates_no_wal_sidecar(self, _r185_env):
        """The specific mechanism, so a future `mode=ro` regression is caught."""
        db = self._r185_db(_r185_env)
        _r185_fusion(_r185_env, query="refresh_token")
        assert Path(str(db) + "-wal").exists() is False

    def test_the_first_fusion_search_of_a_process_can_still_prove_absence(
        self, _r185_env
    ):
        """The consequence, stated as the thing a user would notice. No warm-up
        search: the FIRST fusion call is the one that used to be poisoned."""
        result = _r185_fusion(_r185_env, query="zzz_nothing_like_this_anywhere")
        verdict = result["_meta"]["verdict"]
        assert verdict["state"] == "absent"
        assert "moved_during_scan" not in verdict
        assert verdict["channels"]["index"] != "rebuilding"

    def test_the_readonly_reader_returns_the_same_vectors_as_the_writer(self, tmp_path):
        """Non-vacuity for the read path: it must still actually read."""
        from jcodemunch_mcp.storage.embedding_store import EmbeddingStore

        # `set_dimension` is deliberately not called: the `meta` table belongs to
        # the main index schema, not this store, and EmbeddingStore only ever runs
        # against a repo database that already has one.
        db = tmp_path / "vec.db"
        EmbeddingStore(db).set_many({"a.py::f#function": [0.1, 0.2, 0.3]})
        loaded = EmbeddingStore(db).get_all_readonly()
        assert set(loaded) == {"a.py::f#function"}
        assert len(loaded["a.py::f#function"]) == 3

    def test_the_readonly_reader_is_empty_on_a_missing_file(self, tmp_path):
        from jcodemunch_mcp.storage.embedding_store import EmbeddingStore

        assert EmbeddingStore(tmp_path / "nope.db").get_all_readonly() == {}


# --- fusion can prove absence ------------------------------------------------


class TestFusionCanProveAbsence:
    def test_a_genuine_fusion_zero_result_reaches_absent(self, _r185_env):
        """The conclusion of the review, and the opposite of v1.108.184's."""
        result = _r185_fusion(_r185_env, query="zzz_nothing_like_this_anywhere")
        assert result["_meta"]["verdict"]["state"] == "absent"

    def test_it_carries_no_absence_unprovable_block(self, _r185_env):
        """A semantic zero result carries one; fusion's lexical and identity
        channels are complete passes, so this one has no need of it."""
        result = _r185_fusion(_r185_env, query="zzz_nothing_like_this_anywhere")
        assert "absence_unprovable" not in result["_meta"]["verdict"]

    def test_the_context_fusion_exit_reaches_absent_too(self, _r185_env):
        result = _r185_context(_r185_env, query="zzz_nothing_like_this_anywhere")
        assert result["items_included"] == 0
        assert result["_meta"]["verdict"]["state"] == "absent"

    def test_a_fusion_absence_is_citable_through_the_dispatcher(self, _r185_env):
        """End to end on the real route: the scan earns a token."""
        body = _r185_call(
            "search_symbols",
            {
                "repo": _r185_env["repo"],
                "query": "zzz_nothing_like_this_anywhere",
                "fusion": True,
                "format": "json",
            },
        )
        ref, refusal = _r185_token(body)
        assert refusal is None
        assert ref and ref.startswith("absent:")

    def test_the_scan_behind_that_token_is_on_record(self, _r185_env):
        body = _r185_call(
            "search_symbols",
            {
                "repo": _r185_env["repo"],
                "query": "zzz_nothing_like_this_anywhere",
                "fusion": True,
                "format": "json",
            },
        )
        ref, _ = _r185_token(body)
        record = _handoff_mod.absence_record(ref)
        assert record is not None
        assert _handoff_mod.absence_refusal(record) is None
        assert record["scanned"]["symbols"] > 0


# --- every exit, gated -------------------------------------------------------


class TestEveryFusionExitIsGated:
    def test_a_packed_empty_fusion_response_is_not_an_absence(self, _r185_env):
        """The item-1 shape lives on this exit too: it packs by token_budget."""
        result = _r185_fusion(_r185_env, query="refresh_token", token_budget=1)
        verdict = result["_meta"]["verdict"]
        assert result["result_count"] == 0
        assert verdict["state"] == "degraded"
        assert verdict["omitted"]["matches_found"] > 0
        assert verdict["absence_refused"] is True

    def test_an_empty_scope_proves_nothing_on_search_symbols(self, _r185_env):
        """The filters selected nothing, so not a byte was read (#377 item 9)."""
        result = _r185_fusion(_r185_env, query="refresh_token", file_pattern="nope/*.py")
        verdict = result["_meta"]["verdict"]
        assert result["result_count"] == 0
        assert verdict["state"] == "degraded"
        assert verdict["incomplete"]["reason"] == "empty_scope"

    def test_an_empty_scope_proves_nothing_on_get_ranked_context(self, _r185_env):
        result = _r185_context(_r185_env, query="refresh_token", scope="nope/*.py")
        verdict = result["_meta"]["verdict"]
        assert result["items_included"] == 0
        assert verdict["incomplete"]["reason"] == "empty_scope"

    def test_a_refused_fusion_scan_says_why_through_the_dispatcher(self, _r185_env):
        body = _r185_call(
            "search_symbols",
            {
                "repo": _r185_env["repo"],
                "query": "refresh_token",
                "fusion": True,
                "file_pattern": "nope/*.py",
                "format": "json",
            },
        )
        ref, refusal = _r185_token(body)
        assert ref is None
        assert "nothing was searched" in refusal or "selected no eligible" in refusal

    def test_a_fusion_hit_is_still_a_hit(self, _r185_env):
        """The fix must not cost either exit its actual job."""
        result = _r185_fusion(_r185_env, query="refresh_token")
        assert result["result_count"] >= 1
        assert result["_meta"]["verdict"]["state"] == "ok"
        assert "⚠ warning" not in result

    def test_a_context_fusion_hit_is_still_a_hit(self, _r185_env):
        result = _r185_context(_r185_env, query="refresh_token")
        assert result["items_included"] >= 1
        assert result["_meta"]["verdict"]["state"] == "ok"

    def test_the_context_fusion_exit_reports_semantic_off_honestly(self, _r185_env):
        """It builds no similarity channel at all, whatever the ranking ledger
        records for it."""
        result = _r185_context(_r185_env, query="refresh_token")
        assert result["_meta"]["verdict"]["channels"]["semantic"] == "off"
        assert "similarity" not in result["_meta"]["channels"]

    def test_prose_parity_with_the_non_fusion_exits(self, _r185_env):
        """The same question must not get a differently-honest answer depending on
        which ranking mode ran."""
        result = _r185_fusion(_r185_env, query="zzz_nothing_like_this_anywhere")
        assert "Do not claim this feature exists" in result["⚠ warning"]
        assert result["negative_evidence"]["verdict"] == "no_implementation_found"


class TestNoExitIsLeftWithoutAVerdict:
    """The structural check, so the next exit cannot be missed by inspection."""

    @pytest.mark.parametrize(
        "module,exits",
        [
            (
                "search_symbols.py",
                ("_search_symbols_semantic", "_search_symbols_fusion"),
            ),
            ("get_ranked_context.py", ("_get_ranked_context_fusion",)),
        ],
    )
    def test_every_diverging_exit_builds_a_verdict(self, module, exits):
        source = (_R185_ROOT / "src" / "jcodemunch_mcp" / "tools" / module).read_text(
            encoding="utf-8"
        )
        for name in exits:
            body = source.split(f"def {name}")[1]
            # Either it calls build_verdict directly or through the index-aware
            # wrapper; what matters is that a verdict exists on every return.
            assert (
                "build_verdict" in body or "retrieval_verdict_for_index" in body
            ), name

    def test_the_no_candidate_returns_carry_one_too(self):
        """These were the silent ones: a bare empty dict with no verdict."""
        for module in ("search_symbols.py", "get_ranked_context.py"):
            source = (_R185_ROOT / "src" / "jcodemunch_mcp" / "tools" / module).read_text(
                encoding="utf-8"
            )
            head = source.split("if not candidates:")[1][:1600]
            assert "verdict" in head, module


# --- the shared wrapper ------------------------------------------------------


class TestTheIndexAwareWrapper:
    """Three exits shipped with no verdict because adding one meant reproducing
    five call patterns by hand. The wrapper is the structural answer to that."""

    def test_it_binds_the_five_index_signals(self, _r185_env):
        from jcodemunch_mcp.storage import IndexStore
        from jcodemunch_mcp.tools._utils import resolve_repo

        owner, name = resolve_repo(_r185_env["repo"], _r185_env["storage"])
        index = IndexStore(base_path=_r185_env["storage"]).load_index(owner, name)
        out = retrieval_verdict_for_index(index, result_count=0, query_terms=["zzz"])
        verdict = out["verdict"]
        assert verdict["state"] == "absent"
        assert verdict["channels"]["index"] == "not_tracked"
        assert verdict["working_tree"]["state"] in (
            "clean",
            "dirty_in_scope",
            "dirty_outside_scope",
            "unknown",
            "not_applicable",
        )
        assert verdict["scorer"] >= 1

    def test_it_returns_the_negative_evidence_block_too(self, _r185_env):
        from jcodemunch_mcp.storage import IndexStore
        from jcodemunch_mcp.tools._utils import resolve_repo

        owner, name = resolve_repo(_r185_env["repo"], _r185_env["storage"])
        index = IndexStore(base_path=_r185_env["storage"]).load_index(owner, name)
        out = retrieval_verdict_for_index(index, result_count=0)
        assert out["negative_evidence"]["verdict"] == "no_implementation_found"

    def test_the_semantic_channel_is_stated_not_probed(self, _r185_env):
        from jcodemunch_mcp.storage import IndexStore
        from jcodemunch_mcp.tools._utils import resolve_repo

        owner, name = resolve_repo(_r185_env["repo"], _r185_env["storage"])
        index = IndexStore(base_path=_r185_env["storage"]).load_index(owner, name)
        assert (
            retrieval_verdict_for_index(index, result_count=1, semantic_channel="ok")[
                "verdict"
            ]["channels"]["semantic"]
            == "ok"
        )
        assert (
            retrieval_verdict_for_index(index, result_count=1)["verdict"]["channels"][
                "semantic"
            ]
            == "off"
        )

    def test_the_working_tree_is_measured_only_for_a_zero_result(self, _r185_env):
        """Probing the tree on every search would price a subprocess into the
        answers that do not need it."""
        from jcodemunch_mcp.storage import IndexStore
        from jcodemunch_mcp.tools._utils import resolve_repo

        owner, name = resolve_repo(_r185_env["repo"], _r185_env["storage"])
        index = IndexStore(base_path=_r185_env["storage"]).load_index(owner, name)
        assert "working_tree" not in retrieval_verdict_for_index(
            index, result_count=3
        )["verdict"]


# --- the receipt contract ----------------------------------------------------


class TestTheReceiptFollowsTheMode:
    def test_a_fusion_receipt_declares_fusion_completeness(self, _r185_env):
        """The gate fired a second time: giving these exits a verdict made them
        mintable under a declaration written for a different operation."""
        body = _r185_call(
            "search_symbols",
            {
                "repo": _r185_env["repo"],
                "query": "refresh_token",
                "fusion": True,
                "receipt": True,
                "format": "json",
            },
        )
        ids = ((body.get("_meta") or {}).get("receipts") or {}).get("ids") or []
        assert ids, "the fusion exit minted nothing"
        envelope = _r185_receipts.lookup(ids[0])[0]
        declared = envelope["capabilities"]["completeness"]
        assert "Weighted Reciprocal Rank" in declared
        assert "corpus fact" in declared
        assert envelope["effective_search"]["mode"]["search_mode"] == "fusion"

    def test_a_fusion_absence_receipt_can_prove_absence(self, _r185_env):
        """Contrast with the semantic exit, whose absence receipts never can."""
        body = _r185_call(
            "search_symbols",
            {
                "repo": _r185_env["repo"],
                "query": "zzz_nothing_like_this_anywhere",
                "fusion": True,
                "receipt": True,
                "format": "json",
            },
        )
        ids = ((body.get("_meta") or {}).get("receipts") or {}).get("ids") or []
        assert ids
        envelope = next(
            _r185_receipts.lookup(i)[0]
            for i in ids
            if _r185_receipts.is_absence_kind(_r185_receipts.lookup(i)[0]["proof_kind"])
        )
        assert envelope["capabilities"]["proves_absence"] is True

    def test_fusion_identity_differs_from_the_lexical_one(self, _r185_env):
        """A fused search and a lexical search of one string are different
        operations and must not collide on one evidence id."""
        lexical = _r185_call(
            "search_symbols",
            {
                "repo": _r185_env["repo"],
                "query": "refresh_token",
                "receipt": True,
                "format": "json",
            },
        )
        fused = _r185_call(
            "search_symbols",
            {
                "repo": _r185_env["repo"],
                "query": "refresh_token",
                "fusion": True,
                "receipt": True,
                "format": "json",
            },
        )
        a = ((lexical.get("_meta") or {}).get("receipts") or {}).get("ids") or []
        b = ((fused.get("_meta") or {}).get("receipts") or {}).get("ids") or []
        assert a and b
        assert set(a).isdisjoint(set(b))

    def test_every_search_mode_the_code_emits_is_declared(self):
        """Drift guard. A mode falls back to the default silently, so the only
        thing stopping a new exit inheriting the wrong declaration is this."""
        import re

        for module, tool in (
            ("search_symbols.py", "search_symbols"),
            ("get_ranked_context.py", "get_ranked_context"),
        ):
            source = (_R185_ROOT / "src" / "jcodemunch_mcp" / "tools" / module).read_text(
                encoding="utf-8"
            )
            emitted: set = set()
            for line in source.splitlines():
                if '"search_mode"' in line:
                    emitted.update(re.findall(r'"([a-z_]+)"', line))
            emitted.discard("search_mode")
            declared = set(_r185_producers.PRODUCERS[tool].completeness_by_mode)
            assert emitted, module
            assert emitted <= declared, (module, emitted - declared)


class TestThePublishedSchema:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"query": "refresh_token"},
            {"query": "zzz_nothing_like_this_anywhere"},
            {"query": "refresh_token", "token_budget": 1},
            {"query": "refresh_token", "file_pattern": "nope/*.py"},
        ],
    )
    def test_every_fusion_verdict_validates(self, _r185_env, kwargs):
        schema = json.loads(
            (_R185_ROOT / "schemas" / "retrieval-verdict.schema.json").read_text(
                encoding="utf-8"
            )
        )
        result = _r185_fusion(_r185_env, **kwargs)
        jsonschema.validate(result["_meta"]["verdict"], schema)

    def test_the_context_fusion_verdicts_validate(self, _r185_env):
        schema = json.loads(
            (_R185_ROOT / "schemas" / "retrieval-verdict.schema.json").read_text(
                encoding="utf-8"
            )
        )
        for kwargs in ({"query": "refresh_token"}, {"query": "zzz_nothing_at_all"}):
            jsonschema.validate(
                _r185_context(_r185_env, **kwargs)["_meta"]["verdict"], schema
            )
