"""v1.108.208 — `content_hash` stops riding every `get_symbol_source` response.

The field is a 64-char SHA-256 (`parser/symbols.py:80`) that was emitted on every
served row unconditionally, not gated on `verify`. Roughly 83 characters per
symbol on the hottest read path, for a value the caller was never offered a use
for: `verify` answers the drift question as a boolean, `get_changed_symbols`
answers it across a diff, and a receipt carries the digest out of band.

The interesting half is NOT that the field disappears. It is that
`evidence/producers.py::_row_subject` reads the digest off the SERVED ROW and
deliberately never re-reads the index, so gating at the emission site in
`tools/get_symbol.py` would have silently downgraded every receipt's
`hash_source` from `index_content_hash` to `served_bytes` — a quieter, worse
version of the defect this saving is worth. The strip therefore lives in the
dispatcher AFTER the mint block, and `TestTheReceiptIsUnaffected` is the test
that would have caught the tool-side version.

Helper names are prefixed `_r208_` for the reason test_v1_108_183.py documents:
an unprefixed helper here outranks real symbols in golden replay queries.
"""

from __future__ import annotations

import asyncio
import json
import os
import re

import pytest

from jcodemunch_mcp.evidence import receipts as _r208_receipts

_R208_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _r208_payload(res):
    if hasattr(res, "content"):
        return json.loads(res.content[0].text)
    return json.loads(res[0].text)


def _r208_call(server, tool, args):
    return _r208_payload(asyncio.run(server.call_tool(tool, args)))


@pytest.fixture()
def _r208_env(tmp_path, monkeypatch):
    """A two-symbol indexed repo plus the live server, on a private index store."""
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / "idx"))
    proj = tmp_path / "proj"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "mod.py").write_text(
        "def alpha(x):\n"
        "    return x + 1\n"
        "\n"
        "\n"
        "def beta(y):\n"
        "    return y * 2\n",
        encoding="utf-8",
    )
    from jcodemunch_mcp import server

    _r208_receipts.clear_receipts()
    indexed = _r208_call(server, "index_folder", {"path": str(proj)})
    yield server, indexed["repo"]
    _r208_receipts.clear_receipts()


_R208_ALPHA = "src/mod.py::alpha#function"
_R208_BETA = "src/mod.py::beta#function"


class TestTheDefaultResponseDropsIt:
    def test_single_symbol_carries_no_content_hash(self, _r208_env):
        server, repo = _r208_env
        body = _r208_call(
            server,
            "get_symbol_source",
            {"repo": repo, "symbol_id": _R208_ALPHA, "format": "json"},
        )
        # Non-vacuity: the row really was served, we did not assert on an error.
        assert body["id"] == _R208_ALPHA
        assert body["source"], "no body served, so the assertion below proves nothing"
        assert "content_hash" not in body

    def test_batch_shape_carries_no_content_hash_on_any_row(self, _r208_env):
        server, repo = _r208_env
        body = _r208_call(
            server,
            "get_symbol_source",
            {"repo": repo, "symbol_ids": [_R208_ALPHA, _R208_BETA], "format": "json"},
        )
        assert len(body["symbols"]) == 2, "batch shape did not serve both rows"
        for sym in body["symbols"]:
            assert "content_hash" not in sym, f"{sym['id']} still carries the digest"


class TestTheConsumersStillGetIt:
    """`verify` and `receipt` are the two requests that consume the digest."""

    def test_verify_keeps_the_digest_and_it_is_a_full_sha256(self, _r208_env):
        server, repo = _r208_env
        body = _r208_call(
            server,
            "get_symbol_source",
            {"repo": repo, "symbol_id": _R208_ALPHA, "verify": True, "format": "json"},
        )
        assert body["content_verified"] is True
        assert _R208_HEX64.match(body["content_hash"]), body.get("content_hash")

    def test_verify_on_the_batch_shape_keeps_it_on_every_row(self, _r208_env):
        server, repo = _r208_env
        body = _r208_call(
            server,
            "get_symbol_source",
            {
                "repo": repo,
                "symbol_ids": [_R208_ALPHA, _R208_BETA],
                "verify": True,
                "format": "json",
            },
        )
        for sym in body["symbols"]:
            assert _R208_HEX64.match(sym["content_hash"]), sym["id"]


class TestTheReceiptIsUnaffected:
    """The regression a tool-side gate would have caused, made a test.

    `_row_subject` reads the digest off the served row and never re-reads the
    index. If the strip ran before minting, or at the emission site, the receipt
    would still be produced and would still validate — it would just quietly
    attest the bytes it was handed instead of the digest the index stored. Every
    field would be right except the provenance of the one that matters.
    """

    def test_hash_source_is_still_the_index_digest(self, _r208_env):
        server, repo = _r208_env
        body = _r208_call(
            server,
            "get_symbol_source",
            {"repo": repo, "symbol_id": _R208_ALPHA, "receipt": True, "format": "json"},
        )
        ids = ((body.get("_meta") or {}).get("receipts") or {}).get("ids") or []
        assert ids, "the registered producer minted no receipt"

        envelope, reason = _r208_receipts.lookup(ids[0])
        assert envelope is not None, f"receipt not retrievable: {reason}"
        integrity = envelope["integrity"]
        assert integrity["hash_source"] == "index_content_hash", (
            "the receipt fell back to hashing the served bytes, which is what a "
            "tool-side gate on content_hash would have caused"
        )
        assert integrity["content_hash_matches_served_bytes"] is True

    def test_a_receipt_request_also_leaves_the_field_on_the_response(self, _r208_env):
        """Requesting a receipt is a request for the digest, so it stays visible."""
        server, repo = _r208_env
        body = _r208_call(
            server,
            "get_symbol_source",
            {"repo": repo, "symbol_id": _R208_ALPHA, "receipt": True, "format": "json"},
        )
        assert _R208_HEX64.match(body["content_hash"]), body.get("content_hash")


class TestTheStripIsScoped:
    def test_the_tool_still_populates_it_internally(self, _r208_env):
        """Guards the emission site itself.

        If someone 'finishes the job' by dropping the key from
        `tools/get_symbol.py`, the default response looks identical and only the
        receipt's provenance changes. Assert the unstripped tool output directly.
        """
        server, repo = _r208_env
        from jcodemunch_mcp.tools import get_symbol as _gs

        del server
        # storage_path is resolved by the dispatcher, not defaulted in the tool,
        # so a direct call has to hand it over or the index is "not loadable".
        out = _gs.get_symbol_source(
            repo=repo,
            symbol_id=_R208_ALPHA,
            storage_path=os.environ["CODE_INDEX_PATH"],
        )
        assert "error" not in out, out.get("error")
        assert _R208_HEX64.match(out["content_hash"]), (
            "the tool no longer populates content_hash; the dispatcher strip is "
            "not the place to fix that"
        )
