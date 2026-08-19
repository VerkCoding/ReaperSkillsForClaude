#!/usr/bin/env python3
"""Diagnostic script for the REAPER MCP plugin. Performs read-only checks on environment and configuration.

The PowerShell script `install/health-check.ps1` delegates to this script to maintain a single source of truth for diagnostic logic.

Validates operational status by executing dependencies and server initialization, rather than version string comparison.
Configuring REAPER is restricted to Python <= 3.12 due to a known defect in reapy 0.10 causing reaper.ini truncation on Python 3.13+.
"""

from __future__ import annotations

import argparse
import json
import os
import re
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
    "scripts/_launcher.py",
    "scripts/bootstrap.py",
    "scripts/bridge.py",
    "src/reaper_mcp/server.py",
    "skills/reaper-audio-engineer/SKILL.md",
    "skills/reaper-mcp/SKILL.md",
    "skills/reaper-core-setup/SKILL.md",
)

MANIFEST = ".claude-plugin/plugin.json"

# Evaluates health against the exact dependency list used by the launcher to ensure consistent validation.
from _launcher import REQUIRED as CORE_IMPORTS  # noqa: E402

# Legacy packages that duplicate current functionality and cause parallel loading if not removed.
SUPERSEDED = ("reaper-mcp", "reaper-ai-engineer-skill")

# External package with overlapping scope. Flagged for awareness but preserved to avoid unintended data loss.
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


# Check 64-bit configuration keys prior to 32-bit due to prevalence in modern environments.
_PY_LIB_KEYS = ("pythonlibpath64", "pythonlibpath")


def reaper_embedded_python(explicit: str = "") -> tuple:
    """Resolves the Python interpreter used by REAPER for ReaScripts.

    Fallback sequence to prevent false positives when virtualenv base diverges from REAPER's embedded version:
      1. reaper.ini configuration values, representing active state.
      2. Installer selection via bootstrap.py, for pre-configuration states.
      3. sys.base_prefix, as fallback.
    """
    res = reaper_resource_path(explicit)
    if res:
        try:
            text = (res / "reaper.ini").read_text(encoding="utf-8", errors="ignore")
        except OSError:
            text = ""
        for key in _PY_LIB_KEYS:
            m = re.search(rf"(?mi)^{key}=(.+?)\s*$", text)
            # Empty values bypass Path resolution to prevent unintended resolution against the current working directory.
            if not m or not m.group(1).strip():
                continue
            lib = Path(m.group(1).strip())
            exe = lib / ("python.exe" if os.name == "nt" else "bin/python3")
            if exe.is_file():
                return exe, "reaper.ini"

    boot = ROOT / "scripts" / "bootstrap.py"
    if boot.is_file():
        p = run([sys.executable, str(boot), "--print-reaper-python"])
        if p and p.returncode == 0:
            candidate = Path((p.stdout or "").strip())
            if str(candidate).strip() and candidate.is_file():
                return candidate, "bootstrap.py"

    base = Path(sys.base_prefix) / ("python.exe" if os.name == "nt" else "bin/python3")
    if not base.is_file():
        base = Path(sys.executable)
    return base, "sys.base_prefix"


def desktop_config_paths() -> list:
    """Retrieves potential paths for claude_desktop_config.json across supported operating systems."""
    out = []
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            out.append(Path(appdata) / "Claude" / "claude_desktop_config.json")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            # MSIX installations redirect configuration data into application-specific containers.
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
    """Executes subprocesses with strict UTF-8 decoding.

    Prevents silent failures in subprocess reader threads caused by locale codec mismatch (e.g., cp1252 on Windows) when processing non-ASCII characters from CLI output or file paths.
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
            r.fail(f"missing: {rel}", "Installation incomplete or directory relocated.")


def check_mcp_command(r: Report) -> None:
    """Validates the existence of the MCP launch command specified in the manifest.

    Prevents silent connection failures caused by unresolvable commands prior to server diagnostic initialization.
    """
    r.section("MCP launch command")
    cfg = ROOT / MANIFEST
    if not cfg.is_file():
        r.fail(f"{MANIFEST} missing")
        return
    try:
        command = json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"]["reaper"]["command"]
    except Exception as e:
        r.fail(f"{MANIFEST} missing reaper server declaration: {e}")
        return

    resolved = shutil.which(command)
    if resolved:
        # Validates syntax compatibility against Python 3.8+ to prevent early parsing failures on legacy interpreters.
        p = run([resolved, "-c", "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)"])
        if p and p.returncode == 0:
            v = run([resolved, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"])
            ver = (v.stdout or "").strip() if v else "?"
            r.ok(f"`{command}` resolved to {resolved} (Python {ver})")
        else:
            r.fail(
                f"`{command}` resolved to {resolved}. Version unsupported.",
                "Configure MCP entry to use Python 3.8+.",
            )
    else:
        alt = next((c for c in ("python3", "python") if shutil.which(c)), None)
        if alt:
            r.fail(
                f"{MANIFEST} command `{command}` not found on PATH.",
                f"Change command in {cfg} to {alt}.",
            )
        else:
            r.fail(
                f"{MANIFEST} command `{command}` not found. Python absent from PATH.",
                "Install Python.",
            )


def check_python(r: Report, explicit: str = "") -> None:
    r.section("Python")
    r.ok(f"Environment: {sys.version.split()[0]} ({sys.executable})")

    boot = ROOT / "scripts" / "bootstrap.py"
    if boot.is_file():
        p = run([sys.executable, str(boot), "--check"])
        if p:
            for line in p.stdout.splitlines():
                r.info(line)
            if p.returncode == 0:
                r.ok("Managed environment ready.")
            else:
                # Launcher accepts environments possessing required dependencies independently of managed states.
                r.warn("Managed environment absent. Falling back to default Python.")

    for mod in CORE_IMPORTS:
        p = run([sys.executable, "-c", f"import {mod}"])
        if p and p.returncode == 0:
            r.ok(f"Module {mod} imported.")
        else:
            r.warn(f"Module {mod} import failed.")

    # Validates reapy availability in the base interpreter.
    # REAPER embeds the base Python shared library directly, rendering virtualenv dependency verification insufficient for distant API initialization.
    base, source = reaper_embedded_python(explicit)
    p = run([str(base), "-c", "import reapy"])
    if p and p.returncode == 0:
        r.ok(f"REAPER interpreter imported reapy.")
    else:
        r.fail(
            f"REAPER interpreter failed to import reapy.",
            f"Execute: \"{base}\" -m pip install --user python-reapy or run scripts/bootstrap.py",
        )
        r.info("activate_reapy_server initialization failure.")
    if source != "reaper.ini":
        # Indicates source of inference when non-deterministic methods are utilized.
        r.info(f"Source: {source}")

    # Validates availability of lazy-loaded analysis dependencies to prevent runtime tool errors.
    # Checks soxr explicitly to expose underlying compiled extension failures obscured by librosa's lazy_loader.
    p = run([sys.executable, "-c", "import librosa, soundfile, pyloudnorm, soxr"])
    if p and p.returncode == 0:
        r.ok("Analysis libraries present.")
    elif p and "soxr" in (p.stderr or "") and "DLL load failed" in (p.stderr or ""):
        # Indicates missing Visual C++ redistributable requirements.
        r.fail(
            "soxr failed to load due to missing Visual C++ runtime.",
            "Install Visual C++ runtime.",
        )
        r.info("Visual C++ runtime required.")
    else:
        r.fail(
            "Analysis libraries missing.",
            f"Execute: \"{sys.executable}\" \"{ROOT / 'scripts' / 'bootstrap.py'}\"",
        )

    launcher = ROOT / "scripts" / "launch_server.py"
    if launcher.is_file():
        # Verifies server initialization directly to avoid false positives from proxy validation methods.
        p = run([sys.executable, str(launcher), "--self-test"])
        if p and p.returncode == 0:
            r.ok(f"MCP server started (interpreter: {p.stdout.strip()})")
        else:
            r.fail(
                "MCP server failed to start.",
                f"Execute: {sys.executable} \"{ROOT / 'scripts' / 'bootstrap.py'}\"",
            )
            if p:
                for line in (p.stderr or "").splitlines()[:5]:
                    r.info(line)


def check_reaper(r: Report, explicit: str) -> None:
    r.section("REAPER")

    res = reaper_resource_path(explicit)
    if res is None:
        r.fail(
            "REAPER resource directory not found.",
            "Run REAPER to generate configuration, or specify --resource-path.",
        )
        return
    r.ok(f"reaper.ini located at {res}")

    scripts = res / "Scripts"
    if (scripts / "claude_bridge.lua").is_file():
        r.ok("claude_bridge.lua present.")
    else:
        r.fail(f"claude_bridge.lua not found.", "Execute installer.")

    startup = scripts / "__startup.lua"
    if not startup.is_file():
        r.fail("__startup.lua not found.", "Execute installer.")
    elif "claude_bridge" in startup.read_text(encoding="utf-8", errors="ignore"):
        r.ok("__startup.lua configuration valid.")
    else:
        r.fail("claude_bridge reference missing in __startup.lua.", "Execute installer.")

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
                r.ok("Distant API configured.")
            else:
                r.fail(
                    "Distant API configuration invalid.",
                    f"Terminate REAPER, execute: {sys.executable} \"{enable}\"",
                )

    # Identifies interpreters safe for reapy execution to prevent configuration corruption during potential repairs.
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
        r.ok(f"REAPER configuration interpreter verified: `{safe}`")
    else:
        r.warn(
            "Compatible Python <= 3.12 interpreter with reapy not found.",
            "reapy 0.10.0 incompatible with Python >= 3.13.",
        )

    bridge = res / "claude_bridge"
    status = bridge / "status.txt"
    if not bridge.is_dir():
        r.warn("Bridge directory absent.")
    elif not status.is_file():
        r.warn("status.txt absent.")
    else:
        age = time.time() - status.stat().st_mtime
        if age < 30:
            r.ok(f"Bridge listener active.")
        else:
            r.warn(f"Bridge heartbeat expired.")

    # Identifies stale commands that will execute automatically upon next application launch.
    pending = bridge / "cmd.lua"
    if pending.is_file():
        r.warn(
            "Unconsumed cmd.lua present in bridge directory.",
            f"Pending execution on next application launch. File: {pending}",
        )


def check_live_link(r: Report) -> None:
    """Validates end-to-end MCP connectivity.

    Confirms REAPER's distant API is listening and accessible to the server, verifying absence of port conflicts.
    """
    r.section("Live connection")

    if not reaper_running():
        r.warn("REAPER process not detected.")
        return
    r.ok("REAPER process detected.")

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
            "Connection probe execution failed.",
            "Dependencies unavailable.",
        )
        return

    line = next((l for l in p.stdout.splitlines() if l.startswith("{")), "")
    try:
        status = json.loads(line)
    except Exception:
        r.fail("Connection probe invalid response.")
        return

    if status.get("connected"):
        r.ok(
            f"Connected to REAPER."
        )
    else:
        r.fail(
            "REAPER process detected. MCP connection failed.",
            "Terminate REAPER, execute repairs.",
        )
        for chunk in str(status.get("error", "")).splitlines()[:4]:
            r.info(chunk)


def check_claude(r: Report) -> None:
    r.section("Claude")

    link = Path.home() / ".claude" / "skills" / "reaper-for-claude"

    claude = shutil.which("claude")
    if not claude:
        r.warn("claude CLI not found on PATH.")
        if link.exists():
            r.ok(f"Developer link present: {link}")
    else:
        p = run([claude, "plugin", "list"])
        listing = ((p.stdout or "") + (p.stderr or "")) if p else ""
        from_market = "reaper-for-claude@reaper-skills-for-claude" in listing
        from_dir = "reaper-for-claude@skills-dir" in listing

        if from_market and from_dir:
            # Warns on parallel installations. Marketplace copy takes precedence during load sequence.
            r.warn(
                "Multiple installation routes detected.",
                "Resolve duplicate installations.",
            )
        elif from_market:
            r.ok("Marketplace installation active.")
            r.warn(
                "Local modifications bypass Claude due to cache copy.",
                "Use developer link.",
            )
        elif from_dir:
            r.ok("Developer link active.")
        else:
            r.fail(
                "reaper-for-claude absent in Claude Code.",
                "Install via marketplace or configure developer link.",
            )

    found_desktop = False
    for cfg in desktop_config_paths():
        if not cfg.is_file():
            continue
        found_desktop = True
        try:
            data = json.loads(cfg.read_text(encoding="utf-8") or "{}")
        except Exception:
            r.fail(f"Invalid JSON configuration: {cfg}", "Restore configuration backup.")
            continue
        entry = (data.get("mcpServers") or {}).get("reaper")
        if not entry:
            r.warn(f"reaper MCP server absent in configuration: {cfg.name}")
            continue
        target = next((a for a in entry.get("args", []) if str(a).endswith("launch_server.py")), None)
        if target and Path(target).is_file():
            r.ok(f"Claude Desktop target: {target}")
        elif entry.get("env", {}).get("PYTHONPATH"):
            r.fail("Deprecated PYTHONPATH entry detected.", "Execute installer.")
        else:
            r.fail("Target missing for reaper entry.", "Execute installer.")
    if not found_desktop:
        r.warn("Claude Desktop configuration absent.")

    skills = Path.home() / ".claude" / "skills"
    superseded = [skills / d for d in SUPERSEDED if (skills / d).exists()]
    if superseded:
        r.warn("Legacy packages detected:")
        for p in superseded:
            r.info(str(p))
        r.info("Remove legacy packages.")

    if (skills / INDEPENDENT).exists():
        r.warn(
            f"External REAPER skill detected: {skills / INDEPENDENT}",
            "Scope overlap detected.",
        )


def main() -> int:
    ap = argparse.ArgumentParser(description="REAPER MCP plugin diagnostics.")
    ap.add_argument("--resource-path", default="", help="Specify REAPER resource directory.")
    ap.add_argument("--skip-live", action="store_true", help="Bypass connection probe.")
    args = ap.parse_args()

    print()
    print(_c("36", "==============================================="))
    print(_c("36", "  Diagnostics"))
    print(_c("36", "==============================================="))
    print(_c("90", f"  Path: {ROOT}"))

    r = Report()
    check_layout(r)
    check_mcp_command(r)
    check_python(r, args.resource_path)
    check_reaper(r, args.resource_path)
    if not args.skip_live:
        check_live_link(r)
    check_claude(r)

    print()
    print(_c("36", "==============================================="))
    if r.fails == 0:
        print(_c("32", "  Status: OK"))
    else:
        print(_c("33", f"  Status: {r.fails} check(s) failed"))
    print(_c("36", "==============================================="))
    print()
    return r.fails


if __name__ == "__main__":
    sys.exit(main())
