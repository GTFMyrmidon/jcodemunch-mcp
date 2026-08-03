"""v1.108.226 — check_references stops discarding the defining file, and
index_repo stops failing on renamed repositories.

@rknighton, [#406](https://github.com/jgravelle/jcodemunch-mcp/issues/406) and
[#407](https://github.com/jgravelle/jcodemunch-mcp/issues/407).

**#406(1) — the whole file was skipped, not the definition.** `_check_single`
built `defining_files` from every file containing a symbol of the matching name
and `continue`d past those files entirely. The intent — "finding the name in the
defining file is not a reference" — was right; the implementation discarded the
file rather than the definition, so every same-file call site was lost. A helper
defined and used in one module read as dead code, which is precisely the
question this tool exists to answer.

**#406(2) — the TaskCompleted hook read a key that does not exist.**
`ref_result.get("total_references", 0) == 0` against a payload that has never
carried `total_references` in either mode, so it was always true and every
inspected symbol was labelled unreferenced in the agent-facing message.

**#407 — `httpx.AsyncClient()` defaults `follow_redirects=False`.** GitHub
answers a renamed or transferred repository with 301, which does not match the
`(403, 429)` retry branch and fell through to `raise_for_status()`.

`TestStillReportsGenuinelyUnreferenced` is the load-bearing class: #406's fix
makes `is_referenced` say `true` more often, and could be "passed" by always
saying it.
"""

from __future__ import annotations

import inspect
import json
import shutil
import tempfile
from pathlib import Path

import pytest

from jcodemunch_mcp.tools.check_references import check_references
from jcodemunch_mcp.tools.index_folder import index_folder


MODULE = """\
def helper(x):
    return x + 1


def main():
    return helper(1)


def never_used(y):
    return y


def recurses(n):
    if n <= 0:
        return 0
    return recurses(n - 1)
"""


@pytest.fixture(scope="module")
def repo():
    """The issue's minimal reproduction, indexed for real."""
    root = Path(tempfile.mkdtemp(prefix="jcm-406-"))
    storage = tempfile.mkdtemp(prefix="jcm-406-store-")
    try:
        (root / "mod.py").write_text(MODULE, encoding="utf-8")
        res = index_folder(str(root), use_ai_summaries=False, storage_path=storage)
        if isinstance(res, str):
            res = json.loads(res)
        yield (res.get("repo") or res.get("repo_id")), storage
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(storage, ignore_errors=True)


def _refs(repo, identifier):
    repo_id, storage = repo
    return check_references(
        repo_id, identifier=identifier, storage_path=storage, max_content_results=20
    )


class TestSameFileCallSitesAreCounted:
    def test_a_helper_used_in_its_own_module_is_referenced(self, repo):
        """The issue's headline case, end to end through the public tool."""
        res = _refs(repo, "helper")
        assert res["is_referenced"] is True
        assert res["content_count"] >= 1

    def test_the_call_site_is_reported_and_the_definition_is_not(self, repo):
        res = _refs(repo, "helper")
        lines = [
            m["line"]
            for f in res["content_references"]
            for m in f["matches"]
        ]
        assert 6 in lines, "the call site `return helper(1)` must be reported"
        assert 1 not in lines, "the definition `def helper(x):` is not a reference"


class TestStillReportsGenuinelyUnreferenced:
    def test_an_uncalled_symbol_stays_unreferenced(self, repo):
        """The guard: the fix must not trade a false negative for a false
        positive. `never_used` appears only on its own def line."""
        res = _refs(repo, "never_used")
        assert res["is_referenced"] is False
        assert res["content_count"] == 0

    def test_a_purely_self_recursive_symbol_stays_unreferenced(self, repo):
        """A function calling only itself is not evidence anyone else uses it,
        and the recursive call sits inside the definition span."""
        res = _refs(repo, "recurses")
        assert res["is_referenced"] is False

    def test_batch_mode_agrees_with_singular_mode(self, repo):
        """`_check_batch` delegates to `_check_single`, so one fix covers both —
        asserted rather than assumed."""
        repo_id, storage = repo
        batch = check_references(
            repo_id, identifiers=["helper", "never_used"], storage_path=storage
        )
        by_name = {r["identifier"]: r for r in batch["results"]}
        assert by_name["helper"]["is_referenced"] is True
        assert by_name["never_used"]["is_referenced"] is False


class TestSpanArithmeticIsDefensive:
    """⚠ Not from the issue. The exclusion now depends on `line`/`end_line`
    arithmetic, so the degenerate shapes need pinning."""

    def _spans_for(self, symbols, ident="f"):
        """Drive the span builder the way `_check_single` does."""
        out: dict[str, list[tuple[int, int]]] = {}
        for sym in symbols:
            if sym.get("name", "").lower() != ident:
                continue
            file_path = sym.get("file", "")
            start_line = sym.get("line")
            if not file_path or not start_line:
                continue
            try:
                lo = int(start_line)
                hi = int(sym.get("end_line") or lo)
            except (TypeError, ValueError):
                continue
            out.setdefault(file_path, []).append((lo, max(lo, hi)))
        return out

    def test_an_inverted_span_still_hides_the_declaration(self):
        """`lo > hi` would match no line at all, so the signature would be
        reported as a reference to itself."""
        spans = self._spans_for([{"name": "f", "file": "a.py", "line": 10, "end_line": 3}])
        assert spans["a.py"] == [(10, 10)]

    def test_a_missing_end_line_collapses_to_one_line(self):
        spans = self._spans_for([{"name": "f", "file": "a.py", "line": 7, "end_line": None}])
        assert spans["a.py"] == [(7, 7)]

    def test_duplicate_name_in_one_file_keeps_every_span(self):
        """102 duplicate (file, name) pairs were measured on a real index; one
        span per file would leave the others counting their own signatures."""
        spans = self._spans_for([
            {"name": "f", "file": "a.py", "line": 1, "end_line": 3},
            {"name": "f", "file": "a.py", "line": 20, "end_line": 25},
        ])
        assert spans["a.py"] == [(1, 3), (20, 25)]

    def test_a_garbage_line_is_dropped_not_fatal(self):
        assert self._spans_for([{"name": "f", "file": "a.py", "line": "x"}]) == {}


class TestUnspannedSymbolsDegradeToTheOldBehaviour:
    """⚠⚠ A regression this fix would have introduced, caught by an EXISTING
    test (`test_no_import_data_searches_content`) rather than a new one.

    A symbol carrying no usable `line` gives nothing to exclude, so counting its
    file reports the DEFINITION as a reference to itself. That trades #406's
    false negative for a false positive on exactly the indexes with the least
    metadata. Where the evidence is missing, the file is skipped whole — the
    pre-v1.108.226 behaviour — instead of guessed at.
    """

    @pytest.fixture
    def unspanned(self, tmp_path):
        from jcodemunch_mcp.parser.symbols import Symbol
        from jcodemunch_mcp.storage import IndexStore

        store = IndexStore(base_path=str(tmp_path / "idx"))
        store.save_index(
            owner="test", name="nospan",
            source_files=["src/utils.py", "src/app.py"],
            symbols=[Symbol(
                id="src/utils.py::helper#function", file="src/utils.py",
                name="helper", qualified_name="helper", kind="function",
                language="python", signature="def helper(): pass",
            )],
            raw_files={
                "src/utils.py": "def helper(): pass",
                "src/app.py": "result = helper()",
            },
            languages={"python": 2},
            file_languages={"src/utils.py": "python", "src/app.py": "python"},
            file_summaries={"src/utils.py": "", "src/app.py": ""},
        )
        return str(tmp_path / "idx")

    def test_a_symbol_with_no_line_does_not_count_its_own_definition(self, unspanned):
        res = check_references(
            repo="test/nospan", identifier="helper", storage_path=unspanned
        )
        files = [f["file"] for f in res["content_references"]]
        assert files == ["src/app.py"], "the definition's own file must stay excluded"
        assert res["content_count"] == 1


class TestTaskCompleteHookReadsAKeyThatExists:
    def test_check_references_does_not_return_total_references(self, repo):
        """The read site's premise, asserted against the real payload."""
        assert "total_references" not in _refs(repo, "helper")

    def test_the_hook_no_longer_reads_it(self):
        from jcodemunch_mcp.cli import hooks

        src = inspect.getsource(hooks.run_taskcomplete)
        assert 'get("total_references"' not in src, (
            "run_taskcomplete must not read a key check_references never returns"
        )
        assert 'get("is_referenced")' in src

    def test_nothing_in_the_package_reads_total_references(self):
        """It appeared exactly once in the tree, at the read site. Keep it that
        way: a returning reader is the same defect again.

        Matches a READ (`.get("total_references"` / `["total_references"]`), not
        the bare string, so the comment recording why this exists does not trip
        its own guard.
        """
        import re

        root = Path(__file__).resolve().parents[1] / "src"
        read = re.compile(r"""(\.get\(|\[)\s*["']total_references["']""")
        offenders = [
            str(p.relative_to(root))
            for p in root.rglob("*.py")
            if read.search(p.read_text(encoding="utf-8", errors="replace"))
        ]
        assert offenders == [], offenders


class TestSafetyPreflightsSeeSameFileCallers:
    """⚠⚠ Not in either issue — found by asking who CONSUMES check_references.

    `check_delete_safe` and `check_edit_safe` both skipped
    `ref_file == target_file`, which is #406's defect one layer up: it discards
    the FILE when only the DEFINITION must be excluded. The skip was unreachable
    before v1.108.226 because `check_references` never returned a reference in
    the defining file, so nothing showed it was wrong.

    Measured on the module below before the fix: `helper`, called by `main()`
    one line down, came back **`safe_to_delete` with zero blockers**.
    """

    def test_delete_preflight_blocks_on_a_same_file_caller(self, repo):
        from jcodemunch_mcp.tools.check_delete_safe import check_delete_safe

        repo_id, storage = repo
        out = check_delete_safe(
            repo=repo_id, symbol="mod.py::helper#function", storage_path=storage
        )
        if isinstance(out, str):
            out = json.loads(out)
        assert out["verdict"] != "safe_to_delete"
        assert any(b["kind"] == "internal_reference" for b in out["blockers"])

    def test_delete_preflight_still_clears_a_genuinely_unused_symbol(self, repo):
        """The guard: correcting toward safety must not block everything."""
        from jcodemunch_mcp.tools.check_delete_safe import check_delete_safe

        repo_id, storage = repo
        out = check_delete_safe(
            repo=repo_id, symbol="mod.py::never_used#function", storage_path=storage
        )
        if isinstance(out, str):
            out = json.loads(out)
        assert out["verdict"] == "safe_to_delete"
        assert out["blockers"] == []

    def test_neither_preflight_still_skips_the_defining_file(self):
        """Guards the CODE, not the comments — the comments explaining why this
        exists necessarily quote the expression they warn about."""
        import io
        import tokenize

        from jcodemunch_mcp.tools import check_delete_safe, check_edit_safe

        def code_only(module) -> str:
            src = inspect.getsource(module)
            kept = [
                tok.string
                for tok in tokenize.generate_tokens(io.StringIO(src).readline)
                if tok.type not in (tokenize.COMMENT, tokenize.STRING)
            ]
            return " ".join(kept)

        for mod in (check_delete_safe, check_edit_safe):
            assert "ref_file == target_file" not in code_only(mod), (
                f"{mod.__name__} must not discard same-file references again"
            )


class TestIndexRepoFollowsRedirects:
    """#407. GitHub 301s a renamed or transferred repository; httpx defaults
    `follow_redirects=False`, and a 301 does not match the (403, 429) retry
    branch, so it reached `raise_for_status()`."""

    def test_every_async_client_in_index_repo_follows_redirects(self):
        from jcodemunch_mcp.tools import index_repo

        src = inspect.getsource(index_repo)
        assert "httpx.AsyncClient()" not in src, (
            "a bare httpx.AsyncClient() defaults follow_redirects=False, which "
            "makes every renamed GitHub repo unindexable"
        )
        assert src.count("httpx.AsyncClient(follow_redirects=True)") == 2

    @pytest.mark.asyncio
    async def test_fetch_repo_tree_follows_a_301(self, monkeypatch):
        """Drive the real function against a redirecting transport."""
        import httpx

        from jcodemunch_mcp.tools import index_repo

        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            if "/repos/old/name/" in str(request.url):
                return httpx.Response(
                    301,
                    headers={
                        "location": "https://api.github.com/repositories/999/git/trees/HEAD?recursive=1"
                    },
                )
            return httpx.Response(200, json={"sha": "deadbeef", "tree": [], "truncated": False})

        real_client = httpx.AsyncClient

        def fake_client(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_client(*args, **kwargs)

        monkeypatch.setattr(index_repo.httpx, "AsyncClient", fake_client)
        entries, sha = await index_repo.fetch_repo_tree("old", "name", None)

        assert sha == "deadbeef"
        assert entries == []
        assert len(seen) == 2, "the redirect must actually be followed"
        assert "/repositories/999/" in seen[1]
