"""Deletion safety as proof obligations.

The cases here are the three gaps from the 2026-03-18 dead-code A/B
(`benchmarks/ab-test-dead-code-2026-03-18.md`), rebuilt as a fixture and
re-measured against 1.108.213 on 2026-08-02. Two of the three were already
fixed at the tool layer. The one that survived is Gap 2, and it survived
because it was never a capability defect: `check_references` answers the
export-level question correctly and no aggregate caller asked it.

`test_dead_export_in_live_file_is_the_gap_2_case` is therefore the load-bearing
test in this file. It encodes the exact shape an agent got wrong 6 times out of
6 while holding a tool that would have answered it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jcodemunch_mcp.investigator import (
    REFUTED,
    SATISFIED,
    UNESTABLISHED,
    Obligation,
    investigate_deletion_safety,
)
from jcodemunch_mcp.investigator.deletion_safety import (
    DYNAMIC,
    NOT_ESTABLISHED,
    SAFE,
    STATIC,
    STATIC_CLEAR,
    UNSAFE,
    _verdict,
)
from jcodemunch_mcp.tools.index_folder import index_folder

# Mirrors the A/B's three gap shapes in one tree.
FIXTURE = {
    "package.json": '{"name":"fx","version":"1.0.0","type":"module","main":"src/main.js"}',
    "src/main.js": (
        "import { routes } from './router/routes.js';\n"
        "import { renderTable } from './consumer.js';\n"
        "export function boot() { return renderTable(routes); }\n"
    ),
    # Gap 1: lazy route via dynamic import()
    "src/router/routes.js": (
        "export const routes = [\n"
        "  { path: '/lists', component: () => import('../views/Lists.vue') },\n"
        "];\n"
    ),
    "src/views/Lists.vue": "<script>\nexport default { name: 'Lists' };\n</script>\n",
    # Gap 2: live file, one live export, one dead export
    "src/composables/useDocumentColumns.js": (
        "export function useDocumentColumns() { return 1; }\n"
        "export const TIMESTAMP_COLUMNS = ['created_at'];\n"
    ),
    "src/consumer.js": (
        "import { useDocumentColumns } from './composables/useDocumentColumns.js';\n"
        "export function renderTable(routes) "
        "{ return useDocumentColumns() + routes.length; }\n"
    ),
    # Gap 3: transitively dead chain
    "src/loaders/storageLoader.js": "export function loadFromStorage() { return null; }\n",
    "src/loaders/deadLoader.js": (
        "import { loadFromStorage } from './storageLoader.js';\n"
        "export function firestoreDocumentLoader() { return loadFromStorage(); }\n"
    ),
}


@pytest.fixture(scope="module")
def repo(tmp_path_factory) -> tuple[str, str]:
    root = tmp_path_factory.mktemp("investigator_fx")
    for rel, body in FIXTURE.items():
        p = Path(root) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    storage = str(Path(root) / ".index")
    res = index_folder(str(root), use_ai_summaries=False, storage_path=storage)
    return res.get("repo", str(root)), storage


def _ob(result: dict, name: str) -> dict:
    for o in result["obligations"]:
        if o["obligation"] == name:
            return o
    raise AssertionError(f"obligation {name!r} missing from {result['obligations']}")


# --- The honesty rule, tested in isolation --------------------------------- #

class TestVerdictRule:
    """`_verdict` is the only place a conclusion may be drawn. If these pass and
    the module still reports a bad verdict, the bug is in obligation status
    assignment, not in the rule."""

    def test_unestablished_static_obligation_never_yields_safe(self):
        obs = [
            Obligation("a", "?", SATISFIED),
            Obligation("b", "?", UNESTABLISHED),
        ]
        assert _verdict(obs) == NOT_ESTABLISHED

    def test_static_clear_when_only_the_dynamic_channel_is_open(self):
        """Source said everything it can say. Runtime is a separate channel and
        its silence is not a source failure."""
        obs = [
            Obligation("a", "?", SATISFIED, channel=STATIC),
            Obligation("rt", "?", UNESTABLISHED, channel=DYNAMIC),
        ]
        assert _verdict(obs) == STATIC_CLEAR

    def test_static_clear_requires_every_static_obligation_settled(self):
        """THE distinction static_clear exists to protect. An unanswered
        SOURCE question is a gap in work we could have done; an unanswered
        RUNTIME question is a gap in evidence that may not exist. Collapsing
        them would let a failed reference check masquerade as a clean scan."""
        obs = [
            Obligation("a", "?", UNESTABLISHED, channel=STATIC),
            Obligation("rt", "?", UNESTABLISHED, channel=DYNAMIC),
        ]
        assert _verdict(obs) == NOT_ESTABLISHED

    def test_static_clear_never_outranks_a_refutation(self):
        obs = [
            Obligation("a", "?", REFUTED, channel=STATIC),
            Obligation("rt", "?", UNESTABLISHED, channel=DYNAMIC),
        ]
        assert _verdict(obs) == UNSAFE

    def test_safe_still_requires_the_dynamic_channel_too(self):
        """static_clear must not become a back door to `safe`."""
        obs = [
            Obligation("a", "?", SATISFIED, channel=STATIC),
            Obligation("rt", "?", SATISFIED, channel=DYNAMIC),
        ]
        assert _verdict(obs) == SAFE

    def test_all_satisfied_yields_safe(self):
        assert _verdict([Obligation("a", "?", SATISFIED)]) == SAFE

    def test_refuted_beats_unestablished(self):
        obs = [Obligation("a", "?", UNESTABLISHED), Obligation("b", "?", REFUTED)]
        assert _verdict(obs) == UNSAFE

    def test_refuted_beats_satisfied(self):
        obs = [Obligation("a", "?", SATISFIED), Obligation("b", "?", REFUTED)]
        assert _verdict(obs) == UNSAFE


# --- The three A/B gaps ---------------------------------------------------- #

class TestGapCases:

    def test_dead_export_in_live_file_is_the_gap_2_case(self, repo):
        """THE case. `useDocumentColumns.js` has a live importer, so file-level
        liveness says 'alive'. TIMESTAMP_COLUMNS is imported by nobody. The
        A/B's agent classified it alive in 6/6 iterations by stopping at the
        file. The obligation must be raised and satisfied on the EXPORT."""
        repo_id, storage = repo
        r = investigate_deletion_safety(repo_id, "TIMESTAMP_COLUMNS", storage_path=storage)
        assert _ob(r, "export_not_imported")["status"] == SATISFIED, (
            "export-level liveness must not inherit the file's liveness"
        )
        assert r["verdict"] != UNSAFE
        assert "export_not_imported" in r["satisfied_obligations"]

    def test_live_export_is_refuted(self, repo):
        """Same file, opposite verdict. If both exports get the same answer the
        investigation is reading the file, not the symbol."""
        repo_id, storage = repo
        r = investigate_deletion_safety(repo_id, "useDocumentColumns", storage_path=storage)
        assert r["verdict"] == UNSAFE
        assert "export_not_imported" in r["refuted_obligations"]
        assert any("consumer.js" in e for e in _ob(r, "export_not_imported")["evidence"])

    def test_transitively_dead_symbol_reports_a_cluster(self, repo):
        """Gap 3 at the obligation level. `loadFromStorage` IS imported, but only
        by `deadLoader.js`, which nothing imports. Breaking dead code is not
        breaking anything, so this must not be a flat refutation, and the caller
        must be told what else has to go."""
        repo_id, storage = repo
        r = investigate_deletion_safety(repo_id, "loadFromStorage", storage_path=storage)
        assert r["verdict"] != UNSAFE
        assert r.get("deletion_cluster"), "transitively dead cluster not surfaced"
        assert any("deadLoader" in f for f in r["deletion_cluster"])

    def test_gap_1_lazy_route_keeps_the_view_alive(self, repo):
        """`Lists.vue` is reachable only through `component: () => import(...)`.
        Before v1.8.1 the extractor missed that form and four live views read as
        dead. A regression here reintroduces alive-called-dead, the dangerous
        direction for a deletion tool."""
        from jcodemunch_mcp.tools.find_importers import find_importers

        repo_id, storage = repo
        res = find_importers(repo_id, "src/views/Lists.vue", storage_path=storage)
        assert res.get("importer_count", 0) >= 1, "dynamic import() no longer resolved"


class TestHonesty:

    def test_no_runtime_traces_leaves_the_obligation_unresolved(self, repo):
        """The most dangerous available lie: reporting 'no runtime evidence'
        when the truth is 'no traces were ever ingested'. Those are identical in
        a bare hit count and must not be identical here."""
        repo_id, storage = repo
        r = investigate_deletion_safety(repo_id, "TIMESTAMP_COLUMNS", storage_path=storage)
        rt = _ob(r, "no_runtime_evidence")
        assert rt["status"] == UNESTABLISHED
        assert any("no runtime traces" in e.lower() for e in rt["evidence"])

    def test_unresolved_obligation_blocks_a_safe_verdict(self, repo):
        """End-to-end statement of the rule: with runtime unresolvable, nothing
        in this fixture may come back `safe`, however clean the static evidence."""
        repo_id, storage = repo
        for sym in ("TIMESTAMP_COLUMNS", "firestoreDocumentLoader", "loadFromStorage"):
            r = investigate_deletion_safety(repo_id, sym, storage_path=storage)
            assert r["verdict"] != SAFE, f"{sym} claimed safe with an open obligation"
            assert r["verdict"] == STATIC_CLEAR
            assert "no_runtime_evidence" in r["unresolved_obligations"]

    def test_disabling_the_runtime_check_cannot_manufacture_safe(self, repo):
        """A caller opting out of the runtime channel must not thereby upgrade
        the verdict. Opting out is not evidence."""
        repo_id, storage = repo
        r = investigate_deletion_safety(
            repo_id, "TIMESTAMP_COLUMNS", include_runtime=False, storage_path=storage
        )
        assert r["verdict"] == STATIC_CLEAR
        assert "no_runtime_evidence" in r["unresolved_obligations"]

    def test_every_obligation_declares_its_channel(self, repo):
        repo_id, storage = repo
        r = investigate_deletion_safety(repo_id, "TIMESTAMP_COLUMNS", storage_path=storage)
        for o in r["obligations"]:
            assert o["channel"] in (STATIC, DYNAMIC), o

    def test_unknown_symbol_does_not_fabricate_a_verdict(self, repo):
        repo_id, storage = repo
        r = investigate_deletion_safety(repo_id, "NoSuchSymbolAnywhere", storage_path=storage)
        assert r["verdict"] == NOT_ESTABLISHED
        assert "error" in r

    def test_every_obligation_carries_its_evidence(self, repo):
        """A status with no evidence is an assertion. The point of the format is
        that a reader can check the reasoning without rerunning it."""
        repo_id, storage = repo
        r = investigate_deletion_safety(repo_id, "TIMESTAMP_COLUMNS", storage_path=storage)
        for o in r["obligations"]:
            assert o["evidence"], f"{o['obligation']} reported {o['status']} with no evidence"

    def test_investigation_is_read_only(self, repo):
        """Charter. The investigation reads and plans; it never mutates."""
        repo_id, storage = repo
        r = investigate_deletion_safety(repo_id, "TIMESTAMP_COLUMNS", storage_path=storage)
        assert r["_meta"]["charter"] == "read_only"
