#!/usr/bin/env python3
r"""Enable the reapy distant API so the MCP server can reach REAPER.

Two ways to run this
--------------------

1. From a terminal, or automatically from RunThisToStart.bat (preferred).
   REAPER must be CLOSED, because REAPER rewrites reaper.ini when it exits and
   would discard the changes.

       python enable_reapy.py
       python enable_reapy.py --resource-path "E:\REAPER\Portable"
       python enable_reapy.py --check
       python enable_reapy.py --repair

2. From inside REAPER, if you would rather not close it:
       Actions > Show action list... > ReaScript: Run... > this file
   Then restart REAPER.

What it does
------------

Connecting to REAPER from outside needs four things, all of which
``reapy.config.configure_reaper()`` handles and all of which are idempotent:

    1. enable_python()       reascript=1, plus the path to the Python shared
                             library, so REAPER can run Python ReaScripts
    2. add_web_interface()   a REAPER web interface on port 2307
    3. add_reascript()       registers reapy's activate_reapy_server action
                             in reaper-kb.ini
    4. set_ext_state()       records that action's id in reaper-extstate.ini
                             so REAPER actually runs it

An earlier version of this script hand-edited reaper.ini and only did part of
step 2. Steps 1, 3 and 4 were missing, which means nothing ever started the
reapy server, and ``reapy.connect()`` had nothing to talk to.

It also added a web interface on port 2306. That is wrong and actively harmful:
2306 is REAPY_SERVER_PORT, the socket reapy's own server binds. A web interface
there takes the port first, and the connection fails with WinError 10053. Run
``--repair`` to remove it.

Delegating to reapy instead of hand-editing also means REAPER's own ini backups
(.bak and .before-reapy.bak) get written for you.
"""

import argparse
import os
import subprocess
import sys
import warnings

# reapy warns at import time that it cannot reach the distant API and suggests
# enabling it. Enabling it is precisely what this script does, so the warning is
# noise that reads like an error.
warnings.filterwarnings("ignore", module=r"reapy\..*")

REAPY_SERVER_PORT = 2306   # reapy's server socket. NOT a web interface.
WEB_INTERFACE_PORT = 2307  # REAPER's web interface, which reapy talks through.

# The newest Python that reapy 0.10.0 can safely CONFIGURE with.
#
# Python 3.13 added unnamed sections to configparser, so parsing a file now
# yields a `_UnnamedSection` sentinel among the section names. reapy calls
# .lower() on each of them, which raises - and it does so partway through
# rewriting reaper.ini, leaving the file ZERO BYTES. Every REAPER preference,
# device setting and path is gone, with no error that mentions reaper.ini.
#
# Reading is unaffected, so --check still runs on any version. This gate applies
# only to the paths that write.
CONFIGURE_MAX_PYTHON = (3, 12)


# --------------------------------------------------------------------------
# Output that works both in a terminal and in REAPER's console
# --------------------------------------------------------------------------

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


# --------------------------------------------------------------------------
# Locating REAPER
# --------------------------------------------------------------------------

def resolve_resource_path(explicit: str = "") -> str:
    """Find REAPER's resource directory (the folder holding reaper.ini)."""
    if explicit:
        p = os.path.abspath(os.path.expandvars(os.path.expanduser(explicit)))
        if not os.path.isdir(p):
            fail(f"--resource-path does not exist: {p}")
        return p

    if INSIDE:
        from reaper_python import RPR_GetResourcePath
        return RPR_GetResourcePath()

    # Outside REAPER, ask reapy. detect_portable_install=False on purpose: the
    # detection works by finding a running REAPER process, and we want REAPER
    # closed. Portable installs pass --resource-path instead.
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

    fail(
        "Could not locate REAPER's resource folder. Launch REAPER once so it "
        "creates reaper.ini, or pass --resource-path for a portable install."
    )


def reaper_is_running() -> bool:
    """Best-effort check. A false negative only costs a warning."""
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


# --------------------------------------------------------------------------
# Inspection
# --------------------------------------------------------------------------

def report(resource_path: str) -> bool:
    """Print current state. Returns True if the config looks correct."""
    from reapy.config.config import Config, web_interface_exists

    ini = os.path.join(resource_path, "reaper.ini")
    say(f"REAPER resource path : {resource_path}")
    say(f"reaper.ini           : {'found' if os.path.isfile(ini) else 'MISSING'}")

    ok = True

    has_web = web_interface_exists(resource_path, WEB_INTERFACE_PORT)
    say(f"Web interface :{WEB_INTERFACE_PORT}  : {'yes' if has_web else 'NO - required'}")
    ok = ok and has_web

    bogus = web_interface_exists(resource_path, REAPY_SERVER_PORT)
    say(
        f"Web interface :{REAPY_SERVER_PORT}  : "
        + ("PRESENT - this blocks the reapy server, run --repair" if bogus else "absent (correct)")
    )
    ok = ok and not bogus

    cfg = Config(ini)
    reascript = cfg["reaper"].get("reascript", "0") if "reaper" in cfg else "0"
    say(f"Python ReaScript     : {'enabled' if reascript == '1' else 'DISABLED - required'}")
    ok = ok and reascript == "1"

    kb = os.path.join(resource_path, "reaper-kb.ini")
    registered = False
    if os.path.isfile(kb):
        with open(kb, encoding="utf-8", errors="ignore") as f:
            registered = "activate_reapy_server" in f.read()
    say(f"activate_reapy_server: {'registered' if registered else 'NOT registered - required'}")
    ok = ok and registered

    return ok


# --------------------------------------------------------------------------
# Actions
# --------------------------------------------------------------------------

def _guard_ini(resource_path: str):
    """Protect reaper.ini across a write, whatever goes wrong inside reapy.

    The version gate above catches the failure we know about. This catches the
    ones we do not: reaper.ini holds every preference, audio device setting and
    path a user has, and reapy rewrites it in place. An exception partway
    through leaves a truncated or empty file, and nothing in the resulting error
    mentions reaper.ini, so the loss is discovered much later.

    Returns a callable that restores the file if it ended up smaller than it
    started - the signature of a partial write - or if the caller reports a
    failure.
    """
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
                    say(f"reaper.ini shrank from {size_before} to "
                        f"{os.path.getsize(ini)} bytes - restored from backup.")
                else:
                    say("Restored reaper.ini after a failed write.")
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
            f"Python {v[0]}.{v[1]} cannot configure REAPER safely.\n\n"
            "reapy 0.10.0 crashes partway through rewriting reaper.ini on Python "
            "3.13 and newer, because configparser gained unnamed sections in 3.13 "
            "and reapy does not expect them. The crash leaves reaper.ini EMPTY, "
            "taking every REAPER preference with it.\n\n"
            "Run this with Python 3.12 or older:\n"
            f"    py -3.12 \"{os.path.abspath(__file__)}\"\n\n"
            "Only this configuration step is affected. The MCP server itself runs "
            "fine on newer versions, so nothing else needs downgrading."
        )

    from reapy.config import configure_reaper

    say("Configuring REAPER for reapy...")
    restore = _guard_ini(resource_path)
    try:
        configure_reaper(resource_path=resource_path)
    except Exception:
        restore(ok=False)
        raise
    restore(ok=True)
    say("Done. All four steps applied (they are idempotent, so re-running is safe).")


def _prune_orphan_csurfs(resource_path: str) -> int:
    """Drop csurf_N keys with N >= csurf_cnt.

    reapy's delete_web_interface shifts the surviving entries down but never
    removes the now-duplicated tail key, so deleting csurf_0 out of two leaves
    csurf_0 and csurf_1 both holding the survivor with csurf_cnt=1. REAPER only
    reads up to csurf_cnt, so this is cosmetic rather than dangerous - but a
    stale duplicate in a config file is exactly the sort of thing that wastes an
    hour later.
    """
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
        say(f"Removing the stray web interface on port {REAPY_SERVER_PORT}...")
        delete_web_interface(resource_path, REAPY_SERVER_PORT)
        say(f"Removed. Port {REAPY_SERVER_PORT} is now free for the reapy server.")
    else:
        say(f"No web interface on port {REAPY_SERVER_PORT}. Nothing to repair.")

    pruned = _prune_orphan_csurfs(resource_path)
    if pruned:
        say(f"Cleaned up {pruned} leftover csurf entr{'y' if pruned == 1 else 'ies'}.")


# --------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Enable the reapy distant API in REAPER.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--resource-path", default="",
                        help="REAPER resource folder (for portable installs)")
    parser.add_argument("--check", action="store_true",
                        help="Report current state and exit without changing anything")
    parser.add_argument("--repair", action="store_true",
                        help="Remove a stray web interface on port 2306")
    parser.add_argument("--force", action="store_true",
                        help="Proceed even if REAPER appears to be running")

    # Inside REAPER there is no command line, so take the default path.
    args = parser.parse_args([] if INSIDE else (argv if argv is not None else sys.argv[1:]))

    try:
        import reapy  # noqa: F401
    except Exception as e:
        # Do not name a supported Python range here. reapy has broken and been
        # repaired across releases, so a hard-coded range goes stale and sends
        # people to reinstall a Python that was fine. Point at the environment
        # the plugin builds and tests instead.
        v = f"{sys.version_info.major}.{sys.version_info.minor}"
        fail(
            f"Could not import reapy under Python {v}: {e}\n"
            "Build the plugin's environment, then run this with the interpreter it "
            "creates:\n"
            "    python scripts/bootstrap.py\n"
            "The installer does this for you, and runs this script with the right "
            "interpreter afterwards."
        )

    resource_path = resolve_resource_path(args.resource_path)

    if args.check:
        say("")
        ok = report(resource_path)
        say("")
        say("Configuration looks correct." if ok else "Configuration is incomplete - run without --check to fix it.")
        return 0 if ok else 1

    # Editing reaper.ini while REAPER runs is pointless: REAPER holds its own
    # copy in memory and writes it back on exit, discarding whatever we wrote.
    if not INSIDE and not args.force and reaper_is_running():
        fail(
            "REAPER appears to be running. It rewrites reaper.ini on exit and "
            "would discard these changes.\nClose REAPER and re-run, or pass "
            "--force if you are certain, or run this script from inside REAPER."
        )

    if args.repair:
        do_repair(resource_path)
    else:
        do_repair(resource_path)   # clear the bad port first
        do_configure(resource_path)

    say("")
    report(resource_path)
    say("")
    say("Restart REAPER for the changes to take effect.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:
        say(f"Unexpected error: {e}")
        sys.exit(1)
