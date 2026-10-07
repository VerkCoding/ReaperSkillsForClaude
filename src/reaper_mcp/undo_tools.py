"""Undo and redo through REAPER's own undo history.

Every tool that changes the project records one step named "MCP: <tool>"
(records_undo in connection.py), and each Lua bridge command records
"Claude bridge command". Transport, cursor, render and project-file tools
record none, as REAPER does not keep those in undo history.
"""

import logging

from reaper_mcp.connection import RPR, UNDO_PREFIX, _step_name, get_project, held, records_undo

logger = logging.getLogger("reaper_mcp.undo_tools")

_MAX_STEPS = 100
_OWN_STEPS = (UNDO_PREFIX, "Claude bridge command")


def register_tools(mcp):

    @mcp.tool()
    @records_undo(own_step=True, verify=False)
    def undo(steps: int = 1, redo: bool = False, any_step: bool = False) -> dict:
        """Undo (or redo=true) the last steps of REAPER's undo history and name each one.

        Every tool that changes the project is one step, "MCP: <tool>"; transport, render and file tools are none.
        Stops at a step neither these tools nor the bridge made, i.e. the user's own edit, unless any_step=true.
        """
        try:
            if not 1 <= steps <= _MAX_STEPS:
                return {"success": False, "error": f"steps must be 1-{_MAX_STEPS}, got {steps}"}
            get_project()
            act = RPR.Undo_DoRedo2 if redo else RPR.Undo_DoUndo2
            done, stopped_at, error = [], None, None
            with held():
                for _ in range(steps):
                    name = _step_name(redo)
                    if not name:
                        break
                    if not any_step and not name.startswith(_OWN_STEPS):
                        stopped_at = name
                        break
                    if not act(0):
                        error = f"REAPER refused to {'redo' if redo else 'undo'} '{name}'"
                        break
                    # The step just undone becomes the next redo (and the reverse), which
                    # confirms REAPER moved through the history rather than reporting it did.
                    if _step_name(not redo) != name:
                        error = f"after '{name}' the history reads '{_step_name(not redo)}'"
                        break
                    done.append(name)
                next_undo, next_redo = _step_name(False), _step_name(True)

            result = {
                "success": bool(done) and error is None,
                "redone" if redo else "undone": done,
                "next_undo": next_undo,
                "next_redo": next_redo,
            }
            if stopped_at is not None:
                result["stopped_at"] = stopped_at
                result["note"] = (
                    f"'{stopped_at}' was not made by these tools or the bridge; "
                    "pass any_step=true only if the user asked to undo it"
                )
            if error:
                result["error"] = error
            elif not done and stopped_at is None:
                result["error"] = f"nothing to {'redo' if redo else 'undo'}"
            elif not done:
                result["error"] = result.pop("note")
            return result
        except Exception as e:
            logger.error(f"undo failed: {e}")
            return {"success": False, "error": str(e)}
