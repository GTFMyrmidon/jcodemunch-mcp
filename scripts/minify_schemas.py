"""Build-time script to test and validate schema minification and TypeScript signatures for jcodemunch-mcp."""

from __future__ import annotations

import json
import sys
from jcodemunch_mcp.server import _raw_catalog_tools
from jcodemunch_mcp.schema_minifier import (
    json_schema_to_typescript_signature,
    minify_description,
    minify_json_schema,
)


def main() -> None:
    tools = _raw_catalog_tools()
    print(f"Total tools in raw catalog: {len(tools)}")
    
    total_raw_bytes = 0
    total_min_bytes = 0
    
    signatures: list[str] = []
    
    for tool in tools:
        raw_desc = tool.description or ""
        raw_schema = tool.inputSchema or {}
        
        raw_json_str = json.dumps({"description": raw_desc, "inputSchema": raw_schema})
        total_raw_bytes += len(raw_json_str)
        
        min_desc = minify_description(raw_desc)
        min_schema = minify_json_schema(raw_schema)
        sig = json_schema_to_typescript_signature(tool.name, min_schema)
        
        min_json_str = json.dumps({"description": min_desc, "signature": sig})
        total_min_bytes += len(min_json_str)
        
        signatures.append(sig)
    
    reduction_pct = (1.0 - (total_min_bytes / max(1, total_raw_bytes))) * 100.0
    print(f"Raw schema payload size: {total_raw_bytes} bytes")
    print(f"Minified detail payload size: {total_min_bytes} bytes")
    print(f"Minification Reduction: {reduction_pct:.2f}%")
    
    print("\nSample Compact TypeScript Signatures:")
    for s in signatures[:10]:
        print(f"  - {s}")


if __name__ == "__main__":
    main()
