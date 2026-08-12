"""Connection management for the REAPER distant API.

Reconnecting is rate limited, and the reason is not caution - it is that an
unlimited retry loop makes REAPER pop a modal dialog and then wedges the
connection for good. The chain is worth writing down, because nothing in the
error messages points at it:

  * ``activate_reapy_server.py`` is a DEFERRED ReaScript. It runs forever in
    REAPER's background and is the server we talk to. It does not call
    ``set_action_options``, so REAPER uses its default for a re-launch: ask.

  * reapy reaches the server by reading a REAPER ext state over the web
    interface. If that read fails it assumes the server is gone and PERFORMS THE
    ACTION AGAIN to restart it - see ``WebInterface.get_reapy_server_port``.

  * Re-running an already-running deferred script is what makes REAPER show
    "ReaScript task control: activate_reapy_server.py is running in the
    background. Terminate / New instance / Continue?"

  * That dialog is modal, so REAPER stops running deferred scripts - including
    the server. Every call now fails, and a reconnect-on-every-failure policy
    performs the action again, and again.

The read that starts it has a 0.5 second timeout inside reapy. Any moment REAPER
is busy past that - loading plugins, rendering, opening a large project - looks
identical to a dead server. On a session of any length this is not unlikely, it
is expected, which is why "just retry" was the wrong policy rather than an
unlucky one.

So: prove the socket cheaply, reconnect at most occasionally, and when it keeps
failing say what to look for on screen instead of hammering.
"""

import logging
import threading
import time

logger = logging.getLogger("reaper_mcp.connection")

_lock = threading.RLock()
_connected = False

# When we last asked reapy to connect. Each attempt can trigger the REAPER
# action, so this is the value that governs how often a dialog can appear.
_last_attempt = 0.0
_consecutive_failures = 0

REAPY_SERVER_PORT = 2306
WEB_INTERFACE_PORT = 2307

# Long enough that a busy REAPER - a render, a plugin scan - finishes and
# answers on its own rather than being interrupted by a reconnect it did not
# need. Short enough that a genuine restart is picked up without the user
# wondering whether anything is happening.
RECONNECT_INTERVAL_SEC = 20.0

# After this many failed rounds, stop reconnecting until something succeeds.
# Past this point the problem is not transient and more attempts only add
# dialogs to whatever is already on screen.
MAX_CONSECUTIVE_FAILURES = 3

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

_DIALOG_HINT = (
    "CHECK REAPER'S WINDOW FIRST. If a 'ReaScript task control' dialog is "
    "waiting - 'activate_reapy_server.py is running in the background' - that "
    "dialog is the problem: while it is open REAPER runs no background scripts, "
    "so the server cannot answer.\n"
    "Choose 'Continue running' (the server is alive; it was only asked to start "
    "twice), and tick 'Remember my answer for this script' so it stops "
    "appearing.\n"
    "REAPER being busy for more than half a second - rendering, scanning "
    "plugins, opening a big project - is enough to start this, so it says "
    "nothing about your setup being wrong."
)


def _import_reapy():
    """Import reapy, converting the failure into something actionable.

    The resulting ImportError names an internal module rather than the real
    problem, so translate it here.
    """
    try:
        import reapy  # noqa: PLC0415
        return reapy
    except Exception as e:
        import sys
        v = f"{sys.version_info.major}.{sys.version_info.minor}"
        raise RuntimeError(
            f"Could not import reapy under Python {v}: {e}. "
            "Run scripts/bootstrap.py to build the plugin's environment."
        ) from e


def _connect(reapy) -> None:
    global _last_attempt
    _last_attempt = time.monotonic()
    try:
        reapy.connect()
    except Exception as e:
        raise RuntimeError(f"Cannot connect to REAPER: {e}\n{_SETUP_HINT}") from e


def _ensure_connected(reapy) -> None:
    """Connect if needed, but never more often than the interval allows."""
    global _connected
    with _lock:
        if _connected:
            return

        since = time.monotonic() - _last_attempt
        if _last_attempt and since < RECONNECT_INTERVAL_SEC:
            raise RuntimeError(
                f"Not reconnecting yet - the last attempt was "
                f"{since:.0f}s ago and retries are spaced "
                f"{RECONNECT_INTERVAL_SEC:.0f}s apart.\n\n{_DIALOG_HINT}"
            )

        if _consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            raise RuntimeError(
                f"Gave up reconnecting to REAPER after "
                f"{_consecutive_failures} attempts.\n\n{_DIALOG_HINT}\n\n"
                f"{_SETUP_HINT}"
            )

        _connect(reapy)
        _connected = True
        logger.info("Connected to REAPER")


def _reset() -> None:
    global _connected
    with _lock:
        _connected = False


def get_project():
    """Return the active REAPER project.

    The ``n_tracks`` read is not decorative: it forces an actual round trip.
    ``reapy.Project()`` alone can hand back an object that only fails later,
    deep inside a tool, where the error is much harder to attribute.

    A failure is retried ONCE against the existing connection before the
    connection itself is questioned. Most failures here are a busy REAPER, not a
    dead one, and tearing the connection down is what leads to re-triggering the
    server action.
    """
    global _consecutive_failures
    reapy = _import_reapy()

    _ensure_connected(reapy)

    last_error = None
    for attempt in (1, 2):
        try:
            project = reapy.Project()
            _ = project.n_tracks
            with _lock:
                _consecutive_failures = 0
            return project
        except Exception as e:
            last_error = e
            if attempt == 1:
                # Same connection, one more time. A render or a plugin scan
                # blocks REAPER for longer than reapy's half-second timeout, and
                # that is not a reason to rebuild anything.
                logger.warning("REAPER did not answer (%s); retrying once.", e)
                time.sleep(1.0)

    with _lock:
        _consecutive_failures += 1
    _reset()

    raise RuntimeError(
        f"REAPER is not answering: {last_error}\n\n{_DIALOG_HINT}"
    ) from last_error


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
