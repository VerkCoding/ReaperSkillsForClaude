"""Connection management for the REAPER distant API.

Reconnecting is rate limited to prevent REAPER from displaying a modal dialog that
blocks the connection. When `activate_reapy_server.py` is called while already
running, REAPER displays a "ReaScript task control" modal. This modal halts all
deferred scripts. To avoid triggering this dialog, the connection logic verifies
the socket state and limits reconnection attempts.
"""

import importlib.abc
import importlib.machinery
import logging
import sys
import threading
import time

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
