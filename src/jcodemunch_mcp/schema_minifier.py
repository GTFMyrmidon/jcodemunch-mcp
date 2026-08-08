"""Schema minification and TypeScript signature generation utilities for MCP tools."""

from __future__ import annotations

import re
from typing import Any


_TSDOC_TAG_RE = re.compile(
    r"^\s*@(example|param|returns?|deprecated|see|since|note|author|version)\b",
    re.IGNORECASE,
)
_VERSION_TAG_RE = re.compile(r"\(v\d+\.\d+(\.\d+)?\)")
_WHITESPACE_RE = re.compile(r"\s+")


def minify_description(desc: str) -> str:
    """Strip TSDoc/JSDoc tags, version history notes, and collapse whitespace."""
    if not desc or not isinstance(desc, str):
        return ""
    
    lines = []
    for line in desc.splitlines():
        if _TSDOC_TAG_RE.match(line):
            continue
        lines.append(line)
    
    text = " ".join(lines)
    text = _VERSION_TAG_RE.sub("", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


def minify_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Strip non-essential JSON Schema metadata fields ($schema, title, examples, etc.)."""
    if not isinstance(schema, dict):
        return {}
    
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key in ("$schema", "$id", "title", "comment", "examples"):
            continue
        if key == "description" and isinstance(value, str):
            min_desc = minify_description(value)
            if min_desc:
                out[key] = min_desc
            continue
        if key == "properties" and isinstance(value, dict):
            out[key] = {
                prop_name: minify_json_schema(prop_def)
                for prop_name, prop_def in value.items()
                if isinstance(prop_def, dict)
            }
            continue
        if key == "items" and isinstance(value, dict):
            out[key] = minify_json_schema(value)
            continue
        out[key] = value
    return out


def _ts_type_from_json_schema(prop_def: dict[str, Any]) -> str:
    """Infer compact TypeScript type string from a JSON Schema property definition."""
    if not isinstance(prop_def, dict):
        return "any"
    
    # Check enum
    enum_val = prop_def.get("enum")
    if isinstance(enum_val, list) and enum_val:
        formatted_enum = []
        for item in enum_val:
            if isinstance(item, str):
                formatted_enum.append(f'"{item}"')
            elif item is None:
                formatted_enum.append("null")
            elif isinstance(item, bool):
                formatted_enum.append("true" if item else "false")
            else:
                formatted_enum.append(str(item))
        return " | ".join(formatted_enum)
    
    prop_type = prop_def.get("type")
    if isinstance(prop_type, list):
        types = [_ts_type_from_json_schema({"type": t}) for t in prop_type]
        return " | ".join(types)
    
    if prop_type == "string":
        return "string"
    if prop_type in ("integer", "number"):
        return "number"
    if prop_type == "boolean":
        return "boolean"
    if prop_type == "array":
        items = prop_def.get("items")
        if isinstance(items, dict):
            item_type = _ts_type_from_json_schema(items)
            if " | " in item_type:
                return f"({item_type})[]"
            return f"{item_type}[]"
        return "any[]"
    if prop_type == "object":
        return "Record<string, any>"
    
    return "any"


def json_schema_to_typescript_signature(
    name: str,
    schema: dict[str, Any],
    return_type: str = "any",
) -> str:
    """Convert JSON Schema into a compact TypeScript function signature."""
    if not isinstance(schema, dict):
        return f"{name}(): {return_type}"
    
    props = schema.get("properties")
    if not isinstance(props, dict) or not props:
        return f"{name}(): {return_type}"
    
    req_list = schema.get("required")
    req_set = set(req_list) if isinstance(req_list, list) else set()
    
    params = []
    for prop_name, prop_def in props.items():
        if not isinstance(prop_def, dict):
            continue
        is_req = prop_name in req_set
        param_type = _ts_type_from_json_schema(prop_def)
        opt_suffix = "" if is_req else "?"
        params.append(f"{prop_name}{opt_suffix}: {param_type}")
    
    return f"{name}({', '.join(params)}): {return_type}"
