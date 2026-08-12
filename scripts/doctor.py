#!/usr/bin/env python3
"""Health check for the REAPER for Claude plugin. Changes nothing.

Run it when something stops working, or hand the output to Claude and ask it to
read it. Every line is [ok], [warn] or [FAIL], and each failure says what to do.

This is the only implementation. `install/doctor.ps1` delegates here rather than
carrying a parallel copy in PowerShell: two health checks that can disagree
about what "working" means are worse than none, because the one you happen to
run tells you the setup is fine.

It tests whether things work rather than whether they look right. In particular
it does not judge Python by version number - python-reapy has broken and been
repaired across several releases, so a version gate encodes a claim that goes
stale and starts failing setups that are actually fine. What matters is whether
the imports succeed and the server starts, so that is what gets run.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

REQUIRED_LAYOUT = (
    ".claude-plugin/plugin.json",
    ".claude-plugin/marketplace.json",
    "scripts/launch_server.py",
    "scripts/bootstrap.py",
    "scripts/bridge.py",
    "src/reaper_mcp/server.py",
    "skills/reaper-audio-engineer/SKILL.md",
    "skills/reaper-setup/SKILL.md",
)

MANIFEST = ".claude-plugin/plugin.json"

# The submodule, not just `mcp`: mcp 2.0 dropped mcp.server.fastmcp, so a bare
# `import mcp` reports healthy on a version the server cannot run on.
CORE_IMPORTS = ("mcp.server.fastmcp", "reapy", "numpy")

# Installed by earlier versions of this project. This plugin contains everything
# they did, and left in place they load in parallel with it.
SUPERSEDED = ("reaper-mcp", "reaper-ai-engineer-skill")

# Overlaps in purpose but is not from this repository, so it is reported and
# never recommended for deletion - it may carry content that exists nowhere else.
INDEPENDENT = "audio-engineer-reaper"

_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


class Report:
    def __init__(self) -> None:
        self.fails = 0

    def ok(self, msg: str) -> None:
        print("  " + _c("32", "[ok]  ") + f" {msg}")

    def warn(self, msg: str, detail: str = "") -> None:
        print("  " + _c("33", "[warn]") + f" {msg}")
        if detail:
            print(_c("90", f"         -> {detail}"))

    def fail(self, msg: str, fix: str = "") -> None:
        print("  " + _c("31", "[FAIL]") + f" {msg}")
        if fix:
            print(_c("90", f"         -> {fix}"))
        self.fails += 1

    def info(self, msg: str) -> None:
        print(_c("90", f"         {msg}"))

    def section(self, msg: str) -> None:
        print("\n" + _c("36", f"-- {msg}"))


# ---------------------------------------------------------------------------
# Platform helpers
# ---------------------------------------------------------------------------

def reaper_resource_path(explicit: str = "") -> Path | None:
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_dir() else None
    for candidate in (
        Path(os.environ.get("APPDATA", "")) / "REAPER",
        Path.home() / "Library" / "Application Support" / "REAPER",
        Path.home() / ".config" / "REAPER",
    ):
        if (candidate / "reaper.ini").is_file():
            return candidate
    return None


def desktop_config_paths() -> list:
    """Every place Claude Desktop might keep claude_desktop_config.json."""
    out = []
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            out.append(Path(appdata) / "Claude" / "claude_desktop_config.json")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            # An MSIX (Store) install redirects writes into its own container.
            pkgs = Path(local) / "Packages"
            if pkgs.is_dir():
                for d in pkgs.glob("Claude_*"):
                    out.append(d / "LocalCache/Roaming/Claude/claude_desktop_config.json")
    elif sys.platform == "darwin":
        out.append(Path.home() / "Library/Application Support/Claude/claude_desktop_config.json")
    else:
        out.append(Path.home() / ".config/Claude/claude_desktop_config.json")
    return out


def reaper_running() -> bool:
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq reaper.exe", "/NH"],
                capture_output=True, text=True, timeout=10,
            ).stdout.lower()
            return "reaper.exe" in out
        return subprocess.run(
            ["pgrep", "-x", "REAPER"], capture_output=True, text=True, timeout=10
        ).returncode == 0
    except Exception:
        return False


def run(argv, timeout=120):
    """Capture a subprocess as text, decoding as UTF-8 rather than the locale.

    `text=True` alone decodes with the locale codec, which on Windows is cp1252.
    `claude plugin list` prints checkmarks and arrows, and those raise inside
    subprocess's reader thread - the exception surfaces far from here as a
    CompletedProcess whose stdout is silently None. REAPER project names and
    paths are non-ASCII often enough for the same trap to spring elsewhere, so
    every call goes through this.
    """
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return None


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_layout(r: Report) -> None:
    r.section("Plugin layout")
    for rel in REQUIRED_LAYOUT:
        if (ROOT / rel).exists():
            r.ok(rel)
        else:
            r.fail(f"missing: {rel}", "This install is incomplete, or the folder moved mid-install.")


def check_mcp_command(r: Report) -> None:
    """The command Claude will actually launch has to exist on this machine.

    The manifest says `python`, and if that does not resolve, the server dies
    before any of its own diagnostics can run - the host reports only "failed to
    connect", with no cause. Worth checking explicitly, because the fix is a
    one-word edit and the symptom points nowhere near it.
    """
    r.section("MCP launch command")
    cfg = ROOT / MANIFEST
    if not cfg.is_file():
        r.fail(f"{MANIFEST} is missing")
        return
    try:
        command = json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"]["reaper"]["command"]
    except Exception as e:
        r.fail(f"{MANIFEST} does not declare the reaper server: {e}")
        return

    resolved = shutil.which(command)
    if resolved:
        r.ok(f"`{command}` resolves to {resolved}")
    else:
        alt = next((c for c in ("python3", "python") if shutil.which(c)), None)
        if alt:
            r.fail(
                f"{MANIFEST} launches `{command}`, which is not on PATH",
                f'Change "command" in {cfg} to "{alt}".',
            )
        else:
            r.fail(
                f"{MANIFEST} launches `{command}`, and no Python is on PATH at all",
                "Install Python (RunThisToStart.bat option 8 explains how) and reopen your terminal.",
            )


def check_python(r: Report) -> None:
    r.section("Python")
    r.ok(f"running this check under {sys.version.split()[0]} ({sys.executable})")

    boot = ROOT / "scripts" / "bootstrap.py"
    if boot.is_file():
        p = run([sys.executable, str(boot), "--check"])
        if p:
            for line in p.stdout.splitlines():
                r.info(line)
            if p.returncode == 0:
                r.ok("managed environment is ready")
            else:
                # Not a failure by itself: the launcher accepts any interpreter
                # that can import the dependencies, and many machines already
                # have them.
                r.warn("no managed environment - falling back to whatever Python provides")

    for mod in CORE_IMPORTS:
        p = run([sys.executable, "-c", f"import {mod}"])
        if p and p.returncode == 0:
            r.ok(f"import {mod}")
        else:
            r.warn(f"import {mod} fails under {sys.executable}")

    # REAPER embeds a Python shared library and runs ReaScripts inside it. That
    # interpreter is the base installation - never the virtualenv, which is only
    # a redirect layer and contains no python3XX.dll. reapy has to be importable
    # there for activate_reapy_server to start the distant API.
    #
    # Checked separately because the failure is invisible from the server side:
    # the virtualenv can be flawless while REAPER has nothing to answer with.
    base = Path(sys.base_prefix) / ("python.exe" if os.name == "nt" else "bin/python3")
    if not base.is_file():
        base = Path(sys.executable)
    p = run([str(base), "-c", "import reapy"])
    if p and p.returncode == 0:
        r.ok(f"REAPER's interpreter can import reapy ({base})")
    else:
        r.fail(
            f"REAPER's interpreter cannot import reapy ({base})",
            f'"{base}" -m pip install --user python-reapy   '
            "(or re-run scripts/bootstrap.py, which does it)",
        )
        r.info("Without it, activate_reapy_server fails and the distant API never starts.")

    # Imported lazily by the analysis tools, so the server starts without them
    # and the shortfall shows up only when one of those tools is called - which
    # is why it is checked here rather than left to surface mid-task.
    p = run([sys.executable, "-c", "import librosa, soundfile, pyloudnorm"])
    if p and p.returncode == 0:
        r.ok("analysis libraries present (loudness, spectrum, transients)")
    else:
        r.fail(
            "analysis libraries are missing - the offline analysis tools will fail",
            f'"{sys.executable}" "{ROOT / "scripts" / "bootstrap.py"}"',
        )

    launcher = ROOT / "scripts" / "launch_server.py"
    if launcher.is_file():
        # The only check that matters. Every cheaper proxy for "will the server
        # start" - is Python present, is the version recent, does the venv exist
        # - has been wrong at some point.
        p = run([sys.executable, str(launcher), "--self-test"])
        if p and p.returncode == 0:
            r.ok(f"the MCP server starts (interpreter: {p.stdout.strip()})")
        else:
            r.fail(
                "the MCP server cannot start",
                f"{sys.executable} \"{ROOT / 'scripts' / 'bootstrap.py'}\"",
            )
            if p:
                for line in (p.stderr or "").splitlines()[:5]:
                    r.info(line)


def check_reaper(r: Report, explicit: str) -> None:
    r.section("REAPER")

    res = reaper_resource_path(explicit)
    if res is None:
        r.fail(
            "could not find REAPER's resource folder",
            "Launch REAPER once so it creates reaper.ini, or pass --resource-path for a portable install.",
        )
        return
    r.ok(f"reaper.ini at {res}")

    scripts = res / "Scripts"
    if (scripts / "claude_bridge.lua").is_file():
        r.ok("claude_bridge.lua installed")
    else:
        r.fail(f"claude_bridge.lua missing from {scripts}", "Re-run the installer.")

    startup = scripts / "__startup.lua"
    if not startup.is_file():
        r.fail("__startup.lua missing", "Re-run the installer - REAPER needs it to auto-start the bridge.")
    elif "claude_bridge" in startup.read_text(encoding="utf-8", errors="ignore"):
        r.ok("__startup.lua loads the bridge")
    else:
        r.fail("__startup.lua does not reference claude_bridge", "Re-run the installer.")

    # Distant API - delegate to the script that owns that knowledge.
    enable = ROOT / "reaper" / "enable_reapy.py"
    if not enable.is_file():
        enable = scripts / "enable_reapy.py"
    if enable.is_file():
        p = run([sys.executable, str(enable), "--resource-path", str(res), "--check"])
        if p:
            for line in (p.stdout or "").splitlines():
                if line.strip():
                    r.info(line)
            if p.returncode == 0:
                r.ok("distant API configured")
            else:
                r.fail(
                    "distant API not fully configured",
                    f"Close REAPER, then: {sys.executable} \"{enable}\"",
                )

    # Configuring REAPER needs an interpreter reapy can drive safely, which is a
    # stricter requirement than running the server. Worth reporting even when
    # the distant API is already set up, because without one it cannot be
    # repaired later - and attempting it anyway empties reaper.ini.
    safe = None
    for cand in (["py", "-3.12"], ["py", "-3.11"], ["py", "-3.10"], ["python"]):
        if not shutil.which(cand[0]):
            continue
        p = run(cand + ["-c", "import sys; sys.exit(0 if sys.version_info[:2] <= (3,12) else 1)"])
        if not p or p.returncode != 0:
            continue
        p = run(cand + ["-c", "import reapy"])
        if p and p.returncode == 0:
            safe = " ".join(cand)
            break
    if safe:
        r.ok(f"REAPER can be (re)configured with `{safe}`")
    else:
        r.warn(
            "no Python 3.12-or-older interpreter with reapy - REAPER cannot be reconfigured",
            "reapy 0.10.0 empties reaper.ini under Python 3.13+, so the installer refuses. "
            "Fix: winget install -e --id Python.Python.3.12 "
            "then py -3.12 -m pip install python-reapy",
        )

    bridge = res / "claude_bridge"
    status = bridge / "status.txt"
    if not bridge.is_dir():
        r.warn("bridge directory not created yet - start REAPER once after installing.")
    elif not status.is_file():
        r.warn("no status.txt - the listener has not run since installation.")
    else:
        age = time.time() - status.stat().st_mtime
        if age < 30:
            r.ok(f"bridge listener is alive (heartbeat {int(age)}s ago)")
        else:
            r.warn(f"bridge heartbeat is {int(age / 60)} min old - REAPER is probably closed.")

    # An unconsumed command is a loaded gun: REAPER runs whatever sits here the
    # next time it starts, which could be a render firing days later.
    pending = bridge / "cmd.lua"
    if pending.is_file():
        r.warn(
            "an unconsumed cmd.lua is waiting in the bridge directory",
            f"REAPER will run it at next launch. Delete it if that is not intended: {pending}",
        )


def check_live_link(r: Report) -> None:
    """Prove both ends of the MCP path are actually on, not merely configured.

    Everything above verifies settings. This connects: REAPER's distant API must
    be listening AND the server's side must be able to reach it. A setup can be
    configured perfectly and still fail here - the classic case being a web
    interface squatting on reapy's own port.
    """
    r.section("Live connection")

    if not reaper_running():
        r.warn("REAPER is not running - start it, then re-run to test the connection.")
        return
    r.ok("REAPER is running")

    probe = (
        "import sys, json;"
        f"sys.path.insert(0, r'{ROOT / 'src'}');"
        "import warnings; warnings.filterwarnings('ignore');"
        "from reaper_mcp.connection import connection_status;"
        "print(json.dumps(connection_status()))"
    )
    p = run([sys.executable, "-c", probe], timeout=60)
    if not p or p.returncode != 0:
        r.fail(
            "could not run the connection probe",
            "The dependencies are not importable under this interpreter.",
        )
        return

    line = next((l for l in p.stdout.splitlines() if l.startswith("{")), "")
    try:
        status = json.loads(line)
    except Exception:
        r.fail("the connection probe returned nothing usable")
        return

    if status.get("connected"):
        r.ok(
            f"connected to REAPER - project '{status.get('project_name')}', "
            f"{status.get('track_count')} track(s)"
        )
    else:
        r.fail(
            "REAPER is running but the MCP server cannot reach it",
            "Close REAPER and run: reaper/enable_reapy.py --repair, then reaper/enable_reapy.py",
        )
        for chunk in str(status.get("error", "")).splitlines()[:4]:
            r.info(chunk)


def check_claude(r: Report) -> None:
    r.section("Claude")

    link = Path.home() / ".claude" / "skills" / "reaper-for-claude"

    claude = shutil.which("claude")
    if not claude:
        r.warn("the claude CLI is not on PATH (fine if you only use Claude Desktop).")
        if link.exists():
            r.ok(f"developer link present: {link}")
    else:
        p = run([claude, "plugin", "list"])
        listing = ((p.stdout or "") + (p.stderr or "")) if p else ""
        from_market = "reaper-for-claude@reaper-skills-for-claude" in listing
        from_dir = "reaper-for-claude@skills-dir" in listing

        if from_market and from_dir:
            # They do not both load. Claude Code prefers the marketplace and
            # skips the link silently. Uninstalling is not enough either: the
            # marketplace ENTRY reserves the name whether or not anything is
            # installed from it.
            r.fail(
                "the developer link is being ignored - the marketplace reserves the name",
                "claude plugin uninstall reaper-for-claude@reaper-skills-for-claude"
                "  then  claude plugin marketplace remove reaper-skills-for-claude",
            )
        elif from_market:
            r.ok("Claude Code: installed from the marketplace")
            r.warn(
                "edits to this folder do not reach Claude - the install is a copy in the plugin cache",
                "While developing, use the developer link instead.",
            )
        elif from_dir:
            r.ok("Claude Code: loaded in place via the developer link (edits are live)")
        else:
            r.fail(
                "Claude Code does not list reaper-for-claude",
                f'claude plugin marketplace add "{ROOT}"'
                "  then  claude plugin install reaper-for-claude@reaper-skills-for-claude",
            )

    found_desktop = False
    for cfg in desktop_config_paths():
        if not cfg.is_file():
            continue
        found_desktop = True
        try:
            data = json.loads(cfg.read_text(encoding="utf-8") or "{}")
        except Exception:
            r.fail(f"{cfg} is not valid JSON", "Restore it from the .bak alongside it.")
            continue
        entry = (data.get("mcpServers") or {}).get("reaper")
        if not entry:
            r.warn(f"no 'reaper' MCP server in {cfg.name} (fine if you use the Plugins UI instead).")
            continue
        target = next((a for a in entry.get("args", []) if str(a).endswith("launch_server.py")), None)
        if target and Path(target).is_file():
            r.ok(f"Claude Desktop -> {target}")
        elif entry.get("env", {}).get("PYTHONPATH"):
            r.fail("Claude Desktop still uses the old PYTHONPATH entry", "Re-run the installer.")
        else:
            r.fail("Claude Desktop's reaper entry points at something missing", "Re-run the installer.")
    if not found_desktop:
        r.warn("no Claude Desktop config found (fine if you only use Claude Code).")

    skills = Path.home() / ".claude" / "skills"
    superseded = [skills / d for d in SUPERSEDED if (skills / d).exists()]
    if superseded:
        r.warn("superseded copies from an earlier version are still loading:")
        for p in superseded:
            r.info(str(p))
        r.info("-> This plugin replaces them. Delete them, or their tools load in parallel with ours.")

    if (skills / INDEPENDENT).exists():
        r.warn(
            f"a separate REAPER skill is also installed: {skills / INDEPENDENT}",
            "It overlaps with reaper-audio-engineer but is not from this repository. Keep or remove as you prefer.",
        )


def main() -> int:
    ap = argparse.ArgumentParser(description="Health check for the REAPER for Claude plugin.")
    ap.add_argument("--resource-path", default="", help="REAPER resource folder (portable installs).")
    ap.add_argument("--skip-live", action="store_true", help="Skip the REAPER connection probe.")
    args = ap.parse_args()

    print()
    print(_c("36", "==============================================="))
    print(_c("36", "  REAPER for Claude - health check"))
    print(_c("36", "==============================================="))
    print(_c("90", f"  Plugin: {ROOT}"))

    r = Report()
    check_layout(r)
    check_mcp_command(r)
    check_python(r)
    check_reaper(r, args.resource_path)
    if not args.skip_live:
        check_live_link(r)
    check_claude(r)

    print()
    print(_c("36", "==============================================="))
    if r.fails == 0:
        print(_c("32", "  All checks passed"))
    else:
        print(_c("33", f"  {r.fails} check(s) failed - see the -> lines above"))
    print(_c("36", "==============================================="))
    print()
    return r.fails


if __name__ == "__main__":
    sys.exit(main())
