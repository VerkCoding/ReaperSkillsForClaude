#!/usr/bin/env python3
"""Exercise every MCP tool against a live REAPER instance.

This script tests the server's tools by calling them against a running REAPER instance and verifying the return values.
The script performs the following:
  * Functional verification: Tools are executed with arguments that should succeed. Where applicable, the effects are verified.
  * Performance measurement: Tool execution time is recorded. Read-only tools are called repeatedly to gather a distribution of execution times.

Calls are routed through ``mcp.call_tool`` to test argument coercion, schema validation, and JSON serialisation.

Timeout handling:
Every call executes in a separate thread and is awaited with a timeout. This handles modal dialogs that block deferred scripts in REAPER. Upon timeout, the script inspects REAPER's windows, dismisses known dismissable dialogs, and reports the contents of blocking dialogs.

State management:
Operations are performed within the currently open project on temporary tracks. The ``create_project``, ``load_project``, and ``save_project`` tools are only tested when explicitly requested.
Global project state is read before execution and restored afterward. Added tracks and files are removed in reverse order to ensure file locks are released.

USAGE
    python scripts/benchmark_tools.py
    python scripts/benchmark_tools.py --repeat 30
    python scripts/benchmark_tools.py --json out.json
    python scripts/benchmark_tools.py --timeout 60
    python scripts/benchmark_tools.py --include-destructive
    python scripts/benchmark_tools.py --list

Exit code is 0 on success, 1 on failure. Tools with unmet preconditions are marked ``~`` and do not cause failure.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import statistics
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# Prefix for temporary assets to aid identification on failure.
PREFIX = "__bench__"

# Dialog definitions for automated dismissal.
DISMISSABLE = ("Render Error",)
DO_NOT_TOUCH = ("ReaScript task control",)


# --------------------------------------------------------------------------
# Interpreter
# --------------------------------------------------------------------------

def _ensure_dependencies() -> None:
    """Re-execute using the managed virtual environment to ensure dependency availability."""
    sys.path.insert(0, str(ROOT / "src"))
    try:
        import mcp.server.fastmcp  # noqa: F401
        import reapy  # noqa: F401
        import numpy  # noqa: F401
        return
    except ImportError as e:
        # Store exception to print if environment fallback fails.
        first_error = e

    sys.path.insert(0, str(HERE))
    try:
        import _launcher  # noqa: PLC0415
        venv = _launcher.venv_python()
    except Exception:
        venv = None

    if venv is None or Path(venv).resolve() == Path(sys.executable).resolve():
        print(
            f"Cannot import the server's dependencies under {sys.executable}: "
            f"{first_error}\n"
            f"Build the environment first:\n    python {ROOT / 'scripts' / 'bootstrap.py'}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    env = dict(os.environ)
    src = str(ROOT / "src")
    if src not in env.get("PYTHONPATH", "").split(os.pathsep):
        env["PYTHONPATH"] = src + (
            os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
        )
    import subprocess  # noqa: PLC0415

    raise SystemExit(
        subprocess.call([str(venv), str(Path(__file__).resolve()), *sys.argv[1:]], env=env)
    )


# --------------------------------------------------------------------------
# REAPER's windows
#
# Only used to explain and clear a block, never to drive the application. If
# this is not Windows the functions answer "nothing", and the timeout still
# does its job - the run just cannot say what is on screen.
# --------------------------------------------------------------------------

def _win32():
    if sys.platform != "win32":
        return None
    import ctypes  # noqa: PLC0415
    import ctypes.wintypes as wt  # noqa: PLC0415

    return ctypes.windll.user32, ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM), ctypes


def open_dialogs() -> list[tuple[int, str, list[str]]]:
    """Return all active REAPER dialogs to capture error text during a freeze.
    Filtered by process ID to avoid matching unrelated windows.
    """
    api = _win32()
    if api is None:
        return []
    user32, proc_type, ctypes = api
    import ctypes.wintypes as wt  # noqa: PLC0415

    def text(h):
        buf = ctypes.create_unicode_buffer(2048)
        user32.GetWindowTextW(h, buf, 2048)
        return buf.value

    def pid_of(h):
        out = wt.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(out))
        return out.value

    # Identify REAPER main window to obtain process ID.
    reaper_pid = None

    def find_main(h, _):
        nonlocal reaper_pid
        if user32.IsWindowVisible(h) and "REAPER v" in text(h):
            reaper_pid = pid_of(h)
        return True

    user32.EnumWindows(proc_type(find_main), 0)
    if reaper_pid is None:
        return []

    found: list[tuple[int, str, list[str]]] = []

    def visit(h, _):
        if not user32.IsWindowVisible(h) or pid_of(h) != reaper_pid:
            return True
        title = text(h)
        if not title or "REAPER v" in title:
            return True
        lines: list[str] = []

        def child(kh, _l):
            t = text(kh)
            if t:
                lines.append(t)
            return True

        user32.EnumChildWindows(h, proc_type(child), 0)
        found.append((h, title, lines))
        return True

    user32.EnumWindows(proc_type(visit), 0)
    return found


def dismiss(handle: int) -> bool:
    """Dismiss a dialog by simulating a click on the OK button."""
    api = _win32()
    if api is None:
        return False
    user32, proc_type, ctypes = api
    BM_CLICK = 0x00F5
    clicked = False

    def child(kh, _l):
        nonlocal clicked
        buf = ctypes.create_unicode_buffer(64)
        user32.GetWindowTextW(kh, buf, 64)
        if buf.value in ("OK", "&OK"):
            user32.SendMessageW(kh, BM_CLICK, 0, 0)
            clicked = True
        return True

    user32.EnumChildWindows(handle, proc_type(child), 0)
    return clicked


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------

OK, FAIL, WARN, SKIP = "ok", "FAIL", "~", "skip"


@dataclass
class Result:
    tool: str
    group: str
    status: str
    ms: float | None = None
    detail: str = ""
    samples: list = field(default_factory=list)

    @property
    def counts_as_failure(self) -> bool:
        return self.status == FAIL


class Wedged(Exception):
    """Exception raised when REAPER becomes unresponsive."""


class Bench:
    """Benchmark execution environment state."""

    def __init__(self, mcp, repeat: int, timeout: float, may_dismiss: bool):
        self.mcp = mcp
        self.repeat = repeat
        self.timeout = timeout
        self.may_dismiss = may_dismiss
        self.results: list[Result] = []
        self.cleanups: list = []
        self.dialogs_seen: list[str] = []

    # -- plumbing ----------------------------------------------------------

    def _in_thread(self, tool: str, args: dict, times: int, budget: float):
        """Execute calls in a dedicated thread to prevent blocking the main event loop if REAPER hangs."""
        box: dict = {"samples": []}
        done = threading.Event()

        def worker():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                for _ in range(times):
                    started = time.perf_counter()
                    blocks = loop.run_until_complete(self.mcp.call_tool(tool, args))
                    box["samples"].append((time.perf_counter() - started) * 1000.0)
                    box["payload"] = _parse(blocks)
            except Exception as e:
                box["error"] = f"{type(e).__name__}: {e}"
            finally:
                try:
                    loop.close()
                except Exception:
                    pass
                done.set()

        threading.Thread(target=worker, daemon=True, name=f"bench-{tool}").start()

        if done.wait(budget):
            return box, ""

        # Inspect dialogs to determine cause of block.
        note = self._handle_block(tool, budget)
        # Wait for potential return after dialog dismissal.
        if done.wait(10.0):
            return box, note
        raise Wedged(note)

    def _handle_block(self, tool: str, budget: float) -> str:
        dialogs = open_dialogs()
        if not dialogs:
            return f"no reply within {budget:.0f}s and no dialog found; REAPER may be busy"

        notes = []
        for handle, title, lines in dialogs:
            message = " / ".join(t for t in lines if t not in ("OK", "&OK")) or "(no text)"
            self.dialogs_seen.append(f"{tool}: {title} - {message}")

            if title in DO_NOT_TOUCH:
                raise Wedged(
                    f"REAPER is showing '{title}': {message}\n"
                    "    Answer it in REAPER - choose 'New instance', NOT 'Terminate "
                    "instances', which would stop the Lua bridge as well."
                )
            if title in DISMISSABLE and self.may_dismiss and dismiss(handle):
                notes.append(f"REAPER said '{message}' (dialog dismissed)")
            else:
                # Log unfamiliar dialogs without interacting.
                notes.append(
                    f"REAPER is showing '{title}': {message}"
                    f"  [buttons: {', '.join(lines) or 'none found'}] - "
                    "answer it in REAPER"
                )
        return "; ".join(notes)

    def raw(self, tool: str, args: dict | None = None) -> dict:
        """Execute a single call and return the payload."""
        box, _ = self._in_thread(tool, args or {}, 1, self.timeout)
        return box.get("payload", {})

    # -- the recording API -------------------------------------------------

    def call(
        self,
        tool: str,
        args: dict | None = None,
        *,
        group: str,
        expect=None,
        may_fail: str = "",
        times: int = 1,
        budget: float | None = None,
    ) -> dict:
        """Execute a tool, validate the response against expected behavior, and log the result."""
        times = max(1, times)
        budget = budget or self.timeout * (times if times > 1 else 1)

        try:
            box, note = self._in_thread(tool, args or {}, times, budget)
        except Wedged:
            self.results.append(Result(tool, group, FAIL, None, "REAPER stopped answering"))
            raise

        samples = box.get("samples", [])
        ms = statistics.median(samples) if samples else None

        if "error" in box:
            self.results.append(Result(tool, group, FAIL, ms, box["error"], samples))
            return {}

        payload = box.get("payload")
        if payload is None:
            detail = note or f"no reply within {budget:.0f}s"
            self.results.append(Result(tool, group, FAIL, ms, detail, samples))
            return {}

        if not payload.get("success"):
            error = str(payload.get("error", "no error reported"))
            if note:
                error = f"{error}  <- {note}"
            status = WARN if may_fail else FAIL
            if may_fail:
                error = f"{error}  [expected: {may_fail}]"
            self.results.append(Result(tool, group, status, ms, error, samples))
            return payload

        problem = expect(payload) if expect else None
        if problem:
            self.results.append(
                Result(tool, group, FAIL, ms, f"wrong answer: {problem}", samples)
            )
        else:
            self.results.append(Result(tool, group, OK, ms, note, samples))
        return payload

    def skip(self, tool: str, group: str, reason: str) -> None:
        self.results.append(Result(tool, group, SKIP, None, reason))

    def fail(self, tool: str, group: str, detail: str) -> None:
        self.results.append(Result(tool, group, FAIL, None, detail))

    def confirm(self, probe, expected, label: str, tol: float | None = None) -> None:
        """Verify REAPER state matches the expected state. Fails the preceding recorded operation on mismatch."""
        if not self.results or self.results[-1].status != OK:
            return

        box: dict = {}
        done = threading.Event()

        def worker():
            try:
                box["value"] = probe()
            except Exception as e:
                box["error"] = f"{type(e).__name__}: {e}"
            finally:
                done.set()

        # Run probe in thread to prevent blocking on REAPER dialogs.
        threading.Thread(target=worker, daemon=True, name=f"probe-{label}").start()
        if not done.wait(self.timeout):
            self._demote(f"REAPER did not answer the {label} probe within {self.timeout:.0f}s")
            return
        if "error" in box:
            self._demote(f"could not read {label} back from REAPER: {box['error']}")
            return

        actual = box["value"]
        if callable(expected):
            agrees = bool(expected(actual))
            wanted = getattr(expected, "__doc__", None) or "the expected value"
        elif tol is not None:
            agrees = approx(actual, expected, tol)
            wanted = f"{expected!r} (+/-{tol})"
        else:
            agrees = actual == expected
            wanted = repr(expected)

        if not agrees:
            self._demote(f"REAPER says {label} is {actual!r}, not {wanted}")

    def _demote(self, detail: str) -> None:
        """Mark the last result as failed due to a state mismatch."""
        result = self.results[-1]
        result.status = FAIL
        result.detail = f"REAPER disagrees: {detail}" if not result.detail else \
                        f"{result.detail}; REAPER disagrees: {detail}"

    # -- undo --------------------------------------------------------------

    def defer(self, description: str, fn) -> None:
        self.cleanups.append((description, fn))

    def run_cleanups(self) -> list[str]:
        """Execute deferred cleanup operations in LIFO order."""
        problems = []
        for description, fn in reversed(self.cleanups):
            box: dict = {}
            done = threading.Event()

            def worker(_fn=fn, _box=box, _done=done):
                try:
                    _fn()
                except Exception as e:
                    _box["error"] = str(e)
                finally:
                    _done.set()

            threading.Thread(target=worker, daemon=True).start()
            if not done.wait(self.timeout):
                problems.append(f"{description}: timed out; do it by hand in REAPER")
            elif "error" in box:
                problems.append(f"{description}: {box['error']}")
        return problems


def _parse(blocks) -> dict:
    text = ""
    for block in blocks if isinstance(blocks, (list, tuple)) else []:
        text = getattr(block, "text", "") or ""
        if text:
            break
    try:
        return json.loads(text) if text else {}
    except ValueError:
        return {"success": False, "error": f"non-JSON reply: {text[:200]}"}


# --------------------------------------------------------------------------
# Test material
# --------------------------------------------------------------------------

def make_test_wav(path: Path) -> None:
    """Generate stereo audio file for analysis testing."""
    import numpy as np
    import soundfile as sf

    sr, duration = 48000, 3.0
    t = np.arange(int(sr * duration)) / sr

    left = 0.3 * np.sin(2 * np.pi * 220.0 * t)
    right = 0.3 * np.sin(2 * np.pi * 220.0 * t + 0.35)
    left += 0.12 * np.sin(2 * np.pi * 1760.0 * t)
    right += 0.12 * np.sin(2 * np.pi * 1760.0 * t)

    rng = np.random.default_rng(0)
    burst_len = int(0.02 * sr)
    envelope = np.exp(-np.linspace(0.0, 12.0, burst_len))
    for position in (0.5, 1.0, 1.5, 2.0, 2.5):
        start = int(position * sr)
        noise = rng.standard_normal(burst_len) * envelope * 0.45
        left[start:start + burst_len] += noise
        right[start:start + burst_len] += noise

    stereo = np.clip(np.stack([left, right], axis=1), -0.8, 0.8)
    sf.write(str(path), stereo, sr, subtype="PCM_24")


# --------------------------------------------------------------------------
# The plan
# --------------------------------------------------------------------------

def _project_tab_count(limit: int = 32) -> int:
    """Return the number of open project tabs."""
    from reapy import reascript_api as RPR  # noqa: PLC0415

    for i in range(limit):
        if "0x0000000000000000" in str(RPR.EnumProjects(i, "", 0)[0]):
            return i
    return limit


def approx(value, target, tolerance=0.05) -> bool:
    try:
        return abs(float(value) - float(target)) <= tolerance
    except (TypeError, ValueError):
        return False


# --------------------------------------------------------------------------
# Probes: Direct REAPER state access for verification.
# --------------------------------------------------------------------------

NULL_PTR = "0x0000000000000000"


def _is_null(pointer) -> bool:
    return not pointer or NULL_PTR in str(pointer)


def _track_id(index: int):
    from reaper_mcp.connection import get_project  # noqa: PLC0415

    return get_project().tracks[index].id


def _item_ptr(track_index: int, item_index: int):
    from reapy import reascript_api as RPR  # noqa: PLC0415

    return RPR.GetTrackMediaItem(_track_id(track_index), item_index)


def _note_count(track_index: int, item_index: int) -> int:
    """Return the number of notes in the specified item's active take."""
    from reapy import reascript_api as RPR  # noqa: PLC0415

    take = RPR.GetActiveTake(_item_ptr(track_index, item_index))
    if _is_null(take):
        return -1
    # (retval, take, notecnt, ccevtcnt, textsyxevtcnt)
    return RPR.MIDI_CountEvts(take, 0, 0, 0)[2]


def _envelope_points(track_index: int, name: str) -> int:
    """Return point count on a track envelope. Returns 0 if envelope is missing."""
    from reapy import reascript_api as RPR  # noqa: PLC0415

    env = RPR.GetTrackEnvelopeByName(_track_id(track_index), name)
    if _is_null(env):
        return 0
    return RPR.CountEnvelopePoints(env)


def _fx_enabled(track_index: int, fx_index: int) -> bool:
    from reapy import reascript_api as RPR  # noqa: PLC0415

    return bool(RPR.TrackFX_GetEnabled(_track_id(track_index), fx_index))


def _fx_count(track_index: int) -> int:
    from reapy import reascript_api as RPR  # noqa: PLC0415

    return RPR.TrackFX_GetCount(_track_id(track_index))


def _master_fx_names() -> list:
    from reaper_mcp.connection import get_project  # noqa: PLC0415
    from reapy import reascript_api as RPR  # noqa: PLC0415

    master = get_project().master_track.id
    # (retval, track, fx, name, sz)
    return [RPR.TrackFX_GetFXName(master, i, "", 256)[3]
            for i in range(RPR.TrackFX_GetCount(master))]


def _send_count(track_index: int, category: int = 0) -> int:
    """category 0 = sends out of this track, -1 = receives into it."""
    from reapy import reascript_api as RPR  # noqa: PLC0415

    return RPR.GetTrackNumSends(_track_id(track_index), category)


def _play_state() -> int:
    """Bit 1 = playing, bit 2 = paused, bit 4 = recording."""
    from reapy import reascript_api as RPR  # noqa: PLC0415

    return RPR.GetPlayState()


def _track_count() -> int:
    from reaper_mcp.connection import get_project  # noqa: PLC0415

    return get_project().n_tracks


def rendered_bytes(payload: dict) -> str | None:
    """Verify render output size to confirm actual file creation."""
    size = payload.get("file_size_bytes", 0)
    return None if size and size > 1000 else f"reported {size} bytes"


def run_plan(b: Bench, workdir: Path, destructive: bool) -> None:
    from reapy import reascript_api as RPR

    from reaper_mcp.connection import get_project
    from reaper_mcp.project_tools import _read_time_signature, _write_tempo, _write_time_signature
    from reaper_mcp.units import get_volume_db, set_volume_db

    # --- state to put back -------------------------------------------------
    project = get_project()
    original_tempo = project.bpm
    original_cursor = project.cursor_position
    original_master_db = get_volume_db(project.master_track)
    original_master_fx = project.master_track.n_fxs
    original_track_count = project.n_tracks
    original_signature = _read_time_signature()
    original_markers = RPR.CountTempoTimeSigMarkers(0)
    original_tabs = _project_tab_count()

    def report_extra_tabs():
        """Identify leftover project tabs without closing them."""
        extra = _project_tab_count() - original_tabs
        if extra > 0:
            raise RuntimeError(
                f"create_project opened {extra} extra project tab(s); close them "
                "in REAPER when you are done with them"
            )

    def restore_transport():
        RPR.Main_OnCommand(1016, 0)                      # Transport: Stop
        proj = get_project()
        proj.cursor_position = original_cursor
        proj.time_selection = (0.0, 0.0)

    def restore_tempo_map():
        """Restore previous tempo and time signature map."""
        _write_time_signature(*original_signature)
        _write_tempo(original_tempo)
        for i in range(RPR.CountTempoTimeSigMarkers(0) - 1, original_markers - 1, -1):
            RPR.DeleteTempoTimeSigMarker(0, i)
        RPR.UpdateTimeline()

    def restore_master():
        set_volume_db(get_project().master_track, original_master_db)

    def remove_added_master_fx():
        master = get_project().master_track
        for i in range(master.n_fxs - 1, original_master_fx - 1, -1):
            RPR.TrackFX_Delete(master.id, i)

    def remove_added_tracks():
        proj = get_project()
        for i in range(proj.n_tracks - 1, original_track_count - 1, -1):
            RPR.DeleteTrack(proj.tracks[i].id)

    b.defer("project tabs", report_extra_tabs)
    b.defer("restore tempo, time signature and markers", restore_tempo_map)
    b.defer("restore transport and cursor", restore_transport)
    b.defer("restore master volume", restore_master)
    b.defer("remove master FX added by the bench", remove_added_master_fx)
    b.defer("remove tracks added by the bench", remove_added_tracks)

    # --- project -----------------------------------------------------------
    g = "project"
    b.call("get_project_info", group=g, times=b.repeat,
           expect=lambda p: None if "track_count" in p else "no track_count")
    b.call("set_tempo", {"bpm": 140.0}, group=g,
           expect=lambda p: None if approx(p.get("tempo"), 140.0) else f"read back {p.get('tempo')}")
    b.call("set_time_signature", {"numerator": 3, "denominator": 4}, group=g,
           expect=lambda p: None if p.get("time_signature") == "3/4" else f"read back {p.get('time_signature')!r}")

    # Verify setting tempo functions correctly when a time signature marker exists.
    b.call("set_tempo", {"bpm": 90.0}, group=g,
           expect=lambda p: None if approx(p.get("tempo"), 90.0)
           else f"tempo after a time-signature change stuck at {p.get('tempo')}")
    after = b.raw("get_project_info")
    if not approx(after.get("tempo"), 90.0) and b.results[-1].status == OK:
        b.results[-1].status = FAIL
        b.results[-1].detail = f"get_project_info still reports {after.get('tempo')} BPM"
    if after.get("time_signature") != "3/4" and b.results[-1].status == OK:
        b.results[-1].status = FAIL
        b.results[-1].detail = (
            f"setting the tempo changed the time signature to {after.get('time_signature')!r}"
        )

    if destructive:
        saved = workdir / "bench.rpp"
        b.call("save_project", {"project_path": str(saved)}, group=g)
        # Verify file creation and clear project dirty flag.
        b.confirm(lambda: (saved.is_file() and saved.stat().st_size > 0, RPR.IsProjectDirty(0)),
                  lambda pair: pair[0] and pair[1] == 0,
                  f"(a file at {saved.name}, IsProjectDirty), wanted (True, 0)")

        b.call("load_project", {"project_path": str(saved)}, group=g)
        b.confirm(lambda: RPR.GetProjectName(0, "", 512)[2],
                  lambda name: saved.stem.lower() in str(name).lower(),
                  f"the open project name, wanted one containing {saved.stem!r}")

        tabs_before = _project_tab_count()
        b.call("create_project", {"tempo": 120.0}, group=g)
        # Verify project creation via tab count.
        b.confirm(_project_tab_count, tabs_before + 1, "the open project-tab count")
    else:
        for tool in ("save_project", "load_project", "create_project"):
            b.skip(tool, g, "writes or replaces the open project; --include-destructive")

    # --- tracks ------------------------------------------------------------
    g = "track"
    audio = b.call("create_track", {"name": PREFIX + "audio", "track_type": "audio"}, group=g,
                   expect=lambda p: None if p.get("name") == PREFIX + "audio" else f"named {p.get('name')!r}")
    midi = b.call("create_track", {"name": PREFIX + "midi", "track_type": "midi"}, group=g)
    audio_ix = audio.get("track_index", 0)
    midi_ix = midi.get("track_index", 1)

    b.call("rename_track", {"track_index": audio_ix, "name": PREFIX + "renamed"}, group=g,
           expect=lambda p: None if p.get("name") == PREFIX + "renamed" else f"named {p.get('name')!r}")
    b.call("set_track_volume", {"track_index": audio_ix, "volume_db": -6.0}, group=g,
           expect=lambda p: None if approx(p.get("volume_db"), -6.0, 0.1) else f"read back {p.get('volume_db')}")
    b.call("set_track_pan", {"track_index": audio_ix, "pan": -0.5}, group=g,
           expect=lambda p: None if approx(p.get("pan"), -0.5, 0.01) else f"read back {p.get('pan')}")
    b.call("set_track_mute", {"track_index": audio_ix, "muted": True}, group=g,
           expect=lambda p: None if p.get("muted") is True else "mute did not stick")
    b.call("set_track_mute", {"track_index": audio_ix, "muted": False}, group=g)
    b.call("set_track_solo", {"track_index": audio_ix, "soloed": True}, group=g,
           expect=lambda p: None if p.get("soloed") is True else "solo did not stick")
    b.call("set_track_solo", {"track_index": audio_ix, "soloed": False}, group=g)
    b.call("set_track_color", {"track_index": audio_ix, "r": 200, "g": 60, "b": 60}, group=g)
    # Perform API color verification in probe thread.
    b.confirm(lambda: (int(RPR.GetMediaTrackInfo_Value(_track_id(audio_ix), "I_CUSTOMCOLOR")),
                       RPR.ColorToNative(200, 60, 60) | 0x1000000),
              lambda pair: pair[0] == pair[1],
              "I_CUSTOMCOLOR (stored, wanted)")
    b.call("get_track_info", {"track_index": audio_ix}, group=g, times=b.repeat,
           expect=lambda p: None if approx(p.get("volume_db"), -6.0, 0.1) else "volume_db disagrees with what was set")
    b.call("list_tracks", group=g, times=b.repeat,
           expect=lambda p: None if p.get("count", 0) >= 2 else "bench tracks missing from the list")

    # Append temporary track to verify delete functionality.
    throwaway = b.call("create_track", {"name": PREFIX + "throwaway"}, group=g)
    if throwaway.get("success"):
        # Verify deletion via REAPER state rather than tool output.
        before = _track_count()
        b.call("delete_track", {"track_index": throwaway["track_index"]}, group=g)
        b.confirm(_track_count, before - 1, "the track count")
    else:
        b.skip("delete_track", g, "no track to delete")

    # --- audio -------------------------------------------------------------
    g = "audio"
    wav = workdir / "bench_tone.wav"
    make_test_wav(wav)

    imported = b.call("import_audio_file", {"file_path": str(wav), "track_index": audio_ix}, group=g,
                      expect=lambda p: None if approx(p.get("length"), 3.0, 0.05) else f"length {p.get('length')}")
    item_ix = imported.get("item_index", 0)

    b.call("set_cursor_position", {"position": 1.5}, group=g,
           expect=lambda p: None if approx(p.get("position"), 1.5, 0.01) else f"cursor at {p.get('position')}")
    b.call("play_project", group=g)
    # Verify play state bit to isolate playback from paused or recording states.
    b.confirm(lambda: _play_state() & 1, 1, "the transport play bit")
    b.call("stop_transport", group=g)
    b.confirm(_play_state, 0, "the transport state")

    b.call("edit_audio_item",
           {"track_index": audio_ix, "item_index": item_ix, "fade_in": 0.1, "fade_out": 0.1}, group=g)
    # Verify fade attributes via REAPER state as the tool payload lacks them.
    b.confirm(lambda: (RPR.GetMediaItemInfo_Value(_item_ptr(audio_ix, item_ix), "D_FADEINLEN"),
                       RPR.GetMediaItemInfo_Value(_item_ptr(audio_ix, item_ix), "D_FADEOUTLEN")),
              lambda pair: approx(pair[0], 0.1, 0.001) and approx(pair[1], 0.1, 0.001),
              "the item fades (in, out), wanted 0.1 each")
    b.call("adjust_pitch", {"track_index": audio_ix, "item_index": item_ix, "semitones": 2.0}, group=g,
           expect=lambda p: None if approx(p.get("pitch_semitones"), 2.0, 0.01) else f"read back {p.get('pitch_semitones')}")
    b.call("adjust_pitch", {"track_index": audio_ix, "item_index": item_ix, "semitones": 0.0}, group=g)
    b.call("adjust_playback_rate", {"track_index": audio_ix, "item_index": item_ix, "rate": 1.0}, group=g,
           expect=lambda p: None if approx(p.get("playback_rate"), 1.0, 0.01) else f"read back {p.get('playback_rate')}")

    if destructive:
        b.call("start_recording", {"track_index": midi_ix}, group=g)
        # Verify recording state bit to confirm arming and transport start.
        b.confirm(lambda: _play_state() & 4, 4, "the transport record bit")
        b.call("stop_transport", group=g)
        b.confirm(_play_state, 0, "the transport state")
    else:
        b.skip("start_recording", g, "records audio to disk; --include-destructive")

    # --- MIDI --------------------------------------------------------------
    g = "midi"
    item = b.call("create_midi_item", {"track_index": midi_ix, "start_position": 0.0, "length": 2.0}, group=g,
                  expect=lambda p: None if approx(p.get("length"), 2.0, 0.05) else f"length {p.get('length')}")
    midi_item_ix = item.get("item_index", 0)
    notes_before = _note_count(midi_ix, midi_item_ix)
    b.call("add_midi_note",
           {"track_index": midi_ix, "item_index": midi_item_ix, "pitch": 60,
            "start": 0.0, "length": 0.5, "velocity": 100}, group=g)
    b.confirm(lambda: _note_count(midi_ix, midi_item_ix), notes_before + 1,
              "the note count in the take")
    b.call("create_chord_progression",
           {"track_index": midi_ix, "chords": "C,Am,F,G7", "start_position": 4.0}, group=g,
           expect=lambda p: None if len(p.get("chords", [])) == 4 else f"{len(p.get('chords', []))} chords placed")
    b.call("create_drum_pattern",
           {"track_index": midi_ix, "pattern": "k...h...s...h...", "start_position": 12.0}, group=g)
    # Verify note insertion count based on pattern length.
    b.confirm(lambda: _note_count(midi_ix, RPR.CountTrackMediaItems(_track_id(midi_ix)) - 1),
              4, "the drum note count")

    # --- FX ----------------------------------------------------------------
    g = "fx"
    fx = b.call("add_fx", {"track_index": audio_ix, "fx_name": "ReaEQ"}, group=g,
                expect=lambda p: None if "ReaEQ" in str(p.get("name", "")) else f"added {p.get('name')!r}")
    fx_ix = fx.get("fx_index", 0)
    if fx.get("success"):
        b.call("list_track_fx", {"track_index": audio_ix}, group=g,
               expect=lambda p: None if p.get("fx") else "no FX listed after add_fx")
        b.call("get_fx_parameters", {"track_index": audio_ix, "fx_index": fx_ix}, group=g,
               expect=lambda p: None if p.get("parameters") else "no parameters returned")
        b.call("set_fx_parameter",
               {"track_index": audio_ix, "fx_index": fx_ix, "param_index": 0, "value": 0.6}, group=g,
               expect=lambda p: None if approx(p.get("value"), 0.6, 0.02)
               else f"asked for 0.6, REAPER kept {p.get('value')}")
        # Verify parameter setting via secondary read to ensure REAPER state update.
        stored = (b.raw("get_fx_parameters",
                        {"track_index": audio_ix, "fx_index": fx_ix})
                  .get("parameters") or [{}])[0].get("normalized_value")
        if not approx(stored, 0.6, 0.02) and b.results[-1].status == OK:
            b.results[-1].status = FAIL
            b.results[-1].detail = f"get_fx_parameters still reports {stored}"
        # Verify enable state toggle.
        b.call("bypass_fx", {"track_index": audio_ix, "fx_index": fx_ix, "bypassed": True}, group=g)
        b.confirm(lambda: _fx_enabled(audio_ix, fx_ix), False, "TrackFX_GetEnabled after bypassing")
        b.call("bypass_fx", {"track_index": audio_ix, "fx_index": fx_ix, "bypassed": False}, group=g)
        b.confirm(lambda: _fx_enabled(audio_ix, fx_ix), True, "TrackFX_GetEnabled after un-bypassing")

        b.call("load_fx_preset",
               {"track_index": audio_ix, "fx_index": fx_ix, "preset_name": "Default"}, group=g,
               may_fail="stock ReaEQ ships no preset named 'Default'")
        # Verify preset name if loading succeeds.
        b.confirm(lambda: RPR.TrackFX_GetPreset(_track_id(audio_ix), fx_ix, "", 256)[3],
                  "Default", "the loaded preset name")

        fx_before = _fx_count(audio_ix)
        b.call("remove_fx", {"track_index": audio_ix, "fx_index": fx_ix}, group=g)
        b.confirm(lambda: _fx_count(audio_ix), fx_before - 1, "the track FX count")
    else:
        # Skip FX operations if creation failed.
        for tool in ("list_track_fx", "get_fx_parameters", "set_fx_parameter",
                     "bypass_fx", "load_fx_preset", "remove_fx"):
            b.skip(tool, g, "add_fx failed, so there is no FX to address")

    # --- mixing ------------------------------------------------------------
    g = "mixing"
    tracks_before_bus = _track_count()
    bus = b.call("create_bus", {"name": PREFIX + "bus", "track_indices": [audio_ix, midi_ix]}, group=g)
    bus_ix = bus.get("bus_index", midi_ix + 1)
    # Verify bus creation by checking track count and receive routings.
    b.confirm(lambda: (_track_count(), _send_count(bus_ix, -1)),
              lambda pair: pair[0] == tracks_before_bus + 1 and pair[1] >= 2,
              f"(track count, receives into the bus), wanted ({tracks_before_bus + 1}, >=2)")

    sends_before = _send_count(audio_ix, 0)
    b.call("create_send",
           {"source_track_index": audio_ix, "dest_track_index": bus_ix, "volume_db": -3.0}, group=g)
    b.confirm(lambda: _send_count(audio_ix, 0), sends_before + 1, "the send count on the source track")

    sends = b.call("list_sends", {"track_index": audio_ix}, group=g,
                   expect=lambda p: None if p.get("sends") else "no sends listed after create_send")
    last_send = len(sends.get("sends", [])) - 1
    if last_send >= 0:
        b.call("set_send_volume",
               {"source_track_index": audio_ix, "send_index": last_send, "volume_db": -6.0}, group=g)
        # Verify send gain using linear scale.
        b.confirm(lambda: RPR.GetTrackSendInfo_Value(_track_id(audio_ix), 0, last_send, "D_VOL"),
                  10 ** (-6.0 / 20.0), "the send gain (linear)", tol=0.01)

        before_remove = _send_count(audio_ix, 0)
        b.call("remove_send", {"source_track_index": audio_ix, "send_index": last_send}, group=g)
        b.confirm(lambda: _send_count(audio_ix, 0), before_remove - 1, "the send count after removal")
    else:
        b.skip("set_send_volume", g, "no send to operate on")
        b.skip("remove_send", g, "no send to operate on")

    # Test automation point addition assuming envelope UI visibility.
    b.call("add_volume_automation", {"track_index": audio_ix, "position": 1.0, "value_db": -3.0}, group=g,
           may_fail="the volume envelope must be shown in REAPER first")
    b.confirm(lambda: _envelope_points(audio_ix, "Volume"), lambda n: n > 0,
              "points on the Volume envelope, wanted at least one")
    b.call("add_pan_automation", {"track_index": audio_ix, "position": 1.0, "pan": 0.25}, group=g,
           may_fail="the pan envelope must be shown in REAPER first")
    b.confirm(lambda: _envelope_points(audio_ix, "Pan"), lambda n: n > 0,
              "points on the Pan envelope, wanted at least one")

    # --- render ------------------------------------------------------------
    g = "render"
    b.call("render_project", {"output_path": str(workdir / "full.wav")}, group=g,
           expect=rendered_bytes)
    b.call("render_time_selection",
           {"output_path": str(workdir / "slice.wav"), "start": 0.0, "end": 1.0}, group=g,
           expect=rendered_bytes)
    b.call("render_stems",
           {"output_directory": str(workdir / "stems"), "track_indices": [audio_ix]}, group=g,
           expect=lambda p: None if all(s.get("exists") for s in p.get("stems", [{}])) else "a stem file is missing")

    # --- analysis ----------------------------------------------------------
    g = "analysis"
    b.call("analyze_loudness", group=g,
           expect=lambda p: None if -70 < p.get("integrated_lufs", 0) < 0 else f"implausible LUFS {p.get('integrated_lufs')}")
    b.call("detect_clipping", group=g,
           expect=lambda p: None if p.get("clipping_detected") is False else "reported clipping on -1.9 dBFS material")
    b.call("analyze_dynamics", group=g,
           expect=lambda p: None if p.get("crest_factor_db", 0) > 0 else f"crest factor {p.get('crest_factor_db')}")
    b.call("analyze_frequency_spectrum", group=g,
           expect=lambda p: None if len(p.get("frequency_bands", {})) == 7 else "expected seven bands")
    b.call("analyze_stereo_field", group=g,
           expect=lambda p: None if -1.0 <= p.get("lr_correlation", 9) <= 1.0 else f"correlation {p.get('lr_correlation')}")
    b.call("analyze_transients", group=g,
           expect=lambda p: None if p.get("onset_count", 0) > 0 else "found no onsets in material with five bursts")

    # --- mastering ---------------------------------------------------------
    g = "mastering"
    b.call("set_master_volume", {"volume_db": -3.0}, group=g,
           expect=lambda p: None if approx(p.get("volume_db"), -3.0, 0.1) else f"read back {p.get('volume_db')}")
    master_fx = b.call("add_master_fx", {"fx_name": "ReaComp"}, group=g,
                       expect=lambda p: None if "ReaComp" in str(p.get("name", "")) else f"added {p.get('name')!r}")
    b.call("list_master_fx", group=g,
           expect=lambda p: None if p.get("fx") else "no master FX listed after add_master_fx")
    if master_fx.get("success"):
        b.call("set_master_fx_parameter",
               {"fx_index": master_fx.get("fx_index", 0), "param_index": 0, "value": 0.35}, group=g,
               expect=lambda p: None if approx(p.get("value"), 0.35, 0.02)
               else f"asked for 0.35, REAPER kept {p.get('value')}")
    else:
        b.skip("set_master_fx_parameter", g, "add_master_fx failed, so there is no FX to address")
    limiter_before = len(_master_fx_names())
    b.call("apply_limiter", {"threshold_db": -0.5}, group=g)
    # Verify limiter application by checking plugin name.
    b.confirm(_master_fx_names,
              lambda names: len(names) == limiter_before + 1
              and any("limit" in n.lower() for n in names),
              f"the master FX chain, wanted {limiter_before + 1} ending in a limiter")
    b.call("apply_mastering_chain", {"preset": "default"}, group=g,
           expect=lambda p: None if len(p.get("fx_chain", [])) == 3 else f"{len(p.get('fx_chain', []))} of 3 plugins added")
    b.call("normalize_project", {"target_lufs": -14.0}, group=g,
           expect=lambda p: None if approx(p.get("target_lufs"), -14.0) else "target not echoed back")


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return ordered[min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))]


def report(b: Bench, registered: list[str], elapsed: float,
           cleanup_problems: list[str], aborted: str) -> int:
    results = b.results
    width = max((len(r.tool) for r in results), default=20) + 2

    print()
    print("=" * 78)
    print("  REAPER MCP bench")
    print("=" * 78)

    current_group = None
    for r in results:
        if r.group != current_group:
            current_group = r.group
            print(f"\n-- {current_group}")
        timing = f"{r.ms:8.1f} ms" if r.ms is not None else "         -  "
        line = f"  {r.status:<5}{timing}  {r.tool:<{width}}"
        if r.detail:
            line += r.detail if len(r.detail) < 400 else r.detail[:397] + "..."
        print(line)

    timed = [r for r in results if len(r.samples) > 1]
    if timed:
        print("\n-- latency, repeated calls (ms)")
        print(f"  {'tool':<28}{'n':>4}{'min':>9}{'p50':>9}{'p95':>9}{'max':>9}")
        for r in timed:
            s = r.samples
            print(f"  {r.tool:<28}{len(s):>4}{min(s):>9.1f}"
                  f"{statistics.median(s):>9.1f}{percentile(s, 0.95):>9.1f}{max(s):>9.1f}")

    called = {r.tool for r in results if r.status != SKIP}
    skipped = {r.tool for r in results if r.status == SKIP}
    untouched = sorted(set(registered) - called - skipped)
    failures = [r for r in results if r.counts_as_failure]
    warnings = [r for r in results if r.status == WARN]

    print()
    print("=" * 78)
    print(f"  {len(called)}/{len(registered)} tools called   "
          f"{len(failures)} failed   {len(warnings)} blocked by preconditions   "
          f"{len(skipped)} skipped")
    print(f"  wall clock: {elapsed:.1f}s")
    if untouched:
        print(f"  never reached: {', '.join(untouched)}")
    if b.dialogs_seen:
        print("\n  REAPER opened a modal dialog during the run:")
        for d in b.dialogs_seen:
            print(f"    {d}")
    if aborted:
        print(f"\n  RUN ABORTED: {aborted}")
    if cleanup_problems:
        print("\n  CLEANUP DID NOT FINISH - tracks named "
              f"'{PREFIX}*' may be left in the project:")
        for problem in cleanup_problems:
            print(f"    {problem}")
    if failures:
        print("\n  failures:")
        for r in failures:
            print(f"    {r.tool}: {r.detail}")
    print("=" * 78)

    return 1 if failures or cleanup_problems or aborted else 0


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repeat", type=int, default=10,
                        help="samples per read-only tool for the latency table (default 10)")
    parser.add_argument("--timeout", type=float, default=30.0,
                        help="seconds to wait for one call before calling it blocked (default 30)")
    parser.add_argument("--json", metavar="PATH", help="also write results as JSON")
    parser.add_argument("--include-destructive", action="store_true",
                        help="also run save/load/create_project and start_recording")
    parser.add_argument("--scratch", action="store_true",
                        help="open a fresh project tab and run everything there; "
                             "implies --include-destructive")
    parser.add_argument("--no-dismiss", action="store_true",
                        help="report REAPER's modal dialogs but leave them on screen")
    parser.add_argument("--list", action="store_true",
                        help="print the registered tool names and exit")
    args = parser.parse_args()

    import logging
    logging.basicConfig(level=logging.ERROR, stream=sys.stderr, format="%(name)s: %(message)s")

    from reaper_mcp.server import mcp

    registered = [t.name for t in asyncio.run(mcp.list_tools())]
    if args.list:
        for name in sorted(registered):
            print(name)
        return 0

    b = Bench(mcp, repeat=args.repeat, timeout=args.timeout, may_dismiss=not args.no_dismiss)

    # Establish connection prior to benchmarking to exclude initialization latency.
    print("connecting to REAPER...", flush=True)
    started = time.perf_counter()
    try:
        probe = b.raw("get_project_info")
    except Wedged as e:
        print(f"\nREAPER is blocked before the run even started:\n  {e}", file=sys.stderr)
        return 1
    if not probe.get("success"):
        print(f"\nREAPER is not answering: {probe.get('error')}", file=sys.stderr)
        return 1

    print(f"connected in {time.perf_counter() - started:.1f}s  -  "
          f"project {probe.get('name') or '(unsaved)'!r}, "
          f"{probe.get('track_count')} tracks, {probe.get('length', 0):.1f}s long")
    print(f"interpreter: {sys.executable}")

    # Enable destructive tools when scratch mode is active.
    destructive = args.include_destructive or args.scratch

    if args.scratch:
        from reapy import reascript_api as RPR  # noqa: PLC0415

        # Abort if project is dirty to prevent blocking save dialogs.
        if RPR.IsProjectDirty(0):
            print("\nThe open project has unsaved changes, and --scratch would make REAPER\n"
                  "ask about them in a modal dialog - which freezes every route into\n"
                  "REAPER until someone clicks it.\n\n"
                  "  Save or discard your work in REAPER, then run this again.",
                  file=sys.stderr)
            return 1

        tabs_before_scratch = _project_tab_count()
        RPR.Main_OnCommand(40859, 0)  # Action: New project tab
        if _project_tab_count() != tabs_before_scratch + 1:
            print("\nAsked REAPER for a new project tab and did not get one; refusing to\n"
                  "run destructive tools against the project you have open.", file=sys.stderr)
            return 1
        print(f"scratch mode: working in a new tab ({_project_tab_count()} now open)")
    elif not destructive:
        print("project-replacing tools are skipped; pass --include-destructive to run them")

    workdir = Path(tempfile.mkdtemp(prefix="reaper-bench-"))
    aborted = ""
    started = time.perf_counter()
    try:
        run_plan(b, workdir, destructive)
    except Wedged as e:
        aborted = str(e)
    finally:
        elapsed = time.perf_counter() - started
        cleanup_problems = [] if aborted else b.run_cleanups()
        if aborted:
            cleanup_problems = [
                "skipped entirely - REAPER was not answering, so nothing could be undone"
            ]
        # Delete temporary assets after track removal to avoid file locks.
        shutil.rmtree(workdir, ignore_errors=True)

    code = report(b, registered, elapsed, cleanup_problems, aborted)

    if args.scratch:
        # Retain scratch tab to prevent blocking save dialogs on exit.
        print("  scratch mode left its project tab open - close it in REAPER without\n"
              "  saving when you are done reading the results above.\n")

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "interpreter": sys.executable,
                    "elapsed_sec": round(elapsed, 2),
                    "registered_tools": registered,
                    "dialogs": b.dialogs_seen,
                    "aborted": aborted,
                    "cleanup_problems": cleanup_problems,
                    "results": [
                        {
                            "tool": r.tool, "group": r.group, "status": r.status,
                            "ms": round(r.ms, 2) if r.ms is not None else None,
                            "detail": r.detail,
                            "samples_ms": [round(s, 2) for s in r.samples],
                        }
                        for r in b.results
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"  wrote {args.json}")

    return code


if __name__ == "__main__":
    _ensure_dependencies()
    raise SystemExit(main())
