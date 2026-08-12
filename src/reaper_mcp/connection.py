"""Connection management for the REAPER distant API.

The previous version of this module cached a module-level ``_connected = True``
and never revisited it. That is fine until REAPER restarts: the flag stays True,
``reapy.connect()`` is never called again, and every subsequent tool call fails
against a dead socket with an opaque ``WinError 10053``. Recovering meant
restarting Claude.

This version treats the connection as disposable. Every ``get_project()`` makes
a cheap round trip to prove the socket is alive, and reconnects once if it
isn't. A REAPER restart mid-session now costs one retry instead of a session.
"""

import logging
import threading

logger = logging.getLogger("reaper_mcp.connection")

_lock = threading.RLock()
_connected = False

REAPY_SERVER_PORT = 2306
WEB_INTERFACE_PORT = 2307

_SETUP_HINT = (
    "Make sure REAPER is running and the distant API is enabled.\n"
    "To enable it, close REAPER and run:\n"
    "    python reaper/enable_reapy.py\n"
    "or, from inside REAPER: Actions > Show action list > ReaScript: Run... > "
    "enable_reapy.py, then restart REAPER.\n"
    f"The reapy server listens on port {REAPY_SERVER_PORT}; REAPER's web "
    f"interface must be on port {WEB_INTERFACE_PORT}. If a web interface was "
    f"also added on {REAPY_SERVER_PORT}, it squats on the server's port - run "
    "`python reaper/enable_reapy.py --repair` to remove it."
)


def _import_reapy():
    """Import reapy, converting the failure into something actionable.

    reapy does not support Python 3.13+, and the resulting ImportError names an
    internal module rather than the real problem, so translate it here.
    """
    try:
        import reapy  # noqa: PLC0415
        return reapy
    except Exception as e:  # ImportError, SyntaxError on 3.13+, ...
        import sys
        v = f"{sys.version_info.major}.{sys.version_info.minor}"
        extra = (
            f" You are on Python {v}; python-reapy requires 3.11 or 3.12."
            if sys.version_info >= (3, 13) else ""
        )
        raise RuntimeError(f"Could not import reapy: {e}.{extra}") from e


def _connect(reapy) -> None:
    try:
        reapy.connect()
    except Exception as e:
        raise RuntimeError(f"Cannot connect to REAPER: {e}\n{_SETUP_HINT}") from e


def _ensure_connected(reapy) -> None:
    global _connected
    with _lock:
        if _connected:
            return
        _connect(reapy)
        _connected = True
        logger.info("Connected to REAPER")


def _reset() -> None:
    global _connected
    with _lock:
        _connected = False


def get_project():
    """Return the active REAPER project, reconnecting if the socket died.

    The ``n_tracks`` read is not decorative: it forces an actual round trip.
    ``reapy.Project()`` alone can hand back an object that only fails later,
    deep inside a tool, where the error is much harder to attribute.
    """
    reapy = _import_reapy()

    for attempt in (1, 2):
        _ensure_connected(reapy)
        try:
            project = reapy.Project()
            _ = project.n_tracks
            return project
        except Exception as e:
            _reset()
            if attempt == 2:
                raise RuntimeError(
                    f"Lost the connection to REAPER and could not "
                    f"re-establish it: {e}\n{_SETUP_HINT}"
                ) from e
            logger.warning("REAPER connection went stale (%s); reconnecting.", e)


def ensure_connected() -> None:
    """Kept for callers that only want to assert reachability."""
    get_project()


def connection_status() -> dict:
    """Report reachability without raising. Used by the health check."""
    try:
        project = get_project()
        return {
            "connected": True,
            "project_name": project.name,
            "track_count": project.n_tracks,
        }
    except Exception as e:
        return {"connected": False, "error": str(e)}
