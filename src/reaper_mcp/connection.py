"""Connection management for the REAPER distant API.

Reconnecting is rate limited to prevent REAPER from displaying a modal dialog that
blocks the connection. When `activate_reapy_server.py` is called while already
running, REAPER displays a "ReaScript task control" modal. This modal halts all
deferred scripts. To avoid triggering this dialog, the connection logic verifies
the socket state and limits reconnection attempts.
"""

import contextlib
import functools
import glob
import importlib.abc
import importlib.machinery
import logging
import os
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger("reaper_mcp.connection")

_lock = threading.RLock()
_connected = False

_last_attempt = 0.0
_consecutive_failures = 0

REAPY_SERVER_PORT = 2306
WEB_INTERFACE_PORT = 2307

RECONNECT_INTERVAL_SEC = 20.0
MAX_CONSECUTIVE_FAILURES = 3

WARM_WAIT_SEC = 3.0

ACTIVATION_WAIT_SEC = 15.0
ACTIVATION_COOLDOWN_SEC = 60.0
ACTION_TIMEOUT_SEC = 5.0

_last_activation = None

_SETUP_HINT = (
    "Ensure REAPER is running and the distant API is enabled.\n"
    "To enable it, close REAPER and run:\n"
    "    python reaper/enable_reapy.py\n"
    "Alternatively, from within REAPER: Actions > Show action list > ReaScript: Run... > "
    "enable_reapy.py, then restart REAPER.\n"
    f"The reapy server uses port {REAPY_SERVER_PORT}. REAPER's web "
    f"interface requires port {WEB_INTERFACE_PORT}. If a web interface occupies "
    f"port {REAPY_SERVER_PORT}, run "
    "`python reaper/enable_reapy.py --repair` to correct the configuration."
)

_DIALOG_HINT = (
    "Check the REAPER application window. If a 'ReaScript task control' dialog is "
    "present for 'activate_reapy_server.py', REAPER will not execute background scripts. "
    "This prevents the server and bridge from responding.\n"
    "\n"
    "Select 'New instance' or 'Continue running', and "
    "check 'Remember my answer for this script'.\n"
    "\n"
    "Do not select 'Terminate instances'. This action stops the server and the "
    "Lua bridge, which will cause connection failures.\n"
    "\n"
    "This dialog can appear if REAPER is unresponsive for more than 0.5 seconds "
    "during startup, rendering, plugin scanning, or project loading."
)

_NO_SERVER_HINT = (
    "REAPER's web interface answers, but the reapy server never started. REAPER starts "
    "it by running the `activate_reapy_server` action, so that action is missing from "
    "REAPER's action list (reaper-kb.ini), or Python ReaScript failed to load it."
)


def _bound_activation(web_interface) -> None:
    """Replace reapy's unbounded server lookup with one that gives up.

    reapy 0.10 answers an empty `server_port` by running the activate action and
    asking again, recursively and with no limit, and runs the action without a
    timeout. When REAPER cannot start the server, the lookup never returns and runs
    the action twice a second. That hung `import reapy`, and with it the server.
    This version waits up to ACTIVATION_WAIT_SEC, and runs the action at most once
    per ACTIVATION_COOLDOWN_SEC: running it while the server starts opens REAPER's
    "ReaScript task control" dialog.
    """
    network_errors = (web_interface.URLError, web_interface.timeout)
    undefined = web_interface.UndefinedExtStateError
    disabled = web_interface.DisabledDistAPIError

    def perform_action(self, action_id):
        web_interface.request.urlopen(self._url + str(action_id), timeout=ACTION_TIMEOUT_SEC).close()

    def get_reapy_server_port(self):
        try:
            return self.ext_state["server_port"]
        except network_errors:
            raise disabled
        except undefined:
            pass

        global _last_activation
        now = time.monotonic()
        if _last_activation is None or now - _last_activation >= ACTIVATION_COOLDOWN_SEC:
            _last_activation = now
            try:
                self.activate_reapy_server()
            except network_errors:
                raise disabled

        deadline = time.monotonic() + ACTIVATION_WAIT_SEC
        while time.monotonic() < deadline:
            time.sleep(0.5)
            try:
                return self.ext_state["server_port"]
            except undefined:
                continue
            except network_errors:
                break

        logger.warning("%s", _NO_SERVER_HINT)
        raise disabled

    web_interface.WebInterface.perform_action = perform_action
    web_interface.WebInterface.get_reapy_server_port = get_reapy_server_port


class _ReapyActivationGuard(importlib.abc.MetaPathFinder):
    """Patch reapy's web interface module as it loads.

    reapy connects at import time, from module code that runs right after this
    module loads, so there is no later point at which to patch it.
    """

    NAME = "reapy.tools.network.web_interface"

    def find_spec(self, fullname, path, target=None):
        if fullname != self.NAME:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None or spec.loader is None:
            return spec
        exec_module = spec.loader.exec_module

        def exec_and_bound(module):
            exec_module(module)
            _bound_activation(module)

        spec.loader.exec_module = exec_and_bound
        return spec


def _install_reapy_guard() -> None:
    loaded = sys.modules.get(_ReapyActivationGuard.NAME)
    if loaded is not None:
        _bound_activation(loaded)
    elif not any(isinstance(f, _ReapyActivationGuard) for f in sys.meta_path):
        sys.meta_path.insert(0, _ReapyActivationGuard())


_install_reapy_guard()


def _import_reapy():
    """Import reapy and handle associated errors.

    The import runs the connection logic in `reapy/tools/network/machines.py`,
    which starts the reapy server when it is not running. The short wait first
    lets a start that another client already requested publish its port, so the
    action does not run twice. Nothing in REAPER starts the server on its own, so
    a longer wait would only delay the first tool call.
    """
    if "reapy" not in sys.modules:
        _wait_for_server(WARM_WAIT_SEC)

    try:
        import reapy  # noqa: PLC0415
        return reapy
    except Exception as e:
        v = f"{sys.version_info.major}.{sys.version_info.minor}"
        raise RuntimeError(
            f"Failed to import reapy under Python {v}: {e}. "
            "Execute scripts/bootstrap.py to build the environment."
        ) from e


class _Deferred:
    """Stand-in for reapy, or one of its modules, that imports it on first use.

    Importing reapy connects to REAPER. The tool modules imported it at module level,
    so the server could not answer `initialize` until REAPER did. They use these
    instead, and the first attribute access imports reapy through _import_reapy.
    """

    def __init__(self, *path: str) -> None:
        self._path = path

    def __getattr__(self, name: str):
        target = _import_reapy()
        for part in self._path:
            target = getattr(target, part)
        return getattr(target, name)


reapy = _Deferred()
RPR = _Deferred("reascript_api")

# Undo points made by the tools carry this prefix, which is how the undo tool tells
# them apart from the user's own edits.
UNDO_PREFIX = "MCP: "


def is_null(pointer) -> bool:
    """Check for a null pointer from ReaScript.

    Pointers come back as strings such as '(TrackEnvelope*)0x0000000000000000',
    which are truthy in Python.
    """
    return not pointer or "0x0000000000000000" in str(pointer)


def held():
    """Keep REAPER serving this client until the block exits.

    A call from outside REAPER waits for the reapy server's next defer cycle,
    about 30 ms. Inside this block a call takes under a millisecond, so batch
    tools read and write inside it. REAPER does nothing else meanwhile, so the
    block must not wait on anything outside REAPER.
    """
    return reapy.inside_reaper()


@contextlib.contextmanager
def undo_step(tool: str):
    """Make the REAPER calls in the block one undo point named "MCP: <tool>".

    Calls from outside REAPER create no undo point. An undo block opened by one
    call and closed by a later one does not work either: REAPER closes it at the
    end of the first defer cycle as "ReaScript: Run", holding the state from
    before the change. Holding REAPER keeps the whole block in one cycle. A block
    that changes nothing adds no undo point.
    """
    with held():
        RPR.Undo_BeginBlock2(0)
        try:
            yield
        finally:
            RPR.Undo_EndBlock2(0, UNDO_PREFIX + tool, -1)


def undo_top() -> str:
    """Return the name of the step Undo would revert next, or "" when there is none.

    Python ReaScript's Undo_CanUndo2 and Undo_CanRedo2 decode the returned string
    without checking it, so an empty history raises inside REAPER instead of
    returning nothing.
    """
    return _step_name(redo=False)


def _step_name(redo: bool) -> str:
    try:
        return (RPR.Undo_CanRedo2(0) if redo else RPR.Undo_CanUndo2(0)) or ""
    except Exception as e:
        if "decode" in str(e):
            return ""
        raise


# Copies kept per project by backup_before_first_change.
BACKUP_KEEP = 5
_backed_up: set = set()


def backup_before_first_change():
    """Save a copy of the open project before this server first changes it.

    Once per project file per server process, the project as it is in memory,
    unsaved changes included, is written next to its file as
    <name>.mcp-backup-<time>.rpp; only the newest BACKUP_KEEP are kept.
    Main_SaveProjectEx writes the copy without rebinding the project or touching
    its dirty flag or undo history. A project never saved has no folder to write
    to and is skipped. REAPER_MCP_BACKUP=0 turns this off. Never raises: a failed
    backup must not stop the change the user asked for.
    """
    if os.environ.get("REAPER_MCP_BACKUP", "1").strip().lower() in ("0", "off", "false", "no"):
        return None
    try:
        path = RPR.EnumProjects(-1, "", 4096)[2]
        if not path or path in _backed_up:
            return None
        if RPR.CountTracks(0) == 0 and RPR.CountMediaItems(0) == 0:
            return None
        project = Path(path)
        backup = project.with_name(f"{project.stem}.mcp-backup-{time.strftime('%Y%m%d-%H%M%S')}.rpp")
        RPR.Main_SaveProjectEx(0, str(backup), 0)
        if not backup.is_file() or backup.stat().st_size == 0:
            logger.warning("Backup of %s was not written", path)
            return None
        _backed_up.add(path)
        # The timestamp sorts by name, oldest first.
        copies = sorted(project.parent.glob(f"{glob.escape(project.stem)}.mcp-backup-*.rpp"))
        for stale in copies[:-BACKUP_KEEP]:
            stale.unlink(missing_ok=True)
        return str(backup)
    except Exception as e:
        logger.warning("Backup before the first change failed, continuing without one: %s", e)
        return None


def records_undo(items: bool = False, own_step: bool = False, verify: bool = True):
    """Make a tool that changes the project undoable, backed up and checked. Apply beneath @mcp.tool().

    The tool runs as one undo point named "MCP: <tool>":
    * by default inside undo_step, an undo block, which records track, FX, send,
      envelope, marker, tempo, note and item edits;
    * with items=True through Undo_OnStateChange2 after a successful call, for tools
      that create MIDI items: CreateNewMIDIItemInProj inside a block records nothing,
      so undoing reverted the step before it and left the item in place;
    * with own_step=True the tool records its own step (undo_step inside), for a tool
      that must not hold REAPER while it renders.

    Before the first change to a project, the project is backed up. REAPER adds an
    undo point only when the project differs from the last one it recorded, so a call
    reported as successful that left no "MCP: <tool>" step changed nothing: either the
    value was already set, or the write never reached REAPER, the way a reapy
    attribute assignment does. The reply then carries "unconfirmed". verify=False
    skips that check, for tools whose change REAPER does not keep in undo history.
    """
    def decorate(fn):
        step = UNDO_PREFIX + fn.__name__

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                get_project()
            except Exception as e:
                return {"success": False, "error": str(e)}
            backup = backup_before_first_change()
            if own_step:
                result = fn(*args, **kwargs)
            elif items:
                with held():
                    result = fn(*args, **kwargs)
                    if isinstance(result, dict) and result.get("success"):
                        RPR.Undo_OnStateChange2(0, step)
            else:
                with undo_step(fn.__name__):
                    result = fn(*args, **kwargs)
            if not isinstance(result, dict):
                return result
            if backup:
                result["backup"] = backup
            if verify and result.get("success"):
                top = undo_top()
                if top != step:
                    result["unconfirmed"] = (
                        "REAPER recorded no change for this call: the value was already set, "
                        "or the write did not reach REAPER. Read the value back."
                    )
            return result

        return wrapper

    return decorate


def _server_state(timeout_sec: float = 0.5) -> str:
    """Return 'ready', 'starting', or 'down'.

    The web interface is queried directly to avoid side effects. Utilizing
    reapy's internal read function triggers `activate_reapy_server()` on failure,
    which results in a modal dialog. Reading via urllib bypasses this behavior.
    """
    from urllib import request  # noqa: PLC0415

    url = (
        f"http://localhost:{WEB_INTERFACE_PORT}/_/GET/EXTSTATE/reapy/server_port"
    )
    try:
        body = request.urlopen(url, timeout=timeout_sec).read().decode("utf-8")
    except Exception:
        return "down"
    return "ready" if body.split("\t")[-1][:-1] else "starting"


def _wait_for_server(budget_sec: float) -> None:
    """Delay execution to allow REAPER to publish the port.

    This delay handles a race condition during cold start where the web interface
    is available before the reapy server sets its port in the extended state.
    Waiting prevents reapy from assuming the server is inactive and launching
    a duplicate instance.
    """
    deadline = time.monotonic() + budget_sec
    give_up_on_silence = time.monotonic() + WARM_WAIT_SEC
    announced = False
    saw_interface = False

    while True:
        state = _server_state()
        if state == "ready":
            if announced:
                logger.info("REAPER reapy server is ready.")
            return

        if state == "starting":
            saw_interface = True
            if not announced:
                logger.info(
                    "REAPER is initializing the reapy server. Waiting up to %.0f seconds.",
                    budget_sec,
                )
                announced = True
        elif not saw_interface and time.monotonic() >= give_up_on_silence:
            return

        if time.monotonic() >= deadline:
            if announced:
                logger.info("Server port not found. Proceeding with reapy initialization.")
            return
        time.sleep(0.5)


def _connect(reapy) -> None:
    """Establish connection with reapy.

    A short wait is included to prevent triggering the action during a reconnect
    following a REAPER restart.
    """
    global _last_attempt
    _last_attempt = time.monotonic()

    _wait_for_server(WARM_WAIT_SEC)

    try:
        # connect() keeps what the first attempt found, including no connection at
        # all when REAPER was closed then. reconnect() looks REAPER up again.
        reapy.reconnect()
    except Exception as e:
        raise RuntimeError(f"Connection to REAPER failed: {e}\n{_SETUP_HINT}") from e


def _ensure_connected(reapy) -> None:
    """Verify connection state and apply rate limiting for retries."""
    global _connected
    with _lock:
        if _connected:
            return

        since = time.monotonic() - _last_attempt
        if _last_attempt and since < RECONNECT_INTERVAL_SEC:
            raise RuntimeError(
                f"Reconnect rate limit active. Last attempt was "
                f"{since:.0f}s ago. Interval is "
                f"{RECONNECT_INTERVAL_SEC:.0f}s.\n\n{_DIALOG_HINT}"
            )

        if _consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            raise RuntimeError(
                f"Connection attempts exceeded limit of "
                f"{_consecutive_failures}.\n\n{_DIALOG_HINT}\n\n"
                f"{_SETUP_HINT}"
            )

        _connect(reapy)
        _connected = True
        logger.info("Connected to REAPER.")


def _reset() -> None:
    """Clear the connection state."""
    global _connected
    with _lock:
        _connected = False


def get_project():
    """Return the active REAPER project.

    The `n_tracks` property is accessed to validate the connection via a round trip.
    Transient failures are retried on the existing connection to prevent unnecessary
    teardown and server restart triggers.
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
                logger.warning("REAPER response timeout (%s). Retrying.", e)
                time.sleep(1.0)

    with _lock:
        _consecutive_failures += 1
    _reset()

    state = _server_state()
    if state == "down":
        raise RuntimeError(
            "REAPER application or web interface is unavailable.\n\n"
            "Ensure REAPER is running and the web interface is configured on port "
            f"{WEB_INTERFACE_PORT}.\n\n"
            f"Underlying error: {last_error}"
        ) from last_error

    if state == "starting":
        raise RuntimeError(
            f"{_NO_SERVER_HINT}\n\n{_SETUP_HINT}\n\n{_DIALOG_HINT}\n\n"
            f"Underlying error: {last_error}"
        ) from last_error

    raise RuntimeError(
        f"REAPER communication failed: {last_error}\n\n{_DIALOG_HINT}"
    ) from last_error


def ensure_connected() -> None:
    """Verify connection reachability."""
    get_project()


def connection_status() -> dict:
    """Return reachability status without raising exceptions."""
    try:
        project = get_project()
        return {
            "connected": True,
            "project_name": project.name,
            "track_count": project.n_tracks,
        }
    except Exception as e:
        return {"connected": False, "error": str(e)}
