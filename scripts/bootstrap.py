#!/usr/bin/env python3
"""Build the virtualenv the REAPER MCP server runs in.

Run this once after installing the plugin, or any time `reaper_setup_status`
says dependencies are missing:

    python scripts/bootstrap.py

Why a dedicated venv rather than `pip install` into whatever Python is on PATH:

  * It cannot break anything else. reapy pins nothing and drags in numpy,
    librosa and soundfile; installing that set into a system Python is how
    people end up with a broken unrelated project.
  * It survives plugin updates. The venv lives under the host's persistent
    plugin data directory, so a version bump does not trigger a multi-minute
    reinstall of librosa.
  * It can use a different interpreter from the one Claude launches with. The
    launcher probes for the venv first, so bootstrapping with a Python that
    works fixes the plugin without touching any Claude configuration.

Deliberately NOT run from the launcher at startup: a cold install of this
dependency set takes minutes, and an MCP server that does not answer its
initialize request within the host's timeout is dropped as a failed server.
Installing on demand would turn a slow first run into a broken one.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "requirements.txt"

# Kept in step with launch_server.REQUIRED - the imports that must succeed for
# the server to come up at all, as opposed to the lazily imported analysis
# libraries whose absence only costs individual tools. The submodule matters:
# mcp 2.0 dropped mcp.server.fastmcp, so a bare `import mcp` passes on a version
# the server cannot run on.
CORE_IMPORTS = ("mcp.server.fastmcp", "reapy", "numpy")


def data_dir() -> Path:
    """One fixed location, deliberately NOT the host's plugin data directory.

    CLAUDE_PLUGIN_DATA looks like the right home for this, and it is wrong for
    two reasons. It is only set when the host launches the server, so the
    installer - run from a terminal - would build the venv somewhere else and
    the server would never find it. And its value encodes the install route, so
    the marketplace copy and a developer link get different directories, meaning
    a multi-minute librosa install per route.

    A fixed path under the home directory is found identically by the installer,
    the launcher and the health check, however each was started.
    """
    env = os.environ.get("REAPER_MCP_DATA_DIR")
    if env and not env.startswith("${"):
        return Path(env)
    return Path.home() / ".reaper-for-claude"


def venv_python(root: Path) -> Path:
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def requirements_digest() -> str:
    return hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()


def check(python: Path) -> list:
    """Return the core imports that fail under this interpreter."""
    missing = []
    for mod in CORE_IMPORTS:
        proc = subprocess.run(
            [str(python), "-c", f"import {mod}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if proc.returncode != 0:
            missing.append(mod)
    return missing


def main() -> int:
    ap = argparse.ArgumentParser(description="Set up the REAPER MCP environment.")
    ap.add_argument("--check", action="store_true", help="Report status, change nothing.")
    ap.add_argument("--force", action="store_true", help="Reinstall even if up to date.")
    ap.add_argument(
        "--recreate", action="store_true", help="Delete and rebuild the virtualenv."
    )
    args = ap.parse_args()

    if not REQUIREMENTS.is_file():
        print(f"Missing {REQUIREMENTS}", file=sys.stderr)
        return 1

    target = data_dir() / "venv"
    stamp = data_dir() / "requirements.sha256"
    py = venv_python(target)

    print(f"Plugin:      {ROOT}")
    print(f"Environment: {target}")

    if args.check:
        if not py.is_file():
            print("Status:      not created yet  ->  python scripts/bootstrap.py")
            return 1
        missing = check(py)
        if missing:
            print(f"Status:      incomplete, cannot import: {', '.join(missing)}")
            return 1
        print("Status:      ready")
        return 0

    if args.recreate and target.exists():
        import shutil

        print("Removing the existing environment...")
        shutil.rmtree(target, ignore_errors=True)

    if not py.is_file():
        print(f"Creating the virtualenv with {sys.executable} ...")
        # with_pip is what makes this usable straight after creation; the
        # alternative is bootstrapping pip by hand into a bare venv.
        venv.EnvBuilder(with_pip=True, clear=False).create(target)

    if not py.is_file():
        print(f"Virtualenv creation did not produce {py}", file=sys.stderr)
        return 1

    digest = requirements_digest()
    current = stamp.read_text().strip() if stamp.is_file() else ""
    if current == digest and not args.force and not check(py):
        print("Status:      already up to date (requirements unchanged)")
        return 0

    print("Installing dependencies. A cold install takes a few minutes.")
    proc = subprocess.run(
        [str(py), "-m", "pip", "install", "--upgrade", "-r", str(REQUIREMENTS)]
    )
    if proc.returncode != 0:
        print("\npip failed. The output above says why.", file=sys.stderr)
        return proc.returncode

    missing = check(py)
    if missing:
        print(
            f"\npip finished but these still do not import: {', '.join(missing)}.\n"
            "That usually means no wheel exists for this Python version. Try "
            "bootstrapping with a different interpreter:\n"
            f"    py -3.12 \"{Path(__file__).resolve()}\" --recreate",
            file=sys.stderr,
        )
        return 1

    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(digest)

    print("\nStatus:      ready")
    print("Restart Claude so it picks up the new environment.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
