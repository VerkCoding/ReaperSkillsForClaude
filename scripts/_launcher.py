#!/usr/bin/env python3
"""Entry point for the `reaper` MCP server.

This script determines the appropriate Python interpreter for running the MCP server and launches it.
The default python interpreter may lack the required dependencies.
This script performs the following operations:
1. Identifies an interpreter that can import the required dependencies.
2. Re-launches the server using the identified interpreter.
3. Provides a diagnostic fallback server if no suitable interpreter is found.

Diagnostics are written to stderr. Standard output is reserved for the MCP stdio transport.
"""

# Lazy annotations support parsing under Python versions prior to 3.10 where `Path | None` raises a TypeError.
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Required dependencies for module import.
# Analysis libraries (soundfile, librosa, pyloudnorm) are excluded to prevent server failure due to specific missing modules.
# `mcp.server.fastmcp` is specified to ensure compatibility with mcp 2.0+ which dropped the `fastmcp` submodule.
REQUIRED = ("mcp.server.fastmcp", "reapy", "numpy")

RELAUNCH_FLAG = "REAPER_MCP_RELAUNCHED"
PROBE_TIMEOUT = 30


def log(msg: str) -> None:
    print(f"[reaper-mcp] {msg}", file=sys.stderr, flush=True)


def plugin_root() -> Path:
    """Locate the plugin directory.

    Uses CLAUDE_PLUGIN_ROOT or REAPER_MCP_PLUGIN_ROOT if defined in the environment.
    Defaults to the grandparent directory of this script to support direct execution.
    """
    env = os.environ.get("REAPER_MCP_PLUGIN_ROOT") or os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env and Path(env).is_dir():
        return Path(env).resolve()
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """Determine the directory path for the managed virtual environment.

    This definition ensures consistency across the installer and launcher components.
    A fixed path is used rather than CLAUDE_PLUGIN_DATA to ensure the virtual environment remains accessible across updates and independent execution contexts.
    """
    env = os.environ.get("REAPER_MCP_DATA_DIR")
    if env and not env.startswith("${"):
        return Path(env)
    return Path.home() / ".reaper-for-claude"


def venv_python() -> Path | None:
    exe = data_dir() / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return exe if exe.is_file() else None


def child_env(root: Path, *, relaunched: bool = False) -> dict:
    """Construct the environment variables for the server process.

    PYTHONPATH is modified to include the bundled source directory to ensure the `reaper_mcp` package is prioritized over external installations.
    """
    env = dict(os.environ)
    src = str(root / "src")
    existing = env.get("PYTHONPATH", "")
    if src not in existing.split(os.pathsep):
        env["PYTHONPATH"] = src + (os.pathsep + existing if existing else "")
    env["REAPER_MCP_PLUGIN_ROOT"] = str(root)
    if relaunched:
        env[RELAUNCH_FLAG] = "1"
    return env


def candidates(root: Path) -> list:
    """Generate a list of candidate Python interpreters.

    The prioritization order is: explicit override, managed virtual environment, current interpreter, system alternatives.
    """
    out = []

    override = os.environ.get("REAPER_MCP_PYTHON")
    if override:
        out.append(override)

    venv = venv_python()
    if venv:
        out.append(venv)

    out.append(sys.executable)

    if os.name == "nt":
        # The `py` launcher is utilized on Windows to access non-default interpreters.
        for v in ("3.12", "3.11", "3.13", "3.10"):
            out.append(f"py -{v}")
        out.append("python")
    else:
        for v in ("3.12", "3.11", "3.13", "3.10"):
            out.append(f"python{v}")
        out.append("python3")

    seen, unique = set(), []
    for c in out:
        key = str(c)
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique


def as_argv(candidate) -> list:
    """Format the candidate interpreter as a command argument list."""
    text = str(candidate)
    return text.split(" ") if text.startswith("py -") else [text]


def probe(candidate, root: Path) -> str:
    """Verify if the candidate interpreter can import the required dependencies.

    Returns "ok", "failed", or "timeout".
    Distinguishes between a missing module (returns "failed" rapidly) and a slow file system cache (returns "timeout").
    """
    argv = as_argv(candidate)
    code = "import " + ", ".join(REQUIRED)
    try:
        proc = subprocess.run(
            argv + ["-c", code],
            env=child_env(root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=PROBE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return "timeout"
    except (OSError, subprocess.SubprocessError):
        return "failed"
    return "ok" if proc.returncode == 0 else "failed"


def resolve(root: Path) -> list | None:
    """Determine the final interpreter to execute the server.

    The managed virtual environment is retained even if the import check times out, as it was previously verified during installation.
    Other candidates are discarded if they time out.
    """
    venv = venv_python()
    for candidate in candidates(root):
        status = probe(candidate, root)
        if status == "ok":
            return as_argv(candidate)
        if status == "timeout":
            argv = as_argv(candidate)
            log(f"Import check for {' '.join(argv)} timed out after {PROBE_TIMEOUT}s.")
            if venv is not None and str(candidate) == str(venv):
                log("Proceeding with the managed environment despite timeout.")
                return argv
    return None


def run_server_here(root: Path) -> int:
    """Initialize the server from the current process."""
    sys.path.insert(0, str(root / "src"))
    os.environ.setdefault("REAPER_MCP_PLUGIN_ROOT", str(root))
    from reaper_mcp.__main__ import main  # noqa: PLC0415

    main()
    return 0


def run_fallback(root: Path, detail: str) -> int:
    """Initialize a diagnostic server to report startup failures.

    This provides the client with an actionable error message rather than a generic connection failure.
    """
    # `serve` is executed within the try block because `fallback.py` imports `mcp` lazily.
    # This prevents an unhandled ModuleNotFoundError from terminating the process before the error message can be reported.
    try:
        sys.path.insert(0, str(root / "src"))
        from reaper_mcp.fallback import serve  # noqa: PLC0415

        serve(root, detail)
        return 0
    except Exception as e:
        log(f"Cannot start the diagnostic server: {e}")
        log("")
        for line in detail.splitlines():
            log(line)
        return 1


def self_test(root: Path) -> int:
    """Evaluate server startup viability without initializing the server.

    This performs a direct check using the launcher logic to ensure accuracy.
    Outputs results to stdout.
    """
    argv = resolve(root)
    if argv:
        print(" ".join(argv))
        return 0
    print(
        "no interpreter can import " + ", ".join(REQUIRED),
        file=sys.stderr,
    )
    return 1


def main() -> int:
    root = plugin_root()

    if "--self-test" in sys.argv[1:]:
        return self_test(root)

    if os.environ.get(RELAUNCH_FLAG):
        # Avoid recursive searching if this process is the re-launched child.
        try:
            return run_server_here(root)
        except Exception as e:
            return run_fallback(root, f"Imports failed under {sys.executable}: {e}")

    argv = resolve(root)
    if argv:
        same = len(argv) == 1 and Path(argv[0]).resolve() == Path(sys.executable).resolve()
        if same:
            return run_server_here(root)

        log(f"Using interpreter: {' '.join(argv)}")
        proc = subprocess.run(
            argv + [str(Path(__file__).resolve())],
            env=child_env(root, relaunched=True),
        )
        return proc.returncode

    detail = (
        "No Python interpreter can import the required dependencies (" + ", ".join(REQUIRED) + ").\n\n"
        "Execute the following command to create the managed environment:\n"
        f"    python \"{root / 'scripts' / 'bootstrap.py'}\"\n\n"
        "Restart the client after completion.\n\n"
        "To use a specific interpreter, set the REAPER_MCP_PYTHON environment variable to its full path."
    )
    log(detail.replace("\n", " "))
    return run_fallback(root, detail)


if __name__ == "__main__":
    sys.exit(main())
