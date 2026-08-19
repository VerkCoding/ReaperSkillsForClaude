#!/usr/bin/env python3
r"""Enable the reapy distant API for MCP server communication with REAPER.

Execution methods:
1. Terminal or RunThisToStart.bat execution. REAPER must be closed to prevent reaper.ini overwrite on exit.
       python enable_reapy.py
       python enable_reapy.py --resource-path "E:\REAPER\Portable"
       python enable_reapy.py --check
       python enable_reapy.py --repair
2. REAPER execution via Actions > Show action list... > ReaScript: Run... > this file. REAPER restart required.

Operations performed via `reapy.config.configure_reaper()`:
1. enable_python(): Sets reascript=1 and path to Python shared library.
2. add_web_interface(): Adds REAPER web interface on port 2307.
3. add_reascript(): Registers activate_reapy_server action in reaper-kb.ini.
4. set_ext_state(): Records action id in reaper-extstate.ini for execution.

Port 2306 must not host a web interface, as it is the REAPY_SERVER_PORT. Use --repair to correct this configuration if present.
"""

import argparse
import os
import subprocess
import sys
import warnings

# Suppress reapy distant API connection warning during initial setup phase.
warnings.filterwarnings("ignore", module=r"reapy\..*")

REAPY_SERVER_PORT = 2306
WEB_INTERFACE_PORT = 2307

# Restrict configuration writes to Python 3.12 and lower to prevent configparser exception related to unnamed sections introduced in 3.13, which results in truncated reaper.ini files.
CONFIGURE_MAX_PYTHON = (3, 12)


# Terminal and REAPER console output handling.

def _inside_reaper() -> bool:
    try:
        import reaper_python  # noqa: F401
        return True
    except ImportError:
        return False


INSIDE = _inside_reaper()


def say(msg: str = "") -> None:
    if INSIDE:
        try:
            from reaper_python import RPR_ShowConsoleMsg
            RPR_ShowConsoleMsg(msg + "\n")
            return
        except Exception:
            pass
    print(msg)


def fail(msg: str) -> "NoReturn":  # noqa: F821
    say("ERROR: " + msg)
    sys.exit(1)


# Locating REAPER

def resolve_resource_path(explicit: str = "") -> str:
    """Locate REAPER resource directory containing reaper.ini."""
    if explicit:
        p = os.path.abspath(os.path.expandvars(os.path.expanduser(explicit)))
        if not os.path.isdir(p):
            fail(f"--resource-path does not exist: {p}")
        return p

    if INSIDE:
        from reaper_python import RPR_GetResourcePath
        return RPR_GetResourcePath()

    # Set detect_portable_install=False to avoid dependency on running REAPER processes.
    try:
        from reapy.config.resource_path import get_resource_path
        return get_resource_path(detect_portable_install=False)
    except Exception:
        pass

    for candidate in (
        os.path.join(os.environ.get("APPDATA", ""), "REAPER"),
        os.path.expanduser("~/Library/Application Support/REAPER"),
        os.path.expanduser("~/.config/REAPER"),
    ):
        if candidate and os.path.isfile(os.path.join(candidate, "reaper.ini")):
            return candidate

    fail("REAPER resource folder not found. Require reaper.ini generation or --resource-path argument.")


def reaper_is_running() -> bool:
    """Process execution check for REAPER."""
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq reaper.exe", "/NH"],
                capture_output=True, text=True, timeout=10,
            ).stdout.lower()
            return "reaper.exe" in out
        out = subprocess.run(
            ["pgrep", "-x", "REAPER"], capture_output=True, text=True, timeout=10
        )
        return out.returncode == 0
    except Exception:
        return False


# System state inspection.

def report(resource_path: str) -> bool:
    """Output configuration state. Returns boolean indicating validity."""
    from reapy.config.config import Config, web_interface_exists

    ini = os.path.join(resource_path, "reaper.ini")
    say(f"REAPER resource path: {resource_path}")
    say(f"reaper.ini: {'found' if os.path.isfile(ini) else 'missing'}")

    ok = True

    has_web = web_interface_exists(resource_path, WEB_INTERFACE_PORT)
    say(f"Web interface port {WEB_INTERFACE_PORT}: {'present' if has_web else 'missing'}")
    ok = ok and has_web

    bogus = web_interface_exists(resource_path, REAPY_SERVER_PORT)
    say(f"Web interface port {REAPY_SERVER_PORT}: {'present (invalid)' if bogus else 'absent'}")
    ok = ok and not bogus

    cfg = Config(ini)
    reascript = cfg["reaper"].get("reascript", "0") if "reaper" in cfg else "0"
    say(f"Python ReaScript: {'enabled' if reascript == '1' else 'disabled'}")
    ok = ok and reascript == "1"

    kb = os.path.join(resource_path, "reaper-kb.ini")
    registered = False
    if os.path.isfile(kb):
        with open(kb, encoding="utf-8", errors="ignore") as f:
            registered = "activate_reapy_server" in f.read()
    say(f"activate_reapy_server: {'registered' if registered else 'unregistered'}")
    ok = ok and registered

    return ok


# Configuration modification actions.

def _guard_ini(resource_path: str):
    """File backup mechanism for reaper.ini to prevent data loss during write operations."""
    import shutil
    import tempfile

    ini = os.path.join(resource_path, "reaper.ini")
    if not os.path.isfile(ini):
        return lambda ok=True: None

    size_before = os.path.getsize(ini)
    fd, tmp = tempfile.mkstemp(prefix="reaper.ini.", suffix=".guard")
    os.close(fd)
    shutil.copyfile(ini, tmp)

    def finish(ok: bool = True) -> None:
        try:
            shrank = os.path.isfile(ini) and os.path.getsize(ini) < size_before
            if (not ok) or shrank:
                shutil.copyfile(tmp, ini)
                if shrank:
                    say(f"reaper.ini size decreased from {size_before} to {os.path.getsize(ini)} bytes. Restored backup.")
                else:
                    say("Restored reaper.ini backup following write failure.")
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

    return finish


def do_configure(resource_path: str) -> None:
    v = sys.version_info[:2]
    if v > CONFIGURE_MAX_PYTHON:
        fail(
            f"Python {v[0]}.{v[1]} incompatible with safe configuration.\n"
            "reapy 0.10.0 causes truncation of reaper.ini on Python 3.13+.\n"
            "Execute with Python 3.12 or lower:\n"
            f"    py -3.12 \"{os.path.abspath(__file__)}\""
        )

    from reapy.config import configure_reaper

    say("Executing REAPER configuration.")
    restore = _guard_ini(resource_path)
    try:
        configure_reaper(resource_path=resource_path)
    except Exception:
        restore(ok=False)
        raise
    restore(ok=True)
    say("Configuration execution complete.")


def _prune_orphan_csurfs(resource_path: str) -> int:
    """Remove csurf_N registry keys where N >= csurf_cnt to resolve index duplication post-deletion."""
    from reapy.config.config import Config

    cfg = Config(os.path.join(resource_path, "reaper.ini"))
    if "reaper" not in cfg:
        return 0
    count = int(cfg["reaper"].get("csurf_cnt", "0"))
    orphans = [k for k in list(cfg["reaper"].keys())
               if k.lower().startswith("csurf_")
               and k.lower() != "csurf_cnt"
               and k.split("_", 1)[1].isdigit()
               and int(k.split("_", 1)[1]) >= count]
    for k in orphans:
        del cfg["reaper"][k]
    if orphans:
        cfg.write()
    return len(orphans)


def do_repair(resource_path: str) -> None:
    from reapy.config.config import delete_web_interface, web_interface_exists

    if web_interface_exists(resource_path, REAPY_SERVER_PORT):
        say(f"Removing web interface on port {REAPY_SERVER_PORT}.")
        delete_web_interface(resource_path, REAPY_SERVER_PORT)
        say(f"Web interface on port {REAPY_SERVER_PORT} removed.")
    else:
        say(f"Web interface on port {REAPY_SERVER_PORT} absent.")

    pruned = _prune_orphan_csurfs(resource_path)
    if pruned:
        say(f"Removed {pruned} orphaned csurf keys.")


# Execution entry point.

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Configure REAPER distant API via reapy.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--resource-path", default="",
                        help="REAPER resource directory path.")
    parser.add_argument("--check", action="store_true",
                        help="Output current configuration state.")
    parser.add_argument("--repair", action="store_true",
                        help="Remove web interface on port 2306.")
    parser.add_argument("--force", action="store_true",
                        help="Bypass REAPER execution check.")

    # Apply default parsing for internal REAPER execution.
    args = parser.parse_args([] if INSIDE else (argv if argv is not None else sys.argv[1:]))

    try:
        import reapy  # noqa: F401
    except Exception as e:
        # Avoid listing supported Python versions due to environment dependencies.
        v = f"{sys.version_info.major}.{sys.version_info.minor}"
        fail(
            f"Import failure for reapy under Python {v}: {e}\n"
            "Execute using bootstrapped interpreter:\n"
            "    python scripts/bootstrap.py"
        )

    resource_path = resolve_resource_path(args.resource_path)

    if args.check:
        say("")
        ok = report(resource_path)
        say("")
        say("Configuration valid." if ok else "Configuration invalid. Execution required.")
        return 0 if ok else 1

    # Check for REAPER process to prevent concurrent file modification issues on exit.
    if not INSIDE and not args.force and reaper_is_running():
        fail("REAPER process detected. Terminate REAPER or use --force.")

    if args.repair:
        do_repair(resource_path)
    else:
        do_repair(resource_path)
        do_configure(resource_path)

    say("")
    report(resource_path)
    say("")
    say("REAPER restart required.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:
        say(f"Unexpected error: {e}")
        sys.exit(1)
