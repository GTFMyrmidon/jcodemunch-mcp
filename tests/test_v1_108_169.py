"""v1.108.169 — the retrieval verdict survives compaction, and search_text can
detect a rebuild underneath its scan.

Two defects, both in contracts shipped days earlier:

1. Every compact encoder dropped ``_meta.verdict``. ``schema_driven.encode``
   filters ``_meta`` through a strict allowlist and ``verdict`` was in none of
   the 15 schemas, so under MUNCH encoding the agent got a confident-looking
   zero-result answer with no ``state``, no ``channels``, and no
   ``evidence_ref`` — the absence evidence minted in ``call_tool`` was
   discarded on the way out.

2. ``search_text`` never got ``index_changed=``. v1.108.168 wired the 5th
   absence-refusal rule (a rebuild underneath a scan cannot prove absence) into
   ``search_symbols`` and ``get_ranked_context`` only. The absence chokepoint is
   generic — it fires on any ``_meta.verdict`` dict — so ``search_text`` could
   mint ``absent:<sha>`` refs with the rebuilding rule structurally unable to
   fire.
"""

import importlib
import json
import pkgutil

import pytest

from jcodemunch_mcp.encoding import schema_driven as sd
from jcodemunch_mcp.encoding import schemas as schemas_pkg


def _schema_modules():
    """Every encoder schema that declares a _META allowlist."""
    out = []
    for mod in pkgutil.iter_modules(schemas_pkg.__path__):
        m = importlib.import_module(f"{schemas_pkg.__name__}.{mod.name}")
        if hasattr(m, "_META"):
            out.append(m)
    return out


VERDICT = {
    "state": "degraded",
    "scanned": {"files": 12},
    "channels": {"lexical": "ok", "index": "rebuilding"},
    "coverage": {"walk": "full", "files_indexed": 12},
    "scorer": 1,
    "evidence_ref": "absent:0123456789ab",
    "absence_citable": False,
    "absence_blocked_by": "the index was being rewritten during the scan",
}


# --- 1. the encoder mechanism ------------------------------------------------


def test_meta_json_blobs_round_trips_a_nested_dict():
    """A structured _meta value survives encode -> decode intact."""
    tables = [sd.TableSpec(tag="r", key="results", cols=["file"])]
    payload, _ = sd.encode(
        "t",
        {"results": [{"file": "a.py"}], "_meta": {"verdict": VERDICT}},
        "t1",
        tables,
        (),
        meta_json_blobs=("verdict",),
    )
    out = sd.decode(payload, tables, (), meta_json_blobs=("verdict",))
    assert out["_meta"]["verdict"] == VERDICT
    # Not stringified — the scalar path would have produced a str.
    assert isinstance(out["_meta"]["verdict"], dict)
    assert out["_meta"]["verdict"]["channels"]["index"] == "rebuilding"


def test_meta_json_blobs_omitted_when_absent():
    """No verdict in _meta means no key invented on the way back."""
    tables = [sd.TableSpec(tag="r", key="results", cols=["file"])]
    payload, _ = sd.encode(
        "t",
        {"results": [{"file": "a.py"}], "_meta": {"timing_ms": 1.0}},
        "t1",
        tables,
        (),
        meta_keys=("timing_ms",),
        meta_json_blobs=("verdict",),
    )
    out = sd.decode(
        payload, tables, (), meta_keys=("timing_ms",), meta_json_blobs=("verdict",)
    )
    assert "verdict" not in out.get("_meta", {})


def test_meta_json_blob_survives_delimiter_heavy_content():
    """JSON containing the scalar-format delimiters must not corrupt the block."""
    nasty = {"state": "absent", "note": "a=b|c\nd", "q": 'has "quotes" and, commas'}
    tables = [sd.TableSpec(tag="r", key="results", cols=["file"])]
    payload, _ = sd.encode(
        "t",
        {"results": [{"file": "a.py"}], "_meta": {"verdict": nasty}},
        "t1",
        tables,
        (),
        meta_json_blobs=("verdict",),
    )
    out = sd.decode(payload, tables, (), meta_json_blobs=("verdict",))
    assert out["_meta"]["verdict"] == nasty


# --- 2. every schema carries it ---------------------------------------------


def test_every_schema_declares_verdict_as_a_meta_json_blob():
    """The allowlist is strict, so a schema that forgets verdict silently drops it."""
    mods = _schema_modules()
    assert len(mods) >= 15, f"expected the full schema set, found {len(mods)}"
    missing = [m.__name__ for m in mods if "verdict" not in getattr(m, "_META_JSON", ())]
    assert not missing, f"schemas that would drop _meta.verdict: {missing}"


@pytest.mark.parametrize(
    "name", ["search_symbols", "search_text", "get_ranked_context"]
)
def test_verdict_survives_the_real_encoders(name):
    """The three tools the absence contract is built on must carry their verdict."""
    m = importlib.import_module(f"{schemas_pkg.__name__}.{name}")
    response = {"result_count": 0, "results": [], "_meta": {"verdict": dict(VERDICT)}}
    payload, _ = m.encode(name, response)
    assert "rebuilding" in payload, "verdict never reached the wire"
    out = m.decode(payload)
    got = out["_meta"]["verdict"]
    assert got["state"] == "degraded"
    assert got["channels"]["index"] == "rebuilding"
    assert got["evidence_ref"] == "absent:0123456789ab"
    assert got["absence_blocked_by"]


def test_encoded_payload_carries_the_blob_under_a_json_meta_key():
    """Guard the wire form: a __json._meta.<key> scalar, CSV-quoted inline.

    Scalars share one space-separated line and quotes are doubled, so this
    asserts on the key's presence plus a faithful decode rather than trying to
    re-parse the raw JSON out of the line.
    """
    m = importlib.import_module(f"{schemas_pkg.__name__}.search_text")
    payload, _ = m.encode(
        "search_text", {"result_count": 0, "results": [], "_meta": {"verdict": VERDICT}}
    )
    assert "__json._meta.verdict=" in payload
    # It rides as JSON, not a Python repr — no single-quoted keys.
    assert "'state'" not in payload
    assert m.decode(payload)["_meta"]["verdict"] == VERDICT
    # And it is genuinely JSON once unquoted.
    assert json.loads(json.dumps(m.decode(payload)["_meta"]["verdict"])) == VERDICT


# --- 3. search_text detects a rebuild ---------------------------------------


def test_search_text_passes_index_changed_to_build_verdict(monkeypatch):
    """A rebuild underneath a search_text scan must reach build_verdict."""
    from jcodemunch_mcp.retrieval import verdict as vmod

    seen = {}
    real = vmod.build_verdict

    def spy(**kwargs):
        seen.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(vmod, "build_verdict", spy)
    monkeypatch.setattr(vmod, "index_changed_since_load", lambda _i: True)

    import inspect

    from jcodemunch_mcp.tools import search_text as st

    src = inspect.getsource(st)
    assert "index_changed=_index_changed_since_load(index)" in src, (
        "search_text must pass index_changed, or the 5th absence-refusal rule "
        "cannot fire for it while the generic chokepoint still mints refs"
    )


def test_zero_results_under_rebuild_is_degraded_not_absent():
    """The rule itself: a rewrite underneath the scan cannot prove absence."""
    from jcodemunch_mcp.retrieval.verdict import build_verdict

    absent = build_verdict(result_count=0, scanned_files=5, index_changed=False)
    assert absent["verdict"]["state"] == "absent"

    degraded = build_verdict(result_count=0, scanned_files=5, index_changed=True)
    assert degraded["verdict"]["state"] == "degraded"
    assert degraded["verdict"]["channels"]["index"] == "rebuilding"
