#!/usr/bin/env python3
"""Check that every tool that changes the project is exactly one undo step.

For each tool decorated with ``records_undo``, the script saves the project to a
``.rpp``, calls the tool, undoes once, saves again, and compares the two files.
A tool passes when it reported success without ``unconfirmed``, the top of the
undo history is ``MCP: <tool>``, and the project text after the undo equals the
text before the call. It also checks the backup made before the first change and
the ``unconfirmed`` flag on a write that changes nothing.

Everything runs in a new project tab bound to a temporary file. The open project
is never touched; its tab is selected again at the end. Needs REAPER running with
the distant API and ``claude_bridge.lua`` (fixtures are built through the bridge).

USAGE
    python scripts/check_undo.py
    python scripts/check_undo.py set_track_volume edit_items

Exit code is 0 when every check passes, 1 otherwise.
"""

from __future__ import annotations

import math
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import warnings
import wave
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from reapy import reascript_api as RPR  # noqa: E402

from reaper_mcp.connection import records_undo, undo_top  # noqa: E402
from reaper_mcp.server import mcp  # noqa: E402

# UI state REAPER saves but does not undo.
IGNORED = ("<REAPER_PROJECT", "CURSOR", "ZOOM", "VZOOM", "SELECTION", "SELECTION2", "SEL ", "SCROLL")

BASE = """
local function name(tr, n) reaper.GetSetMediaTrackInfo_String(tr, "P_NAME", n, true) end
reaper.InsertTrackAtIndex(0, true); reaper.InsertTrackAtIndex(1, true)
local a, b = reaper.GetTrack(0, 0), reaper.GetTrack(0, 1)
name(a, "A"); name(b, "B")
reaper.TrackFX_AddByName(a, "ReaEQ", false, -1)
reaper.TrackFX_AddByName(a, "ReaComp", false, -1)
reaper.TrackFX_AddByName(reaper.GetMasterTrack(0), "ReaEQ", false, -1)
local it = reaper.CreateNewMIDIItemInProj(a, 0, 2)
local tk = reaper.GetActiveTake(it)
reaper.MIDI_InsertNote(tk, false, false, 0, 960, 0, 60, 100, true); reaper.MIDI_Sort(tk)
reaper.SetOnlyTrackSelected(b); reaper.SetEditCurPos(0, false, false)
reaper.InsertMedia([[WAV]], 0)
reaper.CreateTrackSend(a, b)
reaper.SetOnlyTrackSelected(a); reaper.Main_OnCommand(40406, 0); reaper.Main_OnCommand(40407, 0)
local env = reaper.GetTrackEnvelopeByName(a, "Volume")
reaper.InsertEnvelopePoint(env, 3.0, reaper.ScaleToEnvelopeMode(1, 0.5), 0, 0, false, false)
reaper.Envelope_SortPoints(env)
reaper.AddProjectMarker2(0, false, 1.0, 0, "M1", -1, 0)
reaper.AddProjectMarker2(0, true, 2.0, 3.0, "R1", -1, 0)
return reaper.CountTracks(0) .. " tracks, " .. reaper.CountMediaItems(0) .. " items"
"""


def plan(wav: str) -> list:
    """(tool, arguments) against the BASE project: tracks A and B, ReaEQ and ReaComp on A,
    a MIDI item on A, an audio item on B, a send A->B, volume and pan envelopes on A,
    ReaEQ on the master, a marker and a region."""
    return [
        ("set_tempo", {"bpm": 133}),
        ("set_time_signature", {"numerator": 3, "denominator": 4}),
        ("create_track", {"name": "C"}),
        ("delete_track", {"track_index": 1}),
        ("rename_track", {"track_index": 0, "name": "Renamed"}),
        ("set_track_volume", {"track_index": 0, "volume_db": -7.0}),
        ("set_track_pan", {"track_index": 0, "pan": 0.3}),
        ("set_track_pan", {"track_index": 0, "pan_mode": "stereo", "width": 0.5, "pan": -0.4}),
        ("set_track_mute", {"track_index": 0, "muted": True}),
        ("set_track_solo", {"track_index": 0, "soloed": True}),
        ("set_track_color", {"track_index": 0, "r": 200, "g": 30, "b": 30}),
        ("create_midi_item", {"track_index": 0, "start_position": 4.0, "length": 2.0}),
        ("add_midi_note", {"track_index": 0, "item_index": 0, "pitch": 64, "start": 0.5, "length": 0.5}),
        ("create_chord_progression", {"track_index": 0, "chords": "C, Am", "start_position": 4.0}),
        ("create_drum_pattern", {"track_index": 0, "pattern": "k.s.k.s.", "start_position": 4.0}),
        ("add_fx", {"track_index": 1, "fx_name": "ReaDelay"}),
        ("remove_fx", {"track_index": 0, "fx_index": 1}),
        ("set_fx_parameter", {"track_index": 0, "fx_index": 0, "param_index": 1, "value": 0.7}),
        ("bypass_fx", {"track_index": 0, "fx_index": 0, "bypassed": True}),
        ("load_fx_preset", {"track_index": 0, "fx_index": 0, "preset_name": "stock - Mud Free"}),
        ("move_fx", {"track_index": 0, "fx_index": 0, "to_index": 1}),
        ("import_audio_file", {"file_path": wav, "track_index": 1, "position": 4.0}),
        ("edit_audio_item", {"track_index": 1, "item_index": 0, "fade_in": 0.2}),
        ("adjust_pitch", {"track_index": 1, "item_index": 0, "semitones": 3}),
        ("adjust_playback_rate", {"track_index": 1, "item_index": 0, "rate": 1.25}),
        ("add_volume_automation", {"track_index": 0, "position": 1.0, "value_db": -6}),
        ("add_pan_automation", {"track_index": 0, "position": 1.0, "pan": 0.5}),
        ("create_send", {"source_track_index": 1, "dest_track_index": 0}),
        ("remove_send", {"source_track_index": 0, "send_index": 0}),
        ("set_send_volume", {"source_track_index": 0, "send_index": 0, "volume_db": -9}),
        ("set_send_routing", {"source_track_index": 0, "send_index": 0, "dest_channels": "3/4", "mode": "pre-fader", "mono": True}),
        ("create_bus", {"name": "Bus", "track_indices": [0, 1]}),
        ("add_master_fx", {"fx_name": "ReaComp"}),
        ("set_master_fx_parameter", {"fx_index": 0, "param_index": 1, "value": 0.6}),
        ("set_master_volume", {"volume_db": -3}),
        ("apply_mastering_chain", {"preset": "default"}),
        ("apply_limiter", {"threshold_db": -1.0, "release_ms": 80}),
        ("normalize_project", {"target_lufs": -20}),
        ("add_envelope_points", {"track_index": 0, "points": [{"time": 0.5, "db": -3}]}),
        ("clear_envelope_points", {"track_index": 0, "start": 2.5, "end": 3.5}),
        ("add_markers", {"entries": [{"position": 5.0, "name": "N"}]}),
        ("edit_markers", {"entries": [{"index": 0, "name": "M1x", "position": 1.5}]}),
        ("set_time_selection", {"start": 1.0, "end": 2.0}),
        ("edit_items", {"entries": [{"track_index": 0, "item_index": 0, "split_at": [1.0]}]}),
        ("edit_items", {"entries": [{"track_index": 1, "item_index": 0, "dest_track_index": 0, "position": 6.0}]}),
        ("edit_items", {"entries": [{"track_index": 1, "item_index": 0, "delete": True}]}),
    ]


def write_tone(path: Path) -> None:
    """Two seconds of a 220 Hz stereo sine, for import_audio_file and the render in normalize_project."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"".join(
            struct.pack("<hh", s, s)
            for s in (int(12000 * math.sin(2 * math.pi * 220 * i / 44100)) for i in range(2 * 44100))
        ))


def bridge(code: str) -> str:
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "bridge.py"), "--quiet", "--timeout", "15", "--code", code],
        capture_output=True, text=True,
    )
    if r.returncode:
        raise RuntimeError(f"bridge failed: {r.stdout}{r.stderr}")
    return r.stdout.strip()


def active() -> str:
    return RPR.EnumProjects(-1, "", 1024)[0]


def project_text(snapshot: Path) -> list:
    """The open project as saved text, without UI state and empty FX chains.

    A track brought back by Undo can carry an empty <FXCHAIN> block it did not have
    before; it holds no FX either way, so it is not a difference.
    """
    RPR.Main_SaveProjectEx(0, str(snapshot), 0)
    lines = [ln for ln in snapshot.read_text(encoding="utf-8", errors="replace").splitlines()
             if not ln.strip().startswith(IGNORED)]
    out, i = [], 0
    while i < len(lines):
        if lines[i].strip() == "<FXCHAIN":
            j = i + 1
            while j < len(lines) and lines[j].strip().split(" ")[0] in ("SHOW", "LASTSEL", "DOCKED"):
                j += 1
            if j < len(lines) and lines[j].strip() == ">":
                i = j + 1
                continue
        out.append(lines[i])
        i += 1
    return out


def close_tab(tab: str, project: Path) -> None:
    """Close the scratch tab without REAPER's save prompt, which would freeze every route."""
    if active() != tab:
        return
    RPR.Main_SaveProjectEx(0, str(project), 0)
    RPR.Main_openProject("noprompt:" + str(project))  # rebinds the tab and clears its dirty flag
    time.sleep(0.8)
    if active() == tab and not RPR.IsProjectDirty(0):
        RPR.Main_OnCommand(40860, 0)  # File: Close current project tab
        time.sleep(0.5)


@records_undo()
def _writes_volume() -> dict:
    """A recorded change, so the project matches its last undo step again."""
    RPR.SetMediaTrackInfo_Value(RPR.GetTrack(0, 0), "D_VOL", 0.25)
    return {"success": True}


@records_undo()
def _writes_nothing() -> dict:
    """Reports success without touching REAPER, as a reapy attribute assignment does."""
    return {"success": True}


def main() -> int:
    only = set(sys.argv[1:])
    work = Path(tempfile.mkdtemp(prefix="reaper-undo-check-"))
    wav, project, snapshot = work / "tone.wav", work / "undo-check.rpp", work / "snapshot.rpp"
    write_tone(wav)
    tests = [t for t in plan(str(wav)) if not only or t[0] in only]

    user_tab = active()
    RPR.Main_OnCommand(40859, 0)  # File: New project tab
    time.sleep(0.5)
    tab = active()
    if tab == user_tab:
        print("REAPER did not open a new project tab; refusing to run against the open project.")
        return 1

    failures, backup = [], None
    try:
        print("fixture:", bridge(BASE.replace("WAV", str(wav))))
        RPR.Main_SaveProjectEx(0, str(project), 0)
        RPR.Main_openProject("noprompt:" + str(project))
        time.sleep(0.8)
        if active() != tab or RPR.IsProjectDirty(0):
            raise RuntimeError("could not bind the scratch tab to its file")

        for tool, args in tests:
            if active() != tab:
                raise RuntimeError("the active project tab changed during the run")
            before = project_text(snapshot)
            started = time.perf_counter()
            result = mcp._tool_manager.get_tool(tool).fn(**args)
            ms = (time.perf_counter() - started) * 1000
            top = undo_top()
            backup = backup or result.get("backup")
            RPR.Undo_DoUndo2(0)
            time.sleep(0.1)
            after = project_text(snapshot)
            problems = []
            if not result.get("success"):
                problems.append(f"reported failure: {result.get('error')}")
            if "unconfirmed" in result:
                problems.append("reply is unconfirmed")
            if top != "MCP: " + tool:
                problems.append(f"top undo step is {top!r}")
            if before != after:
                added = [ln.strip() for ln in after if ln not in before][:3]
                removed = [ln.strip() for ln in before if ln not in after][:3]
                problems.append(f"one undo left the project different: +{added} -{removed}")
            print(f"{'ok  ' if not problems else 'FAIL'}  {tool:26} {ms:7.0f} ms  {'; '.join(problems)}")
            if problems:
                failures.append(tool)

        if not only:
            ok = bool(backup) and Path(backup).is_file()
            print(f"{'ok  ' if ok else 'FAIL'}  backup before the first change  {backup}")
            if not ok:
                failures.append("backup")
            # REAPER compares the whole project with the last undo step, so a change left
            # unrecorded (a selection, a raw call) would give the empty call a step of its own.
            _writes_volume()
            result = _writes_nothing()
            ok = "unconfirmed" in result
            print(f"{'ok  ' if ok else 'FAIL'}  a write that changes nothing is unconfirmed")
            if not ok:
                failures.append("unconfirmed")
    finally:
        close_tab(tab, project)
        RPR.SelectProjectInstance(user_tab)
        time.sleep(0.3)
        shutil.rmtree(work, ignore_errors=True)

    print(f"\n{len(tests) - len([f for f in failures if f not in ('backup', 'unconfirmed')])}/{len(tests)} tools pass"
          + (f"; failed: {', '.join(failures)}" if failures else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
