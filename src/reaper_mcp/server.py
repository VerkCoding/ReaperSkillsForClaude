import logging
from mcp.server.fastmcp import FastMCP

logger = logging.getLogger("reaper_mcp.server")

# Sent to the client on connect, so the route rule holds even when the
# reaper-mcp skill is not loaded. The full rule lives in that skill.
INSTRUCTIONS = (
    "Use these tools first. Use the Lua file bridge (reaper-mcp skill) only when no tool "
    "covers the task, to batch more than about three reads, or to read back a value a tool "
    "reported. Before changing the project through the bridge, tell the user and name what "
    "the tools lack. Never run tool calls and bridge calls in parallel. After the bridge adds, "
    "deletes or moves tracks, FX or sends, call list_tracks before the next tool that takes an "
    "index. success: true is a claim, not proof: read values that matter back."
)

mcp = FastMCP("reaper-mcp", instructions=INSTRUCTIONS)

# Delayed imports prevent circular dependencies during mcp instantiation.
from reaper_mcp.project_tools import register_tools as _reg_project
from reaper_mcp.track_tools import register_tools as _reg_track
from reaper_mcp.midi_tools import register_tools as _reg_midi
from reaper_mcp.fx_tools import register_tools as _reg_fx
from reaper_mcp.audio_tools import register_tools as _reg_audio
from reaper_mcp.mixing_tools import register_tools as _reg_mixing
from reaper_mcp.render_tools import register_tools as _reg_render
from reaper_mcp.mastering_tools import register_tools as _reg_mastering
from reaper_mcp.analysis_tools import register_tools as _reg_analysis
from reaper_mcp.envelope_tools import register_tools as _reg_envelope
from reaper_mcp.marker_tools import register_tools as _reg_marker
from reaper_mcp.item_tools import register_tools as _reg_item
from reaper_mcp.undo_tools import register_tools as _reg_undo

_reg_project(mcp)
_reg_track(mcp)
_reg_midi(mcp)
_reg_fx(mcp)
_reg_audio(mcp)
_reg_mixing(mcp)
_reg_render(mcp)
_reg_mastering(mcp)
_reg_analysis(mcp)
_reg_envelope(mcp)
_reg_marker(mcp)
_reg_item(mcp)
_reg_undo(mcp)
