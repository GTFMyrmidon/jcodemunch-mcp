"""Entry point declared by the MCPB manifest.

The manifest's mcp_config launches `uvx jcodemunch-mcp`, which is the path
clients actually take. This module is the equivalent entry point for a host
that runs `python server/main.py` directly against an already-installed
package, and it satisfies the manifest schema's required `entry_point`.
"""

import sys


def main():
    try:
        from jcodemunch_mcp.server import main as server_main
    except ImportError:
        sys.exit(
            "jcodemunch-mcp is not installed in this interpreter. "
            "Install it with `pip install jcodemunch-mcp`, or let the bundle "
            "launch it via `uvx jcodemunch-mcp` as the manifest specifies."
        )
    server_main()


if __name__ == "__main__":
    main()
