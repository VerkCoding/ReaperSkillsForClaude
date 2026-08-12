"""A one-tool MCP server for when the real one cannot start.

Without this, a missing dependency shows up as "reaper: failed to connect" and
nothing else - no cause, no fix, and no way for Claude to find out more. Serving
a single honest tool instead means Claude can ask what went wrong and relay the
answer, which turns a dead end into one instruction the user can act on.

This module imports only `mcp`. Importing anything else would defeat its
purpose, since a broken dependency is exactly why it is running.
"""

import sys
from pathlib import Path


def serve(root: Path, detail: str) -> None:
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("reaper-mcp-unavailable")

    @mcp.tool()
    def reaper_setup_status() -> str:
        """Explain why the REAPER tools are unavailable and how to fix it.

        Call this whenever the user asks about REAPER and no REAPER tools are
        present, or when a REAPER request cannot be served.
        """
        return (
            "The REAPER MCP server could not start, so none of its tools "
            "(tracks, FX, MIDI, mixing, mastering, rendering, analysis) are "
            f"available.\n\n{detail}\n\n"
            f"Plugin directory: {root}\n"
            f"Interpreter that tried to start it: {sys.executable}\n\n"
            "The file bridge is unaffected: if REAPER is running with "
            "claude_bridge.lua loaded, Lua can still be sent through it, so "
            "measurement and rendering work remains possible."
        )

    mcp.run(transport="stdio")
