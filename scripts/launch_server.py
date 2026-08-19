#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Entry point for the reaper MCP server.

This file is Python 2 and 3 compatible to prevent SyntaxErrors when executed by legacy Python 2 interpreters. This script resolves the appropriate Python 3 interpreter and executes the _launcher.py script. Standard output is reserved for the MCP stdio transport.
"""

import os
import subprocess
import sys

# _launcher.py requires a minimum Python version of 3.8.
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
    """Check if the provided Python interpreter meets the minimum version requirement."""
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
    """Return a list of potential Python interpreter paths for environment resolution."""
    found = []

    override = os.environ.get("REAPER_MCP_PYTHON")
    if override:
        found.append([override])

    # Prioritize the existing virtual environment.
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
        log("File not found: " + REAL_LAUNCHER)
        return 1

    # Direct import avoids subprocessing overhead if the current interpreter meets version requirements.
    if sys.version_info[:2] >= MIN_VERSION:
        sys.path.insert(0, HERE)
        import _launcher
        return _launcher.main()

    log("Current Python version %d.%d is below minimum requirement." % (sys.version_info[0], sys.version_info[1]))
    log("Searching for alternative Python interpreter.")

    for argv in candidates():
        if len(argv) == 1 and not os.path.isfile(argv[0]):
            # Bare executable names are skipped if they contain directory separators and the path does not exist.
            if os.sep in argv[0] or (os.altsep and os.altsep in argv[0]):
                continue
        if not is_usable(argv):
            continue

        log("Selected interpreter: " + " ".join(argv))
        try:
            return subprocess.call(argv + [REAL_LAUNCHER] + args)
        except (OSError, ValueError):
            error = sys.exc_info()[1]
            log("Execution failed: " + str(error))
            continue

    log("Error: Python 3.8 or newer is required.")
    log("Run the following command to install Python 3.12:")
    log("winget install -e --id Python.Python.3.12")
    log("Alternatively, set the REAPER_MCP_PYTHON environment variable to the path of a compatible Python executable.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
