"""Compact encoder for search_symbols."""

from .. import schema_driven as sd

TOOLS = ("search_symbols",)
ENCODING_ID = "ss1"

_TABLES = [
    sd.TableSpec(
        key="results",
        tag="s",
        cols=["id", "name", "kind", "file", "line", "score", "signature", "summary"],
        intern=["file", "id"],
        types={"line": "int", "score": "float"},
    ),
]
_SCALARS = ("result_count", "query", "repo")
_META = (
    "timing_ms", "truncated", "total_symbols", "tokens_saved",
    "total_tokens_saved", "fusion", "channels",
)
_META_JSON = ("verdict",)  # structured _meta that must survive compaction


def encode(tool: str, response: dict) -> tuple[str, str]:
    return sd.encode(tool, response, ENCODING_ID, _TABLES, _SCALARS, meta_keys=_META, meta_json_blobs=_META_JSON)


def decode(payload: str) -> dict:
    return sd.decode(payload, _TABLES, _SCALARS, meta_keys=_META, meta_json_blobs=_META_JSON)
