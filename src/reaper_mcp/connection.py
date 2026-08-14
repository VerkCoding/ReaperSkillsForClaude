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

# How long to let REAPER finish starting its reapy server before asking reapy
# to find it. See _wait_for_server.
#
# The first connection of a session is the one that races REAPER's own startup,
# and on a cold machine that start is slow - REAPER loads Python and imports
# reapy inside itself. A minute is generous enough to cover a first-ever launch
# and still bounded. Claude has already started the server process by this
# point, so the wait costs nothing anyone is watching.
COLD_START_WAIT_SEC = 60.0

# Afterwards REAPER is up, so the question is answered almost immediately or
# not at all. A long wait here would make every call against a closed REAPER
# hang for a minute.
WARM_WAIT_SEC = 3.0

# Only the first connection of a session waits the long budget.
_first_connect = True

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
    "dialog IS the problem: while it is open REAPER runs no background scripts "
    "at all, so neither the server nor the bridge can answer.\n"
    "\n"
    "Choose 'New instance' (or 'Continue running' on older REAPER builds), and "
    "tick 'Remember my answer for this script'.\n"
    "\n"
    "Do NOT choose 'Terminate instances'. That stops the running server, and "
    "because the dialog has already halted every deferred script it takes the "
    "Lua bridge with it - which is why choosing it leaves REAPER connected to "
    "nothing and Claude reporting an MCP error.\n"
    "\n"
    "The server was only ever asked to start twice; nothing is broken. REAPER "
    "being busy for more than half a second - starting up, rendering, scanning "
    "plugins, opening a big project - is enough to cause it."
)


def _import_reapy():
    """Import reapy, converting the failure into something actionable.

    The resulting ImportError names an internal module rather than the real
    problem, so translate it here.

    The wait happens HERE, before the import, and that placement is the whole
    point. Importing reapy is not passive - the bottom of
    ``reapy/tools/network/machines.py`` runs::

        if not reapy.is_inside_reaper():
            connect("localhost")

    which reaches ``get_reapy_server_port()`` and, on a missing ext state,
    performs the REAPER action. So the dialog is triggered by the import
    statement itself; anything guarding ``reapy.connect()`` further down is
    already too late to prevent it.
    """
    global _first_connect
    _wait_for_server(COLD_START_WAIT_SEC if _first_connect else WARM_WAIT_SEC)
    _first_connect = False

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


def _server_port_published(timeout_sec: float = 0.5) -> bool:
    """Has REAPER's reapy server announced itself yet?

    Read directly, deliberately not through reapy. reapy's own read is the thing
    that starts the dialog: on a miss it calls activate_reapy_server(), which
    performs the action a second time, and REAPER answers a re-launched
    deferred script by asking the user what to do. Asking the same question
    with plain urllib has no such side effect - a miss is just a miss.

    The URL and the tab-separated reply shape are reapy's
    ``ExtState.__getitem__``; matching it exactly is the point, because what
    matters is whether the value reapy is about to read is there yet.
    """
    from urllib import request  # noqa: PLC0415

    url = (
        f"http://localhost:{WEB_INTERFACE_PORT}/_/GET/EXTSTATE/reapy/server_port"
    )
    try:
        body = request.urlopen(url, timeout=timeout_sec).read().decode("utf-8")
    except Exception:
        # No web interface yet, or REAPER is not up. Either way, not ready -
        # and not something to report differently from "not ready".
        return False
    return bool(body.split("\t")[-1][:-1])


def _wait_for_server(budget_sec: float) -> None:
    """Give REAPER time to publish the port before reapy goes looking for it.

    This is the whole fix for the cold-start dialog, and the race is precise:

      * REAPER's web interface comes up early in startup.
      * activate_reapy_server.py is launched at the same time, but only sets
        the ext state once it has actually started - after Python and reapy
        have been imported inside REAPER, which on a cold machine is slow.
      * Between those two moments the web interface answers and the ext state
        is empty, which is exactly the case reapy treats as "the server died,
        start it again".

    So on a first run the answer to "is the server there?" is reliably "not
    yet", reapy performs the action, and the user gets a modal dialog they have
    no way to interpret - one whose Terminate option stops the server AND the
    Lua bridge, because a modal dialog halts every deferred script REAPER is
    running.

    Waiting turns that from a race into a non-event. It is a plain poll with no
    side effects: if the port never appears we fall through and let reapy do
    what it did before, because a REAPER that genuinely is not running the
    server does need the action performed, and then there is no second instance
    to clash with.
    """
    if _server_port_published():
        return

    logger.info("Waiting up to %.0fs for REAPER's reapy server to start", budget_sec)
    deadline = time.monotonic() + budget_sec
    while time.monotonic() < deadline:
        time.sleep(0.5)
        if _server_port_published():
            logger.info("REAPER's reapy server is up")
            return
    logger.info("It did not appear; letting reapy start it")


def _connect(reapy) -> None:
    global _last_attempt
    _last_attempt = time.monotonic()

    # A short wait again here. reapy.connect() re-enters the same lookup, so a
    # reconnect against a REAPER that has just restarted can trigger the action
    # exactly as the import can. The import already paid the long wait.
    _wait_for_server(WARM_WAIT_SEC)

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
