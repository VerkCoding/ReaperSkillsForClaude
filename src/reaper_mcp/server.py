import logging
from mcp.server.fastmcp import FastMCP

logger = logging.getLogger("reaper_mcp.server")

mcp = FastMCP("reaper-mcp")

# Import each tool module's register_tools function and call it with the mcp instance.
# The imports must happen after mcp is created to avoid circular dependencies.
from reaper_mcp.project_tools import register_tools as _reg_project
from reaper_mcp.track_tools import register_tools as _reg_track
from reaper_mcp.midi_tools import register_tools as _reg_midi
from reaper_mcp.fx_tools import register_tools as _reg_fx
from reaper_mcp.audio_tools import register_tools as _reg_audio
from reaper_mcp.mixing_tools import register_tools as _reg_mixing
from reaper_mcp.render_tools import register_tools as _reg_render
from reaper_mcp.mastering_tools import register_tools as _reg_mastering
from reaper_mcp.analysis_tools import register_tools as _reg_analysis

_reg_project(mcp)
_reg_track(mcp)
_reg_midi(mcp)
_reg_fx(mcp)
_reg_audio(mcp)
_reg_mixing(mcp)
_reg_render(mcp)
_reg_mastering(mcp)
_reg_analysis(mcp)
