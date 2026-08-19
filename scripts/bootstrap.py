#!/usr/bin/env python3
"""Build the virtualenv the REAPER MCP server runs in.

Run this once after installing the plugin, or any time `reaper_setup_status` says dependencies are missing:

    python scripts/bootstrap.py

Why a dedicated venv rather than `pip install` into whatever Python is on PATH:
  * To isolate dependencies and prevent interference with other projects.
  * To persist across plugin updates without triggering full reinstalls.
  * To allow the use of a different interpreter from the one Claude launches with.

This script is not executed from the launcher at startup to prevent timeout failures during slow initial installations.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "requirements.txt"

# These variables dictate interpreter selection. They are imported to ensure synchronization with the launcher.
from _launcher import REQUIRED as CORE_IMPORTS  # noqa: E402
from _launcher import data_dir  # noqa: E402


def venv_python(root: Path) -> Path:
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def base_python() -> Path:
    """The base of whatever interpreter is running this.

    To access shared libraries, the base installation path is required instead of the virtualenv redirect.
    """
    if sys.prefix != sys.base_prefix:
        exe = Path(sys.base_prefix) / ("python.exe" if os.name == "nt" else "bin/python3")
        if exe.is_file():
            return exe
    return Path(sys.executable)


def _probe(argv) -> Path | None:
    """Return an interpreter's own path if it is <= 3.12, else None."""
    code = (
        "import sys;"
        "sys.stdout.write(sys.executable)"
        " if sys.version_info[:2] <= (3, 12) else sys.exit(1)"
    )
    try:
        p = subprocess.run(argv + ["-c", code], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0 or not p.stdout.strip():
        return None
    exe = Path(p.stdout.strip())
    return exe if exe.is_file() else None


def reaper_python() -> Path:
    """The interpreter REAPER should embed, and where reapy belongs.

    REAPER's embedded interpreter choice is determined during the configure step. 
    This function selects an installation (preferably <= 3.12) to ensure reapy functions correctly without affecting the user's default environment.
    """
    override = os.environ.get("REAPER_MCP_REAPER_PYTHON")
    if override and Path(override).is_file():
        return Path(override)

    candidates = []
    if os.name == "nt":
        for v in ("3.12", "3.11", "3.10"):
            candidates.append(["py", "-" + v])
        candidates.append([str(Path(os.environ.get("LOCALAPPDATA", ""))
                               / "Programs" / "Python" / "Python312" / "python.exe")])
    else:
        for v in ("3.12", "3.11", "3.10"):
            candidates.append(["python" + v])

    for argv in candidates:
        if argv[0] and (len(argv) > 1 or Path(argv[0]).is_file() or shutil.which(argv[0])):
            found = _probe(argv)
            if found:
                return found

    base = base_python()
    if _probe([str(base)]):
        return base
    return base


def can_import(python: Path, module: str) -> bool:
    return subprocess.run(
        [str(python), "-c", f"import {module}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0


def ensure_reaper_side(force: bool = False) -> bool:
    """Put reapy where REAPER can import it.

    This ensures both the MCP server and REAPER's embedded interpreter have access to reapy, preventing connection failures.
    The target is the interpreter identified by `reaper_python()`.
    """
    base = reaper_python()
    print(f"REAPER-side: {base}")

    if not force and can_import(base, "reapy"):
        print("reapy importable.")
        return True

    print("Installing python-reapy.")
    proc = subprocess.run(
        [str(base), "-m", "pip", "install", "--user", "--upgrade", "python-reapy>=0.10.0"]
    )
    if proc.returncode != 0:
        print("pip install failed.", file=sys.stderr)
        return False

    if not can_import(base, "reapy"):
        print("reapy not importable after install.", file=sys.stderr)
        return False

    print("Success.")
    return True


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
    ap.add_argument("--recreate", action="store_true", help="Delete and rebuild the virtualenv.")
    ap.add_argument("--print-reaper-python", action="store_true", help="Print the interpreter REAPER should embed, and exit.")
    ap.add_argument("--allow-source", action="store_true", help="Permit building packages from source.")
    args = ap.parse_args()

    if args.print_reaper_python:
        print(reaper_python())
        return 0

    if not REQUIREMENTS.is_file():
        print(f"Missing {REQUIREMENTS}", file=sys.stderr)
        return 1

    target = data_dir() / "venv"
    stamp = data_dir() / "requirements.sha256"
    py = venv_python(target)

    print(f"Plugin: {ROOT}")
    print(f"Environment: {target}")

    if args.check:
        base = reaper_python()
        reaper_ok = can_import(base, "reapy")
        print(f"REAPER-side: {base}")
        print(f"reapy importable: {'yes' if reaper_ok else 'NO'}")

        if not py.is_file():
            print("Status: not created.")
            return 1
        missing = check(py)
        if missing:
            print(f"Status: incomplete. Missing: {', '.join(missing)}")
            return 1
        if not reaper_ok:
            print("Status: server ready, REAPER side failed.")
            return 1

        if not can_import(py, "librosa"):
            print("Analysis: incomplete.")
            return 1
        print("Analysis: available.")
        print("Status: ready.")
        return 0

    if args.recreate and target.exists():
        import shutil
        print("Removing existing environment.")
        shutil.rmtree(target, ignore_errors=True)

    if not py.is_file():
        print(f"Creating virtualenv with {sys.executable}.")
        venv.EnvBuilder(with_pip=True, clear=False).create(target)

    if not py.is_file():
        print(f"Virtualenv creation failed for {py}", file=sys.stderr)
        return 1

    digest = requirements_digest()
    current = stamp.read_text().strip() if stamp.is_file() else ""
    if current == digest and not args.force and not check(py):
        print("Status: up to date.")
        return 0 if ensure_reaper_side() else 1

    print("Installing dependencies.")

    cmd = [str(py), "-m", "pip", "install", "--upgrade", "-r", str(REQUIREMENTS)]
    if not args.allow_source:
        # Prevents long compilation times by enforcing binary packages.
        cmd.append("--only-binary=:all:")

    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        print("pip install failed.", file=sys.stderr)
        return proc.returncode

    missing = check(py)
    if missing:
        print(f"Missing imports after install: {', '.join(missing)}.", file=sys.stderr)
        return 1

    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(digest)

    if not ensure_reaper_side(force=args.force):
        print("REAPER interpreter setup failed.", file=sys.stderr)
        return 1

    print("Status: ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
