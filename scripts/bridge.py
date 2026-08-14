#!/usr/bin/env python3
"""Send one Lua chunk to a running REAPER through the claude_bridge protocol.

Replaces the PowerShell client on every platform, and closes the trap that
client could not: the old workflow was "write cmd.lua.tmp with an editor, then
point run.ps1 at it", and writing that file with Windows PowerShell 5.1 prepends
a UTF-8 BOM, which the bridge rejects with PARSE_ERROR on byte one. Accepting
the Lua on stdin or as an argument means the encoding is decided here, once, and
the failure mode is gone rather than documented.

    python bridge.py --code 'return reaper.GetAppVersion()'
    echo 'return 1+1' | python bridge.py
    python bridge.py --lua-file cmd.lua.tmp --timeout 240

Exits non-zero when the bridge reports PARSE_ERROR or RUNTIME_ERROR, so a
failure cannot be mistaken for a result.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# Where REAPER keeps its resource directory per platform. claude_bridge.lua
# creates the bridge subdirectory inside whichever one is live.
RESOURCE_ROOTS = (
    Path(os.environ.get("APPDATA", "")) / "REAPER",
    Path.home() / "Library" / "Application Support" / "REAPER",
    Path.home() / ".config" / "REAPER",
)

STALE_HEARTBEAT_SEC = 60
POLL_SEC = 0.2


def find_bridge_dir(explicit: str | None) -> Path:
    if explicit:
        d = Path(explicit)
        if not d.is_dir():
            raise SystemExit(f"Bridge directory does not exist: {d}")
        return d

    env = os.environ.get("REAPER_BRIDGE_DIR")
    if env and Path(env).is_dir():
        return Path(env)

    for root in RESOURCE_ROOTS:
        candidate = root / "claude_bridge"
        if candidate.is_dir():
            return candidate

    raise SystemExit(
        "Bridge directory not found. REAPER creates it at startup by running "
        "claude_bridge.lua.\n"
        "  - Start REAPER, or\n"
        "  - pass --bridge-dir for a portable REAPER install, or\n"
        "  - re-run the installer if REAPER is running but the directory is absent."
    )


def read_lua(args) -> str:
    if args.code:
        return args.code
    if args.lua_file:
        p = Path(args.lua_file)
        if not p.is_file():
            raise SystemExit(f"Lua file not found: {p}")
        # utf-8-sig strips a BOM if the file happens to have one, so a chunk
        # written by any tool still reaches REAPER clean.
        return p.read_text(encoding="utf-8-sig")
    data = sys.stdin.read()
    if not data.strip():
        raise SystemExit("No Lua supplied. Use --code, --lua-file, or pipe it on stdin.")
    return data


def main() -> int:
    ap = argparse.ArgumentParser(description="Run Lua inside REAPER via the file bridge.")
    ap.add_argument("--code", help="Lua source to run.")
    ap.add_argument("--lua-file", help="File containing the Lua source.")
    ap.add_argument("--bridge-dir", help="Override the bridge directory.")
    ap.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Seconds to wait for a result. Renders block REAPER; raise this rather "
        "than assuming failure.",
    )
    ap.add_argument("--quiet", action="store_true", help="Suppress the timing line.")
    args = ap.parse_args()

    lua = read_lua(args)
    bridge = find_bridge_dir(args.bridge_dir)

    cmd = bridge / "cmd.lua"
    out = bridge / "out.txt"
    status = bridge / "status.txt"

    # Fail informatively instead of burning the full timeout when the listener
    # simply is not running.
    if status.is_file():
        age = time.time() - status.stat().st_mtime
        if age > STALE_HEARTBEAT_SEC:
            print(
                f"[warn] Bridge heartbeat is {age:.0f}s old - REAPER may be closed or "
                "the listener stopped. Continuing anyway.",
                file=sys.stderr,
            )

    if cmd.exists():
        print(
            "[warn] cmd.lua is already present; the previous command may not have been "
            "consumed. The bridge script may not be running in REAPER.",
            file=sys.stderr,
        )

    # Snapshot before writing, so a result can be told apart from the previous
    # command's output. Sleeping a fixed amount instead would hand back stale
    # data that looks perfectly valid - the failure this design exists to avoid.
    before = out.stat().st_mtime_ns if out.exists() else -1

    # Write beside the target and rename: REAPER polls this directory, and a
    # partially written cmd.lua would be picked up and fail to parse.
    tmp = bridge / "cmd.lua.partial"
    tmp.write_text(lua, encoding="utf-8", newline="\n")
    os.replace(tmp, cmd)

    start = time.monotonic()
    got = False
    while time.monotonic() - start < args.timeout:
        time.sleep(POLL_SEC)
        if out.exists() and out.stat().st_mtime_ns > before:
            got = True
            break

    if not got:
        if cmd.exists():
            # Still present means it was never picked up: the listener consumes
            # and deletes the file before running it, so a slow command has
            # already removed it. Take it back. Leaving it would arm a delayed
            # trap - REAPER runs whatever is sitting here the next time it
            # starts, which could be a render fired days later with no one
            # watching.
            try:
                cmd.unlink()
            except OSError:
                pass
            raise SystemExit(
                f"The bridge did not pick up the command within {args.timeout:.0f}s, so "
                "it was withdrawn. REAPER is probably not running claude_bridge.lua - "
                "check status.txt in the bridge directory, and see the reaper-core-setup skill."
            )
        raise SystemExit(
            f"No new out.txt after {args.timeout:.0f}s, though cmd.lua was consumed. The "
            "command is still running - renders block REAPER for minutes. Re-run with a "
            "higher --timeout, or have the Lua write its result to a side file and poll "
            "that instead."
        )

    result = out.read_text(encoding="utf-8", errors="replace")

    if not args.quiet:
        print(f"--- {time.monotonic() - start:.1f}s ---")
    print(result)

    return 1 if result.startswith(("PARSE_ERROR", "RUNTIME_ERROR")) else 0


if __name__ == "__main__":
    sys.exit(main())
