"""Provides a minimal MCP server to expose startup errors.

The primary server fails silently on missing dependencies. Exposing a single
diagnostic tool allows the system to communicate the error state.

Dependencies are restricted to `mcp` to ensure execution when other imports fail.
"""

import sys
from pathlib import Path


def serve(root: Path, detail: str) -> None:
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("reaper-mcp-unavailable")

    @mcp.tool()
    def reaper_setup_status() -> str:
        """Returns the startup error state for the REAPER server.

        Invoked to retrieve diagnostic information when normal REAPER tools are not registered.
        """
        return (
            "REAPER MCP server initialization failed.\n\n"
            f"Error detail: {detail}\n\n"
            f"Plugin directory: {root}\n"
            f"Python executable: {sys.executable}\n\n"
            "File bridge functionality remains active via claude_bridge.lua."
        )

    mcp.run(transport="stdio")
