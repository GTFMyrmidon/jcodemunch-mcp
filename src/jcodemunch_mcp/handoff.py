"""Server-owned canonical handoff contract (``jcodemunch.handoff/v1``, #374).

A multi-step repository audit ends with one authoritative Markdown result.
The assistant authors the analysis (the server never writes conclusions);
this module owns everything downstream of authorship: deterministic assembly,
evidence attestation, persistence, identity, hashing, and immutable serving
via the ``munch://handoff/<id>`` resource.

The differentiator no client Stop hook can provide: ``evidence_refs`` are
validated against the session's actual retrieval record (the yield tracker's
served-symbol ids) at finalization time, so a finalized handoff attests that
every evidence reference it cites corresponds to something actually served
by this server in this session. Unknown refs fail closed.

``handoff/v2`` (#377, claim-scoped evidence) is the same contract one level
deeper: a section may carry caller-authored ``claims``, each with its OWN
``evidence_refs``, so the body shows which retrieval backs which sentence
instead of one global list at the end. v1 proved "this was retrieved in this
session"; v2 proves that per claim. The schema string is chosen by the INPUT —
no claims anywhere means a v1 body, byte-identical to what v1 produced.

Charter constraints (accepted scope, issue #374):
- Deterministic: same repo/task/profile/sections/evidence/appendices ->
  byte-identical body, same id, same sha256.
- Each appendix exactly once; duplicate names rejected.
- No arbitrary character limit.
- Session-scoped (process == session, the _steer_state precedent), local-first,
  in-memory only — never writes to the user's repository or index store.
- ``canonical: true`` in the receipt is advisory metadata for direct-render
  clients; it forces nothing.
"""

from __future__ import annotations

import hashlib
import threading
from typing import Iterable, Optional

HANDOFF_SCHEMA = "jcodemunch.handoff/v1"
HANDOFF_SCHEMA_V2 = "jcodemunch.handoff/v2"
HANDOFF_URI_PREFIX = "munch://handoff/"
HANDOFF_CONTENT_TYPE = "text/markdown"

# Session-scoped store: handoff_id -> {"body": str, "receipt": dict}.
# Process lifetime == MCP session lifetime (same precedent as server._steer_state);
# process exit is the cleanup.
_lock = threading.Lock()
_handoffs: dict[str, dict] = {}


def _norm_file(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def _validate_evidence(refs, served_ids: Iterable[str]):
    """Attest each ref against the session retrieval record.

    A ref is attested when it is exactly a served symbol id, or the file
    component (before ``::``) of a served id — a file-level citation of
    retrieved code. Returns (ordered_unique_refs, unknown_refs).
    """
    served = set(served_ids or ())
    served_files = {_norm_file(sid.split("::", 1)[0]) for sid in served}
    seen: set[str] = set()
    ordered: list[str] = []
    unknown: list[str] = []
    for ref in refs:
        if not isinstance(ref, str) or not ref.strip():
            unknown.append(repr(ref))
            continue
        ref = ref.strip()
        if ref in seen:
            continue
        seen.add(ref)
        ordered.append(ref)
        if ref not in served and _norm_file(ref) not in served_files:
            unknown.append(ref)
    return ordered, unknown


def _validate_claims(raw, si, seen_ids):
    """Validate one section's caller-authored claims (#377 phase 1).

    Claim ids are unique across the WHOLE handoff, not per section: they are
    the machine-readable anchor a caller cites, and two sections owning the
    same id would make that citation ambiguous. Returns (claims, error).
    """
    if raw is None:
        return [], None
    if not isinstance(raw, list) or not raw:
        return None, f"sections[{si}].claims must be a non-empty list when present"
    out = []
    for j, claim in enumerate(raw):
        where = f"sections[{si}].claims[{j}]"
        if not isinstance(claim, dict):
            return None, f"{where} must be an object with 'id', 'statement' and 'evidence_refs'"
        cid = claim.get("id")
        statement = claim.get("statement")
        if not isinstance(cid, str) or not cid.strip():
            return None, f"{where}.id must be a non-empty string"
        if not isinstance(statement, str) or not statement.strip():
            return None, f"{where}.statement must be a non-empty string"
        cid = cid.strip()
        if cid in seen_ids:
            return None, f"duplicate claim id: {cid!r} (claim ids must be unique across the handoff)"
        seen_ids.add(cid)
        refs = claim.get("evidence_refs")
        if not isinstance(refs, list) or not refs:
            return None, (
                f"{where}.evidence_refs must be a non-empty list of session "
                "retrieval references (a claim with no evidence is not attestable)"
            )
        classification = claim.get("classification")
        if classification is not None and (
            not isinstance(classification, str) or not classification.strip()
        ):
            return None, f"{where}.classification must be a non-empty string when present"
        out.append(
            {
                "id": cid,
                # Caller-authored text is preserved verbatim; the server never
                # rewrites a statement or a classification.
                "statement": statement.strip(),
                "classification": classification.strip() if classification else None,
                "raw_refs": refs,
            }
        )
    return out, None


def _validate_sections(sections):
    if not isinstance(sections, list) or not sections:
        return None, "sections must be a non-empty list of {heading, content} objects"
    out = []
    seen_ids: set[str] = set()
    for i, sec in enumerate(sections):
        if not isinstance(sec, dict):
            return None, f"sections[{i}] must be an object with 'heading' and 'content'"
        heading = sec.get("heading")
        content = sec.get("content")
        if not isinstance(heading, str) or not heading.strip():
            return None, f"sections[{i}].heading must be a non-empty string"
        claims, err = _validate_claims(sec.get("claims"), i, seen_ids)
        if err:
            return None, err
        # Content stays required in the v1 shape; a claims-carrying section may
        # omit it, since the claims themselves are then the section's body.
        if content is None and claims:
            content = ""
        if not isinstance(content, str) or (not content.strip() and not claims):
            return None, f"sections[{i}].content must be a non-empty string"
        out.append((heading.strip(), content.rstrip(), claims))
    return out, None


def _validate_appendices(appendices):
    if appendices is None:
        return [], None
    if not isinstance(appendices, list):
        return None, "appendices must be a list of {name, content} objects"
    out = []
    names: set[str] = set()
    for i, app in enumerate(appendices):
        if not isinstance(app, dict):
            return None, f"appendices[{i}] must be an object with 'name' and 'content'"
        name = app.get("name")
        content = app.get("content")
        if not isinstance(name, str) or not name.strip():
            return None, f"appendices[{i}].name must be a non-empty string"
        if not isinstance(content, str) or not content.strip():
            return None, f"appendices[{i}].content must be a non-empty string"
        name = name.strip()
        if name in names:
            return None, f"duplicate appendix name: {name!r} (each appendix appears exactly once)"
        names.add(name)
        ctype = app.get("content_type") or "text/markdown"
        out.append((name, str(ctype), content.rstrip()))
    return out, None


def render_handoff(
    repo: str,
    task: str,
    profile: str,
    sections,
    evidence_refs,
    appendices,
    schema: str = HANDOFF_SCHEMA,
) -> str:
    """Deterministic canonical Markdown. No timestamps, no randomness."""
    lines = [
        f"# Handoff: {task}",
        "",
        f"- Schema: {schema}",
        f"- Repo: {repo}",
        f"- Profile: {profile}",
        "",
    ]
    for heading, content, claims in sections:
        lines += [f"## {heading}", ""]
        if content:
            lines += [content, ""]
        for claim in claims:
            # Evidence renders BESIDE the claim it supports — the whole point of
            # v2. The global index below stays for v1 compatibility.
            lines += [f"### {claim['statement']}", ""]
            lines.append(f"- Claim id: `{claim['id']}`")
            if claim["classification"]:
                lines.append(f"- Classification: {claim['classification']}")
            lines.append("- Evidence:")
            lines += [f"  - `{ref}`" for ref in claim["refs"]]
            lines.append("")
    lines += [
        "## Evidence",
        "",
        "Every reference below was validated against this session's retrieval",
        "record at finalization time (server-attested).",
        "",
    ]
    lines += [f"- `{ref}`" for ref in evidence_refs]
    lines.append("")
    for name, ctype, content in appendices:
        lines += [f"## Appendix: {name}", "", f"_Content type: {ctype}_", "", content, ""]
    return "\n".join(lines).rstrip() + "\n"


def finalize_handoff(
    *,
    repo,
    task,
    sections,
    evidence_refs,
    profile: str = "general",
    appendices=None,
    served_ids: Optional[Iterable[str]] = None,
) -> dict:
    """Assemble, attest, persist, and return the compact receipt.

    Any validation failure returns ``{"error": ...}`` — the server dispatcher
    maps in-band error dicts to ``CallToolResult(isError=True)`` (v1.108.74
    contract). The server never authors content: sections/appendices arrive
    verbatim from the caller; only assembly and attestation happen here.
    """
    if not isinstance(repo, str) or not repo.strip():
        return {"error": "repo must be a non-empty string"}
    if not isinstance(task, str) or not task.strip():
        return {"error": "task must be a non-empty string"}
    if not isinstance(profile, str) or not profile.strip():
        return {"error": "profile must be a non-empty string"}
    sec, err = _validate_sections(sections)
    if err:
        return {"error": err}
    apps, err = _validate_appendices(appendices)
    if err:
        return {"error": err}
    served = list(served_ids or ())
    claim_count = sum(len(claims) for _, _, claims in sec)

    # Attest each claim's refs on their own, so an unknown ref names the claim
    # that cited it instead of vanishing into one global failure list (#377).
    invalid_claims = []
    for _, _, claims in sec:
        for claim in claims:
            claim_refs, claim_unknown = _validate_evidence(claim["raw_refs"], served)
            claim["refs"] = claim_refs
            if claim_unknown:
                invalid_claims.append({"claim_id": claim["id"], "unknown_refs": claim_unknown})
    if invalid_claims:
        return {
            "error": (
                "claim evidence attestation failed: the following claims cite "
                "refs that do not correspond to anything retrieved in this session"
            ),
            "invalid_claims": invalid_claims,
            "hint": (
                "Every claim-scoped ref must be a symbol id (or its file path) "
                "this session actually served. Retrieve the evidence that "
                "supports the claim, then finalize."
            ),
        }

    # `evidence_refs` stays required in the v1 shape, but claims can satisfy it:
    # a caller who scoped everything to claims should not have to restate it.
    claim_refs_flat = [ref for _, _, claims in sec for claim in claims for ref in claim["refs"]]
    if not isinstance(evidence_refs, list) or (not evidence_refs and not claim_refs_flat):
        return {
            "error": (
                "evidence_refs must be a non-empty list of session retrieval "
                "references (symbol ids or file paths served this session by "
                "search_symbols / get_ranked_context), or every claim must "
                "carry its own evidence_refs"
            )
        }
    # The canonical index is the union, caller order first: every claim ref is
    # discoverable from the global list, which keeps v1 consumers whole.
    refs, unknown = _validate_evidence(list(evidence_refs) + claim_refs_flat, served)
    if unknown:
        return {
            "error": (
                "evidence attestation failed: the following refs do not "
                "correspond to anything retrieved in this session"
            ),
            "unknown_refs": unknown,
            "hint": (
                "Evidence refs must be symbol ids (or their file paths) that "
                "this session actually served via search_symbols or "
                "get_ranked_context. Retrieve the evidence first, then finalize."
            ),
        }

    # The input picks the contract: no claims anywhere is still a v1 handoff,
    # and its body is byte-identical to what v1 rendered.
    schema = HANDOFF_SCHEMA_V2 if claim_count else HANDOFF_SCHEMA
    body = render_handoff(repo.strip(), task.strip(), profile.strip(), sec, refs, apps, schema)
    raw = body.encode("utf-8")
    sha256 = hashlib.sha256(raw).hexdigest()
    handoff_id = sha256[:16]
    receipt = {
        "schema": schema,
        "handoff_id": handoff_id,
        "repo": repo.strip(),
        "profile": profile.strip(),
        "content_type": HANDOFF_CONTENT_TYPE,
        "resource_uri": f"{HANDOFF_URI_PREFIX}{handoff_id}",
        "sha256": sha256,
        "length": len(raw),
        "canonical": True,
        "evidence_attested": True,
        "evidence_count": len(refs),
        "appendices": [name for name, _, _ in apps],
    }
    if claim_count:
        # Omitted entirely on a v1 handoff, so v1 receipts stay unchanged.
        receipt["claims_attested"] = claim_count
    with _lock:
        _handoffs[handoff_id] = {"body": body, "receipt": receipt}
    return dict(receipt)


def get_handoff(handoff_id: str) -> Optional[dict]:
    """Return {"body", "receipt"} for a stored handoff, or None."""
    with _lock:
        rec = _handoffs.get(handoff_id)
        return {"body": rec["body"], "receipt": dict(rec["receipt"])} if rec else None


def handoff_for_uri(uri: str) -> Optional[dict]:
    """Resolve a munch://handoff/<id> URI to its stored record, or None."""
    s = str(uri)
    if not s.startswith(HANDOFF_URI_PREFIX):
        return None
    return get_handoff(s[len(HANDOFF_URI_PREFIX):])


def list_handoff_resources() -> list[dict]:
    """Rows for list_resources(): one per finalized handoff this session."""
    with _lock:
        return [
            {
                "uri": rec["receipt"]["resource_uri"],
                "name": f"handoff-{hid}",
                "description": (
                    f"Canonical handoff for {rec['receipt']['repo']} "
                    f"({rec['receipt']['profile']}); immutable, "
                    f"sha256 {rec['receipt']['sha256'][:12]}…"
                ),
            }
            for hid, rec in _handoffs.items()
        ]


def clear_handoffs() -> None:
    """Test hook: drop all session handoffs."""
    with _lock:
        _handoffs.clear()
