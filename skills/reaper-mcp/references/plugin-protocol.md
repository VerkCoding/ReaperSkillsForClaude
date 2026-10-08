# Plugin protocol

One procedure for every exchange with a plugin, whatever its vendor or format. It starts from what most plugins do, measured, and leaves that default only when a check says so. A plugin met for the first time is then handled by the same steps as a familiar one, and what it teaches goes into the registry in [Plugin Control](./plugin-control.md#registry-of-measured-plugins) instead of being relearned next time.

Every step is weighed three ways, P/P/S:

- **Price**: the time it costs, measured on REAPER 7.82 under Windows.
- **Payoff**: what it secures towards a confirmed exchange, meaning that REAPER holds the value intended and the plugin shows it.
- **Stability**: what it protects: the user's project, undo history, preferences and REAPER's responsiveness.

The cheap steps run every time. The expensive ones run only when an earlier step or the stakes call for them.

## Contents

- [The protocol at a glance](#the-protocol-at-a-glance)
- [The base: what most plugins do](#the-base-what-most-plugins-do)
- [Step 0: address the instance](#step-0-address-the-instance)
- [Step 1: find the parameter by name](#step-1-find-the-parameter-by-name)
- [Step 2: classify the parameter](#step-2-classify-the-parameter)
- [Step 3: turn the intent into one normalized value](#step-3-turn-the-intent-into-one-normalized-value)
- [Step 4: write once, through the tool](#step-4-write-once-through-the-tool)
- [Step 5: verify at the level the decision needs](#step-5-verify-at-the-level-the-decision-needs)
- [Step 6: record what the plugin taught](#step-6-record-what-the-plugin-taught)
- [When the base does not hold](#when-the-base-does-not-hold)
- [Hard rules](#hard-rules)
- [Plugin structures](#plugin-structures)
- [Price list](#price-list)

## The protocol at a glance

| Step | Do | Price | Payoff | Stability |
|---|---|---|---|---|
| 0 Address | Track and FX index from `list_track_fx`; check the name before each write | 1 call, ~0.2 s | the right instance | no write lands on a renamed or shifted FX |
| 1 Find | `get_fx_parameters` with `name_contains` | 0.2 s; 1.4 s on an 876-parameter plugin | the right index, not one remembered from another version | no dump of 2,000 padding parameters into context |
| 2 Classify | Read `steps`, `step_index`, `step_label` and the display from the same reply | free | toggle, list or continuous, and the true current setting | no "fix" of a setting that only looked wrong |
| 3 Translate | Formula for toggles and lists, registry mapping, else a read-only search, else a search on a scratch instance | 0 / ~0.2 s / ~1.3 s | one normalized value for the intent | nothing is written to the user's instance while searching |
| 4 Write | `set_fx_parameter` once | ~0.35 s; up to +1 s for a plugin that takes writes late; about +1 s (once 8.8 s) when the audio engine must open | REAPER holds the value | one undo step; engine and preferences put back |
| 5 Verify | The reply always; the display by default; a render only for sound decisions | free / ~0.2 s / ~1 s, or ~18 s while the audio engine runs, plus the audio | the plugin shows the intended value | a wrong write is caught before the next one builds on it |
| 6 Record | One registry row when the plugin left the base | a minute, once | the next session starts from the known behaviour | — |

## The base: what most plugins do

Measured on 17 plugins in one mixing project and in a scratch tab: Ozone 12 and Nectar 4 (iZotope), soothe2 (oeksound), SSL G-Channel, Scheps Omni Channel 2 and GTR Amp (Waves), DeNoise 2 (Acon Digital), smart:chain (sonible), Rev SPRING-636 (Arturia), kHs Reverb (Kilohearts), Pure Plate (UADx), FutureVerb (Valhalla), SPAN Plus (Voxengo), Youlean Loudness Meter 2, reVUe (CLAP, Blenheim Sound), JS: Loudness Meter and ReaEQ (Cockos).

| Fact | Count | So the default is |
|---|---|---|
| REAPER appends `Bypass`, `Wet`, `Delta` as the last three parameters | 17 of 17 | ignore the last three when looking for the plugin's own controls |
| Real parameters come first; padding, if any, follows | every plugin with padding | read from the start; the first page holds the controls |
| Padding: VST3 MIDI CC mapping, 16 channels × 130 = 2,080 entries ("MIDI", "MIDI CC helper", "MIDI CC #\|#"), or Waves' 130 entries with an empty display | 6 of 17 | filter by name; never list a whole plugin |
| A write is taken at once, in the same Lua chunk | 12 of 16 tested (one of them snaps to its own steps) | expect the value immediately |
| A write is taken only in the audio callback | 3 of 16: Ozone 12, Nectar 4, reVUe | handled by the tool; see step 4 |
| A write is ignored | 1 of 16: Youlean's preset selector | read back before trusting a write |
| `TrackFX_FormatParamValueNormalized` formats any value without setting it | 13 of the 15 plugins with continuous controls; ValhallaFutureVerb (and Scheps Omni's `Insert` entries) return the current display whatever is asked | search without writing; detect the exception by f(0) = f(1) |
| The two display paths agree on the number but not always on units ("450" against "450 Hz", "5.0 dB dB") | 285 of 7,317 formatted parameters, in 5 plugins (Ozone 12, soothe2, DeNoise 2, Nectar 4, Arturia) | compare numbers, never strings |
| Displays carry kilo notation ("2k5"), unit changes inside the range ("500 ms" to "30.0 s") or a U+202F space before the unit | soothe2; kHs Reverb (both) | parse with the tested `num()` in [Plugin Control](./plugin-control.md#searching-without-writing) |
| A list parameter maps a normalized value to an entry by rounding | 8 plugins; truncating 1 (smart:chain); VST3 SDK floor(v·(steps+1)) 1 (Arturia) | read and write list entries at `index / steps` plus a nudge, which all three agree on |
| JS reports step sizes in native units, not 0-1 | the JS plugin | steps = native range / step |
| Loading a plugin | 0.01-0.65 s; Ozone 12 3.6 s | budget for it when adding FX |

## Step 0: address the instance

Take the track index and FX index from `list_track_fx` (or `list_master_fx`), and check that the FX name at that slot is the expected one immediately before writing. Never find an FX by name with `TrackFX_AddByName` or a name search: a renamed FX matches nothing, and the add call creates a duplicate.

Price: one call, about 0.2 s. Payoff: the write reaches the intended instance. Stability: an FX chain the user reordered or renamed since the last read cannot redirect the write.

## Step 1: find the parameter by name

Call `get_fx_parameters` with `name_contains` (for Ozone, the module prefix: `MAX:`, `EQ:`, `VCOMP:`). The default page is 200 parameters; `next_start` continues it. Take the index from the reply, not from memory or the registry: soothe2 moved its sidechain switch from 51 to 54 between versions. The registry tells you which name to look for.

Skip REAPER's last three parameters (use `bypass_fx` to bypass; `Wet` and `Delta` are REAPER's per-FX mix and delta, not the plugin's own controls) and padding: names containing "MIDI", "MPE_", "VST_ProgramChange", "Control" followed by a number, or an empty display.

Price: 0.2 s for a small plugin, 1.4 s for Ozone 12 filtered across its 876 parameters. Payoff: the right index for this plugin version. Stability: thousands of padding entries stay out of the context.

## Step 2: classify the parameter

The reply from step 1 already says what kind of parameter it is:

| Kind | How it shows | Write it as |
|---|---|---|
| Toggle | a two-state display such as On/Off; REAPER reports it as a toggle | 0 or 1 |
| List | `steps` and `step_index` present, `steps` up to a few dozen | an entry index, step 3b |
| Quantized number | `steps` in the hundreds, numeric display (a JS slider) | a value, step 3c or 3d |
| Continuous | no `steps` | a value, step 3c or 3d |

When `step_label` is present, the plugin labels its own setting as the neighbouring entry; `step_label` is the real one. Report it as such, and confirm with the user in the plugin window before proposing to change it: smart:chain's "Synth | Pad" was a correct "Vocals | High".

Price: nothing beyond step 1. Payoff: the true current setting. Stability: a correct setting is not "fixed" on the strength of a wrong label.

## Step 3: turn the intent into one normalized value

Take the first route that applies; each is cheaper and safer than the next.

| Route | When | How | Price |
|---|---|---|---|
| 3a Toggle | a toggle | 0 or 1 | none |
| 3b List entry | a list | `index / steps + nudge`, with nudge = min(1e-5, 0.5 / (steps × (steps + 1))) (`fx_tools.step_probe`) | none |
| 3c Known mapping | the registry gives a formula | the formula | none |
| 3d Read-only search | anything with a numeric display that the plugin formats for any value | `find_norm` in one bridge command: bisects on `TrackFX_FormatParamValueNormalized`, compares numbers, takes the middle of the values that display the target; code in [Plugin Control](./plugin-control.md#searching-without-writing) | ~0.2 s |
| 3e Scratch-instance search | `find_norm` reports that the plugin formats only its current value | the same search by writing, on a fresh instance in a scratch tab, then one write to the user's instance; [Plugin Control](./plugin-control.md#searching-on-a-scratch-instance) | ~1.3 s with the plugin load |

Writing a list entry as exactly `index / steps` can land one entry low on a truncating plugin when the stored float falls a hair short; the nudge clears that, and stays inside the entry for rounding and VST3-SDK plugins too.

Measured with `find_norm`: Ozone 12 Input Gain 12.40 dB = 0.620000, DeNoise 2 Reduction 5.0 dB = 0.666667, soothe2 450 Hz, kHs Reverb Decay 2.20 s, Nectar 4 Delay 250 ms; with the scratch instance: ValhallaFutureVerb Mix 30 % in 33 writes and 1.3 s.

Payoff: one value that means the intent. Stability: no route writes to the user's instance while searching, so the user's sound never sweeps through a search.

## Step 4: write once, through the tool

Write with `set_fx_parameter` (`set_master_fx_parameter` on the master), once. Since 1.3.1 the tool:

1. reads the value before writing and reports `unconfirmed` when it is already set;
2. writes and reads back in the same cycle, which settles 12 of 16 plugins at once;
3. otherwise follows the value across cycles for up to a second, and if it has not moved while REAPER's audio engine is closed, opens the engine with `Audio_Init`, waits for the value, and closes the engine again with `Audio_Quit`;
4. records one `MCP: <tool>` undo step once REAPER holds the new value;
5. says in `note` when the write landed late, needed the engine, or was snapped to the plugin's own steps.

Do not write a plugin parameter through the bridge when the tool can: the bridge does none of the above. Do not retry a write that came back `unconfirmed` without reading first: a write may still be waiting, and a second value would land after it.

Price: about 0.35 s for a plugin that takes writes at once; up to a second more for one that takes them in its audio callback; about 1 s more, once 8.8 s, when the audio engine has to open, depending on the driver. Payoff: REAPER holds the value. Stability: one undo step that restores the previous value, and the engine left as the user's preferences had it.

## Step 5: verify at the level the decision needs

| Level | Check | Price | Use for |
|---|---|---|---|
| L1 | The reply's `value` equals the intended normalized value, no `unconfirmed`, `note` read | free | every write |
| L2 | `get_fx_parameters` with `name_contains` again: the display, or `step_label`, shows the intended setting | ~0.2 s | the default after every write |
| L3 | Render and measure the sound | ~1 s with the audio engine closed, ~18 s while it runs, plus the audio ([why](./rendering.md#the-audio-device-around-a-render-normal-not-a-fault)) | decisions judged by sound: level, tone, dynamics |

A value can be held and displayed while the sound does not change: a band that is not in use, a module that is bypassed, a gate in front of it. When L3 disagrees with L2, look for the enable switch before changing the value again.

## Step 6: record what the plugin taught

When a plugin leaves the base, add or extend its row in the [registry](./plugin-control.md#registry-of-measured-plugins): parameter count and padding, list mapping, write behaviour, display quirks, and anything only the user can do in its window. Record measured facts with the REAPER and plugin version, and correct a row the moment a new test contradicts it.

Price: a minute, once per plugin. Payoff: the next session treats the plugin as known. Stability: wrong lessons do not outlive the evidence against them; the Ozone lesson was corrected twice in one session.

## When the base does not hold

| Symptom | Likely cause | Do |
|---|---|---|
| `note`: applied N ms late, or the engine was opened | the plugin takes writes in its audio callback | nothing; add it to the registry |
| `unconfirmed`: the value stayed for a second | the parameter ignores host writes (a preset selector, a meter readout) or is driven by the plugin's own state | do not retry; check whether it is an interface-only control and ask the user |
| `note`: the plugin stored another value | the plugin snaps to its own steps | accept the stored value, or write the nearest step |
| `step_label` present | the plugin labels the stored value as the neighbouring entry | report `step_label`; confirm with the user before changing it |
| Empty display or empty formatting | padding, or a meter output | the wrong parameter: filter again |
| `find_norm`: the plugin formats only its current value | the plugin ignores the value asked for when formatting (ValhallaFutureVerb) | route 3e, a scratch instance |
| A bridge command fails with `UnicodeEncodeError` after REAPER ran it | a plugin returned text outside the console's code page (U+202F); fixed in `bridge.py` 1.4.0 | set `PYTHONIOENCODING=utf-8` with older versions |
| The display changes but the sound does not | the band, module or section is not enabled | find and set the enable parameter first; for FabFilter write Used and Enabled before the band's values |
| The value shows only after the user clicks into REAPER | a write waited for the audio engine, outside the tools | read it back; with 1.3.0 tools this was normal |

## Hard rules

These protect the user and hold on every route, tool or bridge:

1. Change only the active project, after confirming it is the one intended. Never close or save the user's project.
2. Never sweep a parameter of the user's project to search for a value. Search read-only (3d), or by writing on a scratch instance (3e).
3. Leave REAPER's preferences as the user set them, `offlineinact` and `audiocloseinactive` included, and put back anything borrowed: the audio engine, solos, render settings, the time selection.
4. Do not open a plugin window to make a write work. A window brings REAPER to the front, changes the engine state and adds a "Close FX config" undo step. Open one only when the user asks.
5. Learning, preset browsing and settings the plugin does not expose belong to the user in the plugin's own window. Name the step and wait.
6. One logical change per write and per undo step, measured before the next one ([audio-mixing.md](../../reaper-audio-engineer/references/audio-mixing.md), the basic loop).

## Plugin structures

What to expect inside a plugin, so a new one is recognised rather than studied:

- **Formats.** VST3 parameters are normalized 0-1; most take writes at once, some only in the audio callback, and their list mappings differ (rounding, truncating, SDK). JS parameters live in native units, report steps in native units, and snap writes to their steps. The one CLAP plugin measured took writes only in the audio callback. Stock Cockos plugins take writes at once.
- **Layouts.** Module prefixes (Ozone: `EQ:`, `MAX:`, `DYNEQ:`), bands as a flat array with a fixed stride (Pro-Q 4: 23 per band), blocks of identical names (Scheps Omni: 1,629 `Insert` entries), a plugin's own `Bypass` beside REAPER's appended one, and padding at the end.
- **Gating.** A value only matters when its band or module is on: FabFilter's Used and Enabled, Ozone's `<MODULE>: Bypass`.
- **Displays.** Units added or dropped between the two display paths, doubled units, units that change inside the range (ms to s, Hz to "1k" and "2k5"), ranges that run backwards (ReaLimit release), non-ASCII characters such as U+202F.
- **Hidden state.** Settings with no parameter (Ozone's True Peak switch), analysis and learn states (smart:chain), presets.

## Price list

| Operation | Time |
|---|---|
| One reapy call outside a held block | ~60 ms |
| One MCP tool call (most tools) | 0.15-0.6 s |
| One bridge command, however many API calls inside | 0.1-0.3 s |
| `get_fx_parameters`, small plugin / Ozone 12 filtered | 0.2 s / 1.4 s |
| Read-only survey of every parameter of 17 plugins, one bridge command | 0.85 s |
| `find_norm`, read-only search for one value | ~0.2 s |
| Scratch-instance search, including tab, plugin load and clean-up | ~1.3 s |
| `set_fx_parameter`, plugin that takes writes at once | ~0.35 s |
| Extra for a plugin that takes writes in its audio callback | up to 1 s |
| Opening the audio engine (`Audio_Init`) | about 1 s, once 8.8 s |
| Loading a plugin / Ozone 12 | 0.01-0.65 s / 3.6 s |
| Any render, audio engine closed and settled / closed moments ago / running | ~1 s / ~10 s / ~18 s, plus the audio |

The render overhead is normal: REAPER stops and restarts the audio device around every render, and the driver sets the price ([Rendering](./rendering.md#the-audio-device-around-a-render-normal-not-a-fault)). It makes L3 the one expensive check: batch sound decisions so one render answers several of them.
