#!/usr/bin/env python3
"""Entry point for the `reaper` MCP server, bundled with the plugin.

`config/mcp.json` runs this with whatever `python` happens to be on PATH. That
interpreter is frequently the wrong one: `python-reapy` ships C-level and
stdlib-sensitive code that has broken on several Python releases, and a user's
default Python is whatever they installed last. Pointing the MCP config
directly at `python -m reaper_mcp` therefore fails on a large fraction of
machines with an ImportError the user never sees, because a stdio MCP server
that dies during startup surfaces only as "server failed to connect".

So this script does three things before the server ever starts:

  1. Picks an interpreter that can actually import the dependencies. It probes
     candidates rather than comparing version numbers - reapy's compatibility
     has moved with its releases, and a version gate hard-codes a claim that
     goes stale. What matters is whether `import reapy` works, so that is what
     gets tested.
  2. Re-launches itself under that interpreter if it is not the current one.
  3. Falls back to a diagnostic server when nothing suitable exists, so Claude
     gets a tool that explains the problem instead of a dead connection.

Everything diagnostic goes to stderr. stdout belongs to the MCP stdio
transport, and a single stray print corrupts the protocol stream.
"""

# Annotations stay lazy so this file still parses under an old interpreter.
# The whole point of the script is to run before we know which Python we got,
# and `Path | None` is a TypeError below 3.10.
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Imports the server needs at module-import time. soundfile, librosa and
# pyloudnorm are deliberately absent: the tool modules import them lazily inside
# the functions that use them, so a missing analysis library costs you three
# tools rather than the whole server.
#
# `mcp.server.fastmcp` rather than `mcp`, because the distinction is load
# bearing: mcp 2.0 dropped that submodule, so a bare `import mcp` succeeds on a
# version the server cannot actually run on - and this probe would hand back an
# interpreter that fails a moment later, past the point where anything can
# report why.
REQUIRED = ("mcp.server.fastmcp", "reapy", "numpy")

RELAUNCH_FLAG = "REAPER_MCP_RELAUNCHED"
PROBE_TIMEOUT = 30


def log(msg: str) -> None:
    print(f"[reaper-mcp] {msg}", file=sys.stderr, flush=True)


def plugin_root() -> Path:
    """Locate the plugin directory.

    Claude Code and Claude Desktop both export CLAUDE_PLUGIN_ROOT into the MCP
    subprocess, and config/mcp.json forwards it. Falling back to this file's
    grandparent keeps the script runnable by hand for debugging.
    """
    env = os.environ.get("REAPER_MCP_PLUGIN_ROOT") or os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env and Path(env).is_dir():
        return Path(env).resolve()
    return Path(__file__).resolve().parent.parent


def data_dir(root: Path) -> Path:
    """Where the managed virtualenv lives. Must agree with bootstrap.py.

    Deliberately a fixed path rather than CLAUDE_PLUGIN_DATA. The host only sets
    that when it launches the server, so a venv built by the installer - run
    from a terminal, with no such variable - would land elsewhere and never be
    found. Its value also encodes the install route, so a marketplace copy and a
    developer link would each need their own multi-minute dependency install.

    Being outside the plugin directory is what matters for surviving updates,
    and this is.
    """
    env = os.environ.get("REAPER_MCP_DATA_DIR")
    if env and not env.startswith("${"):
        return Path(env)
    return Path.home() / ".reaper-for-claude"


def venv_python(root: Path) -> Path | None:
    d = data_dir(root) / "venv"
    exe = d / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return exe if exe.is_file() else None


def child_env(root: Path, *, relaunched: bool = False) -> dict:
    """Environment for the server process.

    PYTHONPATH points at the bundled source. Nothing is installed from PyPI, so
    this is the only way the `reaper_mcp` package becomes importable, and it
    also means the bundled copy wins over any stale `reaper_mcp` that a previous
    install left in site-packages.
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
    """Interpreters to try, best first.

    An explicit override wins, then the managed venv, then the interpreter we
    are already running under, then whatever the system offers. The trailing
    entries are ordered newest-known-good first; they are probed, not trusted.
    """
    out = []

    override = os.environ.get("REAPER_MCP_PYTHON")
    if override:
        out.append(override)

    venv = venv_python(root)
    if venv:
        out.append(venv)

    out.append(sys.executable)

    if os.name == "nt":
        # The `py` launcher is the only reliable way to reach a non-default
        # interpreter on Windows; bare `python3.12` is rarely on PATH there.
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
    """`py -3.12` is a command plus an argument; a path is just a path."""
    text = str(candidate)
    return text.split(" ") if text.startswith("py -") else [text]


def probe(candidate, root: Path) -> list | None:
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
    except (OSError, subprocess.SubprocessError):
        return None
    return argv if proc.returncode == 0 else None


def run_server_here(root: Path) -> int:
    """Serve from this process, once the dependencies are known to be present."""
    sys.path.insert(0, str(root / "src"))
    os.environ.setdefault("REAPER_MCP_PLUGIN_ROOT", str(root))
    from reaper_mcp.__main__ import main  # noqa: PLC0415

    main()
    return 0


def run_fallback(root: Path, detail: str) -> int:
    """Serve a single tool that explains why the real server could not start.

    Exiting instead would show the user "reaper: failed" with no cause. A
    connected server carrying one honest diagnostic tool is far more useful:
    Claude can call it, read the fix, and tell the user what to do.
    """
    try:
        sys.path.insert(0, str(root / "src"))
        from reaper_mcp.fallback import serve  # noqa: PLC0415
    except Exception as e:  # mcp itself is missing - nothing can be served
        log(f"Cannot start even the diagnostic server: {e}")
        log(detail)
        return 1
    serve(root, detail)
    return 0


def self_test(root: Path) -> int:
    """Report whether the server could start, without starting it.

    The health check needs to answer "will this work?" and every cheaper proxy
    for that - is python on PATH, is the version recent enough, does the venv
    exist - has been wrong at some point. Running the same search the launcher
    runs is the only check that cannot disagree with reality.

    Prints to stdout, unlike the rest of this file: nothing is speaking the MCP
    protocol here, and the caller wants the answer.
    """
    for candidate in candidates(root):
        argv = probe(candidate, root)
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
        # We are the re-launched child. Do not search again - if the parent
        # picked us and we still cannot import, searching would loop.
        try:
            return run_server_here(root)
        except Exception as e:
            return run_fallback(root, f"Imports failed under {sys.executable}: {e}")

    for candidate in candidates(root):
        argv = probe(candidate, root)
        if not argv:
            continue

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
        "No Python interpreter on this machine can import the REAPER MCP "
        "dependencies (" + ", ".join(REQUIRED) + ").\n\n"
        "Fix it by creating the plugin's managed environment:\n"
        f"    python \"{root / 'scripts' / 'bootstrap.py'}\"\n\n"
        "That builds a virtualenv the plugin owns and installs everything into "
        "it, leaving your system Python untouched. Restart Claude afterwards.\n\n"
        "If you keep the dependencies somewhere specific, point the plugin at "
        "that interpreter instead by setting REAPER_MCP_PYTHON to its full path."
    )
    log(detail.replace("\n", " "))
    return run_fallback(root, detail)


if __name__ == "__main__":
    sys.exit(main())
