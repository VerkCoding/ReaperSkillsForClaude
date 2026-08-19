import logging

import reapy
from reapy import reascript_api as RPR

from reaper_mcp.connection import get_project
from reaper_mcp.units import project_tempo

logger = logging.getLogger("reaper_mcp.midi_tools")

# General MIDI standard drum mappings for note assignments.
DRUM_MAPPINGS = {
    "k": 36,
    "s": 38,
    "h": 42,
    "o": 46,
    "t": 41,
    "m": 45,
    "f": 48,
    "c": 49,
    "r": 51,
}

CHORD_TYPES = {
    "maj":   [0, 4, 7],
    "min":   [0, 3, 7],
    "m":     [0, 3, 7],
    "dim":   [0, 3, 6],
    "aug":   [0, 4, 8],
    "maj7":  [0, 4, 7, 11],
    "min7":  [0, 3, 7, 10],
    "m7":    [0, 3, 7, 10],
    "7":     [0, 4, 7, 10],
    "dom7":  [0, 4, 7, 10],
    "dim7":  [0, 3, 6, 9],
    "hdim7": [0, 3, 6, 10],
    "sus2":  [0, 2, 7],
    "sus4":  [0, 5, 7],
}

NOTE_TO_NUMBER = {
    "C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3,
    "E": 4, "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8,
    "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11,
}


def _parse_chord(chord_str: str):
    """Returns intervals and root semitone to calculate MIDI note numbers from a chord string.

    Raises ValueError when the root or the chord type is unrecognised. Falling back
    to a default turns a typo into a C major triad that sounds plausible and is
    silently wrong.
    """
    chord_str = chord_str.strip()
    if len(chord_str) >= 2 and chord_str[1] in ("#", "b"):
        root = chord_str[:2]
        chord_type = chord_str[2:] or "maj"
    else:
        root = chord_str[:1]
        chord_type = chord_str[1:] or "maj"
    if root not in NOTE_TO_NUMBER:
        raise ValueError("unknown root note '%s'" % root)
    if chord_type not in CHORD_TYPES:
        raise ValueError("unknown chord type '%s'" % chord_type)
    return CHORD_TYPES[chord_type], NOTE_TO_NUMBER[root]


def _item_index(track, item) -> int:
    """Return the index REAPER uses for an item on its track.

    Items are ordered by position, so a newly added item is last only when it starts
    after every existing one. Reporting n_items - 1 otherwise names a different item,
    and notes addressed to that index land in the wrong place.
    """
    for i in range(track.n_items):
        if track.items[i].id == item.id:
            return i
    return track.n_items - 1


def _out_of_range(**values) -> str:
    """Return a message naming the first value outside its MIDI range, or "".

    MIDI stores these fields in seven bits, so REAPER silently keeps the low bits of
    anything larger: a pitch of 200 becomes 72 and a velocity of 300 becomes 44.
    """
    limits = {"pitch": (0, 127), "velocity": (0, 127), "channel": (0, 15)}
    for name, value in values.items():
        low, high = limits[name]
        if not low <= value <= high:
            return "%s must be %d-%d, got %s" % (name, low, high, value)
    return ""


def register_tools(mcp):

    @mcp.tool()
    def create_midi_item(track_index: int, start_position: float, length: float) -> dict:
        """Creates an empty MIDI item to provide a container for MIDI note insertion."""
        try:
            if length <= 0:
                return {"success": False, "error": f"length must be positive, got {length}"}
            project = get_project()
            track = project.tracks[track_index]
            item = track.add_midi_item(start_position, start_position + length)
            return {
                "success": True,
                "item_id": item.id,
                "item_index": _item_index(track, item),
                "position": item.position,
                "length": item.length,
                "track_index": track_index,
            }
        except Exception as e:
            logger.error(f"create_midi_item failed: {e}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def add_midi_note(
        track_index: int,
        item_index: int,
        pitch: int,
        start: float,
        length: float,
        velocity: int = 100,
        channel: int = 0,
    ) -> dict:
        """Inserts a MIDI note event into a specified item. Provides direct access to individual note properties."""
        try:
            invalid = _out_of_range(pitch=pitch, velocity=velocity, channel=channel)
            if invalid:
                return {"success": False, "error": invalid}
            if length <= 0:
                return {"success": False, "error": f"length must be positive, got {length}"}
            project = get_project()
            track = project.tracks[track_index]
            item = track.items[item_index]
            take = item.active_take
            if not take.is_midi:
                return {"success": False, "error": "Item is not a MIDI item"}
            take.add_note(
                start=start,
                end=start + length,
                pitch=pitch,
                velocity=velocity,
                channel=channel,
            )
            return {
                "success": True,
                "track_index": track_index,
                "item_index": item_index,
                "pitch": pitch,
                "start": start,
                "length": length,
                "velocity": velocity,
                "channel": channel,
            }
        except Exception as e:
            logger.error(f"add_midi_note failed: {e}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def create_chord_progression(
        track_index: int,
        chords: str,
        start_position: float,
        beats_per_chord: int = 4,
    ) -> dict:
        """Generates a sequence of chords within a single MIDI item. Requires comma-separated chord names to determine note intervals."""
        try:
            project = get_project()
            track = project.tracks[track_index]

            if beats_per_chord <= 0:
                return {
                    "success": False,
                    "error": f"beats_per_chord must be positive, got {beats_per_chord}",
                }
            chord_list = [c.strip() for c in chords.split(",") if c.strip()]
            if not chord_list:
                return {"success": False, "error": "chords must name at least one chord"}

            # Every chord is parsed before the item exists. Parsing inside the write
            # loop left a half-filled item behind when a name did not resolve.
            parsed = []
            for chord_str in chord_list:
                try:
                    intervals, root_num = _parse_chord(chord_str)
                except ValueError as e:
                    return {"success": False, "error": f"chord '{chord_str}': {e}"}
                parsed.append((chord_str, intervals, root_num))

            # project_tempo reads quarter-note BPM. Reapy's Project.bpm reports the
            # denominator-scaled setting, which halved every duration in x/8.
            seconds_per_beat = 60.0 / project_tempo()
            chord_length = seconds_per_beat * beats_per_chord
            total_length = chord_length * len(chord_list)

            item = track.add_midi_item(start_position, start_position + total_length)
            take = item.active_take
            added_chords = []

            for i, (chord_str, intervals, root_num) in enumerate(parsed):
                chord_start = i * chord_length
                for interval in intervals:
                    note_num = 60 + root_num + interval
                    take.add_note(
                        start=chord_start,
                        end=chord_start + chord_length * 0.95,
                        pitch=note_num,
                        velocity=80,
                        channel=0,
                    )
                added_chords.append({
                    "chord": chord_str,
                    "position": chord_start,
                    "length": chord_length,
                })

            return {
                "success": True,
                "item_id": item.id,
                "item_index": _item_index(track, item),
                "chords": added_chords,
                "start_position": start_position,
                "total_length": total_length,
            }
        except Exception as e:
            logger.error(f"create_chord_progression failed: {e}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def create_drum_pattern(
        track_index: int,
        pattern: str,
        start_position: float,
        beats: int = 4,
        repeats: int = 1,
    ) -> dict:
        """Constructs a drum sequence based on step-sequencer character mapping. Places events on MIDI channel 9 to comply with General MIDI drum standards."""
        try:
            project = get_project()
            track = project.tracks[track_index]

            # Validated before the item is created. An empty pattern previously
            # divided by zero after the item had already been added to the track.
            if not pattern:
                return {"success": False, "error": "pattern must contain at least one step"}
            if beats <= 0:
                return {"success": False, "error": f"beats must be positive, got {beats}"}
            if repeats < 1:
                return {"success": False, "error": f"repeats must be at least 1, got {repeats}"}

            # project_tempo reads quarter-note BPM. Reapy's Project.bpm reports the
            # denominator-scaled setting, which halved every duration in x/8.
            seconds_per_beat = 60.0 / project_tempo()
            pattern_length = seconds_per_beat * beats
            total_length = pattern_length * repeats

            item = track.add_midi_item(start_position, start_position + total_length)
            take = item.active_take
            time_per_step = pattern_length / len(pattern)
            notes_placed = 0

            for repeat in range(repeats):
                offset = repeat * pattern_length
                for i, char in enumerate(pattern):
                    if char in DRUM_MAPPINGS:
                        note_start = offset + i * time_per_step
                        take.add_note(
                            start=note_start,
                            end=note_start + time_per_step * 0.5,
                            pitch=DRUM_MAPPINGS[char],
                            velocity=100,
                            channel=9,
                        )
                        notes_placed += 1

            return {
                "success": True,
                "item_id": item.id,
                "item_index": _item_index(track, item),
                "notes_placed": notes_placed,
                "pattern": pattern,
                "repeats": repeats,
                "start_position": start_position,
                "total_length": total_length,
            }
        except Exception as e:
            logger.error(f"create_drum_pattern failed: {e}")
            return {"success": False, "error": str(e)}
