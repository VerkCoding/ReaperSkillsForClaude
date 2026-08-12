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
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "requirements.txt"

# Imported, not restated. The launcher owns both of these because it is the one
# that acts on them - it picks the interpreter - and a private copy here that
# drifted would have this script report an environment ready that the launcher
# then rejects, or build one somewhere the launcher never looks. Both failures
# are silent, which is exactly why they are not duplicated.
#
# sys.path[0] is this script's own directory when run as a script, so the
# sibling import resolves without any path juggling.
from launch_server import REQUIRED as CORE_IMPORTS  # noqa: E402
from launch_server import data_dir  # noqa: E402


def venv_python(root: Path) -> Path:
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def base_python() -> Path:
    """The base of whatever interpreter is running this.

    A virtualenv contains no python3XX.dll - it is a redirect layer around a
    real installation - so anything that needs a shared library needs the base.
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

    REAPER loads a Python *shared library* and runs ReaScripts inside it. Which
    one it loads is written into reaper.ini as pythonlibpath64, and reapy derives
    that from whichever interpreter runs the configure step. So this choice is
    the choice of REAPER's Python - and it has nothing to do with PATH.

    Preferring a 3.12 we installed over the user's default is the whole point.
    It keeps two promises at once:

      * Nothing is installed into the user's own Python. reapy has to live
        wherever REAPER looks, and pointing REAPER at our copy means that is our
        copy - not the interpreter their other projects depend on.
      * The version stays <= 3.12, which reapy needs in order to configure
        REAPER without emptying reaper.ini.

    Falls back to the running interpreter's base only when nothing better exists,
    so an installation that predates this still works.
    """
    override = os.environ.get("REAPER_MCP_REAPER_PYTHON")
    if override and Path(override).is_file():
        return Path(override)

    candidates = []
    if os.name == "nt":
        # The `py` launcher reaches a side-by-side install that PATH never
        # mentions, which is exactly the arrangement this is built around.
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
    """Put reapy where REAPER can import it, which the virtualenv cannot do.

    Two different interpreters have to import reapy, and only one of them is
    ours:

      * the MCP server, running outside REAPER - that is the virtualenv
      * activate_reapy_server.py, the ReaScript REAPER runs to start the distant
        API - that is REAPER's embedded interpreter

    Miss the second and everything looks installed while nothing connects: the
    server has reapy, REAPER does not, so the API it dials never comes up.

    It goes into the interpreter REAPER embeds - see reaper_python() - which is
    a 3.12 installed alongside the user's own wherever one is available. That is
    deliberate: reapy has to live where REAPER looks, and pointing REAPER at our
    copy is what stops this writing into the Python their other projects use.

    Only reapy goes there, never the rest. It needs psutil and typing-extensions
    and nothing else - no numpy, no numba, no librosa - so even that is three
    small pure-Python packages rather than the compiled numeric stack.
    """
    base = reaper_python()
    print(f"REAPER-side: {base}")

    if not force and can_import(base, "reapy"):
        print("             reapy already importable there")
        return True

    print("             installing python-reapy so REAPER's ReaScripts can import it")
    proc = subprocess.run(
        [str(base), "-m", "pip", "install", "--user", "--upgrade", "python-reapy>=0.10.0"]
    )
    if proc.returncode != 0:
        print("             pip failed; see the output above", file=sys.stderr)
        return False

    if not can_import(base, "reapy"):
        print(
            "             installed, but reapy still does not import there.",
            file=sys.stderr,
        )
        return False

    print("             ok")
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
    ap.add_argument(
        "--recreate", action="store_true", help="Delete and rebuild the virtualenv."
    )
    ap.add_argument(
        "--print-reaper-python",
        action="store_true",
        help="Print the interpreter REAPER should embed, and exit. The installer "
        "uses this so the choice is made in exactly one place.",
    )
    ap.add_argument(
        "--allow-source",
        action="store_true",
        help="Permit building packages from source. Off by default: a missing "
        "wheel means compiling llvmlite, which can take an hour and usually "
        "fails.",
    )
    args = ap.parse_args()

    if args.print_reaper_python:
        # Nothing else prints, so the caller can read stdout directly.
        print(reaper_python())
        return 0

    if not REQUIREMENTS.is_file():
        print(f"Missing {REQUIREMENTS}", file=sys.stderr)
        return 1

    target = data_dir() / "venv"
    stamp = data_dir() / "requirements.sha256"
    py = venv_python(target)

    print(f"Plugin:      {ROOT}")
    print(f"Environment: {target}")

    if args.check:
        base = reaper_python()
        reaper_ok = can_import(base, "reapy")
        print(f"REAPER-side: {base}")
        print(f"             reapy importable: {'yes' if reaper_ok else 'NO - required'}")

        if not py.is_file():
            print("Status:      not created yet  ->  python scripts/bootstrap.py")
            return 1
        missing = check(py)
        if missing:
            print(f"Status:      incomplete, cannot import: {', '.join(missing)}")
            return 1
        if not reaper_ok:
            # The virtualenv being complete is not enough. REAPER runs its own
            # interpreter, and without reapy there the distant API never starts.
            print("Status:      server ready, but REAPER cannot start its side")
            print("             -> python scripts/bootstrap.py")
            return 1

        if not can_import(py, "librosa"):
            # The server still starts - these are imported lazily - so this is
            # an incomplete install rather than a broken one.
            print("Analysis:    MISSING - loudness, spectrum and transient tools")
            print("             will fail. Re-run without --check to install.")
            return 1
        print("Analysis:    available")
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
        # Still verify the REAPER side: the virtualenv can be perfectly current
        # while REAPER's own interpreter has never had reapy installed.
        return 0 if ensure_reaper_side() else 1

    print("Size:        about 477 MB across ~12,000 files, most of it librosa's")
    print("             chain - llvmlite, scipy, scikit-learn, numba. A cold")
    print("             install takes a few minutes, longer in a VM.")

    cmd = [str(py), "-m", "pip", "install", "--upgrade", "-r", str(REQUIREMENTS)]
    if not args.allow_source:
        # Without this, a missing wheel silently falls back to building from
        # source. For llvmlite that is an hour of compilation that usually ends
        # in failure, and there is nothing on screen to say that is what is
        # happening - it just looks like the install has hung. Failing fast with
        # a version-mismatch error is far kinder.
        cmd.append("--only-binary=:all:")

    print("Installing dependencies...")
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        print("\npip failed. The output above says why.", file=sys.stderr)
        if not args.allow_source:
            print(
                "\nIf it reports that no matching distribution or wheel was found, "
                "this Python is likely too new for one of the packages. Either "
                "rebuild with an older interpreter:\n"
                f'    py -3.12 "{Path(__file__).resolve()}" --recreate\n'
                "or retry with --allow-source to compile it, which is slow and "
                "often fails.",
                file=sys.stderr,
            )
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

    print()
    if not ensure_reaper_side(force=args.force):
        print(
            "\nThe server environment is ready, but REAPER's own interpreter cannot "
            "import reapy, so the distant API will not start. Install it by hand:\n"
            f'    "{reaper_python()}" -m pip install --user python-reapy',
            file=sys.stderr,
        )
        return 1

    print("\nStatus:      ready")
    print("Restart Claude so it picks up the new environment.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
