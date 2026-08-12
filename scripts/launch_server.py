#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Entry point for the `reaper` MCP server. Deliberately Python 2 compatible.

The manifest launches this with the command `python`, and on some machines
`python` is Python 2 - people who have kept a 2.7 as their default for years
have a good reason to, and taking it over to install an audio plugin is not our
call. But `_launcher.py` is modern Python, and Python parses a whole file before
running any of it, so a single f-string in there would make a 2.7 interpreter
fail with a SyntaxError before reaching any check that could explain why.

Hence the split. This file is the only thing `python` has to be able to parse,
so it is written in the intersection of 2 and 3:

    no f-strings, no pathlib, no subprocess.run, no annotations, no `X | None`

It works out which interpreter should really run the server and hands over to
`_launcher.py`, which is free to be written normally. Under a Python 3 that is
new enough, there is no re-exec at all - it just imports it.

Nothing is printed to stdout: that belongs to the MCP stdio transport, and one
stray line corrupts the protocol stream.
"""

import os
import subprocess
import sys

# The floor for _launcher.py itself, not for the server. The server runs from
# the virtualenv and the launcher re-execs into it; this is only "new enough to
# read the next file".
MIN_VERSION = (3, 8)

HERE = os.path.dirname(os.path.abspath(__file__))
REAL_LAUNCHER = os.path.join(HERE, "_launcher.py")

VERSION_TEST = (
    "import sys; sys.exit(0 if sys.version_info[:2] >= (%d, %d) else 1)"
    % (MIN_VERSION[0], MIN_VERSION[1])
)


def log(message):
    sys.stderr.write("[reaper-mcp] " + message + "\n")
    sys.stderr.flush()


def is_usable(argv):
    """Can this command run a Python new enough to read _launcher.py?"""
    try:
        devnull = open(os.devnull, "wb")
    except IOError:
        devnull = None
    try:
        code = subprocess.call(
            argv + ["-c", VERSION_TEST], stdout=devnull, stderr=devnull
        )
    except (OSError, ValueError):
        return False
    finally:
        if devnull is not None:
            devnull.close()
    return code == 0


def candidates():
    """Where to look for a modern Python, without consulting PATH order.

    The `py` launcher matters most here: it reaches a side-by-side install that
    PATH deliberately does not mention, which is the whole arrangement this
    setup is built on.
    """
    found = []

    override = os.environ.get("REAPER_MCP_PYTHON")
    if override:
        found.append([override])

    # The plugin's own virtualenv, which already has everything.
    home = os.path.expanduser("~")
    if os.name == "nt":
        found.append([os.path.join(home, ".reaper-for-claude", "venv", "Scripts", "python.exe")])
    else:
        found.append([os.path.join(home, ".reaper-for-claude", "venv", "bin", "python")])

    if os.name == "nt":
        for version in ("-3.12", "-3.11", "-3.13", "-3.10", "-3"):
            found.append(["py", version])
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            for name in ("Python312", "Python311", "Python313", "Python310"):
                found.append([os.path.join(local, "Programs", "Python", name, "python.exe")])
        for name in ("Python312", "Python311", "Python313", "Python310"):
            found.append([os.path.join("C:\\", name, "python.exe")])
    else:
        for name in ("python3.12", "python3.11", "python3", "python3.13", "python3.10"):
            found.append([name])

    return found


def main():
    args = sys.argv[1:]

    if not os.path.isfile(REAL_LAUNCHER):
        log("Missing " + REAL_LAUNCHER + " - this install is incomplete.")
        return 1

    # Already modern enough: import rather than spawn, so the common case costs
    # nothing and the server keeps this process's stdin and stdout.
    if sys.version_info[:2] >= MIN_VERSION:
        sys.path.insert(0, HERE)
        import _launcher
        return _launcher.main()

    log(
        "This is Python %d.%d, which is too old to run the REAPER server."
        % (sys.version_info[0], sys.version_info[1])
    )
    log("Looking for a newer one - your default Python is left alone.")

    for argv in candidates():
        if len(argv) == 1 and not os.path.isfile(argv[0]):
            # A bare name like "python3" may still resolve; a path that does not
            # exist cannot.
            if os.sep in argv[0] or (os.altsep and os.altsep in argv[0]):
                continue
        if not is_usable(argv):
            continue

        log("Using: " + " ".join(argv))
        try:
            return subprocess.call(argv + [REAL_LAUNCHER] + args)
        except (OSError, ValueError):
            error = sys.exc_info()[1]
            log("Could not start it: " + str(error))
            continue

    log("")
    log("No Python 3.8 or newer could be found on this machine.")
    log("")
    log("The REAPER server needs one. Your existing Python is not touched -")
    log("install a newer one alongside it, then restart Claude:")
    log("")
    log("    winget install -e --id Python.Python.3.12")
    log("")
    log("If you already have one somewhere unusual, point this at it by")
    log("setting REAPER_MCP_PYTHON to its full path.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
