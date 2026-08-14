#!/usr/bin/env python3
"""Exercise every MCP tool against a live REAPER and report what works.

The server has 58 tools and no tests. Most of them cannot be unit tested in any
useful sense - their whole behaviour is a round trip into REAPER - so the honest
check is the expensive one: call them for real, against a running REAPER, and
see what comes back.

Two questions get answered at once, because the same calls answer both:

  * Does it work?  Each tool is called with arguments that should succeed, and
    where there is a checkable claim - set the tempo to 140, read 140 back - the
    claim is checked. A tool that returns ``success: true`` while doing nothing
    is a failure this catches and a smoke test does not. That is not
    hypothetical: ``render_project`` reports success and a file size of 0 when
    REAPER has quietly written the audio somewhere else.

  * How slow is it?  Every call is timed, and the cheap read-only ones are
    repeated so the reapy round trip shows up as a distribution rather than one
    sample. That number is the budget every tool spends, so it is worth knowing.

Calls go through ``mcp.call_tool`` rather than the Python functions directly.
The functions are closures inside ``register_tools`` and not reachable anyway,
but the better reason is that this is the path Claude uses: argument coercion,
schema validation and JSON serialisation are all part of what is being tested.

NOTHING HERE MAY HANG

Every call runs in its own thread and is waited on with a timeout, and that is
not defensive habit - it is the first thing this script found. A render whose
bounds are empty makes REAPER open a modal "Nothing to render!" box, and a modal
box stops REAPER running deferred scripts, so the reapy server stops answering
and the call never returns. The run has to survive that, name it, and carry on.

So on a timeout the script looks at REAPER's windows, reports the dialog by its
text, clicks OK on a render error, and collects the result the call finally
returns. It will not touch a "ReaScript task control" dialog: the wrong button
there stops the bridge as well as the server, and ``connection.py`` explains at
length why. It aborts instead and says what is on screen.

WHAT IT TOUCHES

Everything happens inside the project that is already open, on tracks this
script creates and then deletes. It never calls ``create_project``,
``load_project`` or ``save_project`` unless asked - a new-project command on a
project with unsaved changes opens a modal save dialog - and for the same
reason ``start_recording`` is off by default.

State that is global to the project - tempo, master volume, master FX, cursor,
time selection - is read before and restored after. Tracks and files created
along the way are removed in reverse order, which matters on Windows: REAPER
holds the imported WAV open until the item referencing it is gone.

USAGE

    python scripts/benchmark_tools.py                  # the default run
    python scripts/benchmark_tools.py --repeat 30      # more latency samples
    python scripts/benchmark_tools.py --json out.json  # machine-readable results
    python scripts/benchmark_tools.py --timeout 60     # slower machine, longer renders
    python scripts/benchmark_tools.py --include-destructive
    python scripts/benchmark_tools.py --list           # registered tools, no calls

Exit code is 0 when nothing failed, 1 otherwise. Tools with a documented
precondition this script cannot meet - an envelope that has to be visible in the
UI, a preset that has to exist - are marked ``~`` and do not affect it.
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

# Names for everything this script creates, so a run that dies halfway leaves
# something identifiable behind rather than an anonymous "Track 3".
PREFIX = "__bench__"

# Dialogs REAPER opens that this script knows how to handle. The distinction
# matters: one is safe to dismiss, the other is not.
DISMISSABLE = ("Render Error",)
DO_NOT_TOUCH = ("ReaScript task control",)


# --------------------------------------------------------------------------
# Interpreter
# --------------------------------------------------------------------------

def _ensure_dependencies() -> None:
    """Re-exec under the managed virtualenv if this interpreter cannot serve.

    Running ``python scripts/benchmark_tools.py`` should work from any shell, and the
    interpreter on PATH is usually not the one bootstrap.py built. The launcher
    already knows where that environment lives, so ask it rather than keeping a
    second copy of the path.
    """
    sys.path.insert(0, str(ROOT / "src"))
    try:
        import mcp.server.fastmcp  # noqa: F401
        import reapy  # noqa: F401
        import numpy  # noqa: F401
        return
    except ImportError as e:
        # Bind it outside the handler; Python unbinds `as` names on the way out,
        # and this message is the only useful thing to print if there turns out
        # to be no virtualenv to fall back to.
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
    """Every window REAPER has open besides its main one, with the text inside.

    Deliberately not a list of titles this script knows. The first version only
    recognised two, so the run that hit REAPER's "No tracks are armed for
    recording" warning reported "no dialog found" and gave up - the one piece of
    information that would have explained the freeze was on screen and went
    unread. Whether a dialog is safe to dismiss is a separate question from
    whether it can be seen, and only the first one deserves a list.

    Scoped by process id so an unrelated window from another application is
    never mistaken for REAPER's.
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

    # REAPER's main window names itself; everything else sharing its process is
    # a dialog, a progress box, or a floating window.
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
    """Click OK on one dialog. Only ever called for DISMISSABLE titles."""
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
    """REAPER stopped answering and this script must not keep poking it."""


class Bench:
    """Calls tools, times them, survives modal dialogs, and remembers the undo."""

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
        """Run ``times`` calls on a private loop; give up on the thread if it hangs.

        A thread rather than ``asyncio.wait_for`` because the blocked call is
        blocked inside REAPER, not inside the event loop - cancelling the task
        would leave the loop wedged for every call after it. An abandoned thread
        is a daemon and costs nothing; it unblocks by itself the moment the
        dialog is cleared.

        The clock is started around the call alone, so the cost of standing the
        thread and the loop up never lands in a reported latency.
        """
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

        # Blocked. Find out what is on screen before deciding anything.
        note = self._handle_block(tool, budget)
        # A dismissed dialog releases REAPER, and the call then returns on its
        # own - usually with the error it should have reported in the first
        # place. Worth waiting a moment for, because that error is the finding.
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
                # Reported in full and left alone. Anything not on the safe list
                # may be asking a question with real consequences - "save
                # changes?" has three answers and this script is not entitled to
                # pick one.
                notes.append(
                    f"REAPER is showing '{title}': {message}"
                    f"  [buttons: {', '.join(lines) or 'none found'}] - "
                    "answer it in REAPER"
                )
        return "; ".join(notes)

    def raw(self, tool: str, args: dict | None = None) -> dict:
        """One call, for the places that need a value rather than a result row."""
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
        """Call a tool, record the outcome, and hand back its payload.

        ``expect`` is a callable given the payload; it returns an error string
        when the tool's answer is wrong. That distinction is the point of this
        script - "it returned" and "it did what it said" are different claims,
        and only the second one is worth anything.

        ``may_fail`` names a precondition this script cannot create. Those are
        reported but do not fail the run.
        """
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

    # -- undo --------------------------------------------------------------

    def defer(self, description: str, fn) -> None:
        self.cleanups.append((description, fn))

    def run_cleanups(self) -> list[str]:
        """Undo in reverse, with the same no-hanging rule as everything else."""
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
    """Three seconds of stereo audio chosen so the analysis tools have work.

    A sine alone makes four of the five analysis tools trivial: no transients to
    find, identical channels so stereo width is zero, and a crest factor that is
    a constant. So: a tone for the spectrum, a small phase offset between the
    channels so mid/side and correlation are non-degenerate, and periodic noise
    bursts for onset detection. Peak sits near -1.9 dBFS, which is loud enough
    to measure and quiet enough that ``detect_clipping`` should report nothing.
    """
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
    """How many project tabs are open.

    Bounded, and compared against the null pointer rather than truthiness:
    EnumProjects returns a pointer string past the end of the list too, so
    `while EnumProjects(i, ...)[0]` is an infinite loop that hammers REAPER.
    """
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


def rendered_bytes(payload: dict) -> str | None:
    """A render that reports success is not a render that produced audio.

    ``os.path.exists`` is true for a directory, and REAPER makes a directory of
    exactly the requested name when RENDER_FILE is handed a full path while
    RENDER_PATTERN still holds a filename. So check the size the tool reports,
    which is 0 in that case.
    """
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
        """Notice, but do not close, project tabs create_project opened.

        REAPER's New Project opens a tab rather than replacing the current one,
        so --include-destructive always leaves one behind. Closing it is not
        this script's call: a tab with unsaved changes asks a three-button
        question on the way out, and answering that is how work gets thrown
        away. Naming it is enough.
        """
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
        """Tempo, time signature, and any marker the run created.

        Order is load bearing: the signature goes back first because writing it
        is what creates the marker, and the tempo second because with a marker
        present it is the marker that holds the tempo. Markers the bench added
        are then dropped, newest first, so an existing one at index 0 survives.
        """
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

    # Order matters here, which is the whole reason for the second call. REAPER
    # keeps the time signature in a tempo marker, and while that marker exists
    # the plain tempo setter stops taking effect - so a tempo set after a time
    # signature used to be accepted, reported as done, and ignored.
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
        b.call("save_project", {"project_path": str(workdir / "bench.rpp")}, group=g)
        b.call("load_project", {"project_path": str(workdir / "bench.rpp")}, group=g)
        b.call("create_project", {"tempo": 120.0}, group=g)
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
    b.call("get_track_info", {"track_index": audio_ix}, group=g, times=b.repeat,
           expect=lambda p: None if approx(p.get("volume_db"), -6.0, 0.1) else "volume_db disagrees with what was set")
    b.call("list_tracks", group=g, times=b.repeat,
           expect=lambda p: None if p.get("count", 0) >= 2 else "bench tracks missing from the list")

    # Made and removed on the spot, at the end of the track list, so the indices
    # everything below depends on are never disturbed.
    throwaway = b.call("create_track", {"name": PREFIX + "throwaway"}, group=g)
    if throwaway.get("success"):
        before = b.raw("list_tracks").get("count", 0)
        b.call("delete_track", {"track_index": throwaway["track_index"]}, group=g)
        after = b.raw("list_tracks").get("count", 0)
        if after != before - 1 and b.results[-1].status == OK:
            b.results[-1].status = FAIL
            b.results[-1].detail = f"track count went {before} -> {after}, expected {before - 1}"
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
    b.call("stop_transport", group=g)
    b.call("edit_audio_item",
           {"track_index": audio_ix, "item_index": item_ix, "fade_in": 0.1, "fade_out": 0.1}, group=g)
    b.call("adjust_pitch", {"track_index": audio_ix, "item_index": item_ix, "semitones": 2.0}, group=g,
           expect=lambda p: None if approx(p.get("pitch_semitones"), 2.0, 0.01) else f"read back {p.get('pitch_semitones')}")
    b.call("adjust_pitch", {"track_index": audio_ix, "item_index": item_ix, "semitones": 0.0}, group=g)
    b.call("adjust_playback_rate", {"track_index": audio_ix, "item_index": item_ix, "rate": 1.0}, group=g,
           expect=lambda p: None if approx(p.get("playback_rate"), 1.0, 0.01) else f"read back {p.get('playback_rate')}")

    if destructive:
        b.call("start_recording", {"track_index": midi_ix}, group=g)
        b.call("stop_transport", group=g)
    else:
        b.skip("start_recording", g, "records audio to disk; --include-destructive")

    # --- MIDI --------------------------------------------------------------
    g = "midi"
    item = b.call("create_midi_item", {"track_index": midi_ix, "start_position": 0.0, "length": 2.0}, group=g,
                  expect=lambda p: None if approx(p.get("length"), 2.0, 0.05) else f"length {p.get('length')}")
    midi_item_ix = item.get("item_index", 0)
    b.call("add_midi_note",
           {"track_index": midi_ix, "item_index": midi_item_ix, "pitch": 60,
            "start": 0.0, "length": 0.5, "velocity": 100}, group=g)
    b.call("create_chord_progression",
           {"track_index": midi_ix, "chords": "C,Am,F,G7", "start_position": 4.0}, group=g,
           expect=lambda p: None if len(p.get("chords", [])) == 4 else f"{len(p.get('chords', []))} chords placed")
    b.call("create_drum_pattern",
           {"track_index": midi_ix, "pattern": "k...h...s...h...", "start_position": 12.0}, group=g)

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
        # Confirmed against a separate read, because a setter grading its own
        # work is exactly how the original silent no-op survived: it assigned to
        # an attribute reapy does not define and reported the value it was
        # handed, never the one REAPER holds.
        stored = (b.raw("get_fx_parameters",
                        {"track_index": audio_ix, "fx_index": fx_ix})
                  .get("parameters") or [{}])[0].get("normalized_value")
        if not approx(stored, 0.6, 0.02) and b.results[-1].status == OK:
            b.results[-1].status = FAIL
            b.results[-1].detail = f"get_fx_parameters still reports {stored}"
        b.call("bypass_fx", {"track_index": audio_ix, "fx_index": fx_ix, "bypassed": True}, group=g)
        b.call("bypass_fx", {"track_index": audio_ix, "fx_index": fx_ix, "bypassed": False}, group=g)
        b.call("load_fx_preset",
               {"track_index": audio_ix, "fx_index": fx_ix, "preset_name": "Default"}, group=g,
               may_fail="stock ReaEQ ships no preset named 'Default'")
        b.call("remove_fx", {"track_index": audio_ix, "fx_index": fx_ix}, group=g)
    else:
        # Everything here addresses an FX by index; without one they would all
        # fail for a reason that has nothing to do with them.
        for tool in ("list_track_fx", "get_fx_parameters", "set_fx_parameter",
                     "bypass_fx", "load_fx_preset", "remove_fx"):
            b.skip(tool, g, "add_fx failed, so there is no FX to address")

    # --- mixing ------------------------------------------------------------
    g = "mixing"
    bus = b.call("create_bus", {"name": PREFIX + "bus", "track_indices": [audio_ix, midi_ix]}, group=g)
    bus_ix = bus.get("bus_index", midi_ix + 1)

    b.call("create_send",
           {"source_track_index": audio_ix, "dest_track_index": bus_ix, "volume_db": -3.0}, group=g)
    sends = b.call("list_sends", {"track_index": audio_ix}, group=g,
                   expect=lambda p: None if p.get("sends") else "no sends listed after create_send")
    last_send = len(sends.get("sends", [])) - 1
    if last_send >= 0:
        b.call("set_send_volume",
               {"source_track_index": audio_ix, "send_index": last_send, "volume_db": -6.0}, group=g)
        b.call("remove_send", {"source_track_index": audio_ix, "send_index": last_send}, group=g)
    else:
        b.skip("set_send_volume", g, "no send to operate on")
        b.skip("remove_send", g, "no send to operate on")

    # Envelopes have to be visible in the UI before REAPER hands one back, and
    # nothing in the tool surface can make that happen.
    b.call("add_volume_automation", {"track_index": audio_ix, "position": 1.0, "value_db": -3.0}, group=g,
           may_fail="the volume envelope must be shown in REAPER first")
    b.call("add_pan_automation", {"track_index": audio_ix, "position": 1.0, "pan": 0.25}, group=g,
           may_fail="the pan envelope must be shown in REAPER first")

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
    b.call("apply_limiter", {"threshold_db": -0.5}, group=g)
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

    # Pay the connection cost before anything is timed. The first call of a
    # session can wait up to a minute for REAPER to publish its reapy port, and
    # that belongs to nobody's latency budget.
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
    if not args.include_destructive:
        print("project-replacing tools are skipped; pass --include-destructive to run them")

    workdir = Path(tempfile.mkdtemp(prefix="reaper-bench-"))
    aborted = ""
    started = time.perf_counter()
    try:
        run_plan(b, workdir, args.include_destructive)
    except Wedged as e:
        aborted = str(e)
    finally:
        elapsed = time.perf_counter() - started
        cleanup_problems = [] if aborted else b.run_cleanups()
        if aborted:
            cleanup_problems = [
                "skipped entirely - REAPER was not answering, so nothing could be undone"
            ]
        # REAPER holds the imported WAV until the item referencing it is gone,
        # so this has to follow the track deletions above.
        shutil.rmtree(workdir, ignore_errors=True)

    code = report(b, registered, elapsed, cleanup_problems, aborted)

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
