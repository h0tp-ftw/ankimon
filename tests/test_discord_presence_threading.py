import importlib.util
import sys
import threading
import time
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest


class _PyPresenceException(Exception):
    """Stand-in for the vendored pypresence exception hierarchy."""


class _ResponseTimeout(_PyPresenceException):
    """Discord did not answer in time. The socket is still open."""


class _ServerError(_PyPresenceException):
    """Discord answered with an error payload. The socket is still open."""


class _DiscordError(_PyPresenceException):
    """Discord answered with an error code. The socket is still open."""


#: Levels ``ShowInfoLogger._record`` actually writes. Anything else — "debug",
#: notably — falls through every branch and is recorded nowhere.
RECORDED_LOG_LEVELS = {"info", "warning", "error", "game"}


class _Taskman:
    def __init__(self):
        self.callbacks = []
        self.calling_threads = []

    def run_on_main(self, callback):
        self.callbacks.append(callback)
        self.calling_threads.append(threading.get_ident())


def _package(name):
    module = types.ModuleType(name)
    module.__path__ = []
    return module


@pytest.fixture
def discord_env(monkeypatch):
    taskman = _Taskman()
    logger = MagicMock()
    tooltip = MagicMock()
    presenter_notify = MagicMock()
    event_emit = MagicMock()
    mw = types.SimpleNamespace(
        addonManager=types.SimpleNamespace(allAddons=lambda: []),
        logger=logger,
        taskman=taskman,
    )

    modules = {
        "Ankimon": _package("Ankimon"),
        "Ankimon.functions": _package("Ankimon.functions"),
        "Ankimon.pyobj": _package("Ankimon.pyobj"),
        "Ankimon.addon_files": _package("Ankimon.addon_files"),
        "Ankimon.addon_files.lib": _package("Ankimon.addon_files.lib"),
        "aqt": _package("aqt"),
    }
    modules["aqt"].mw = mw

    aqt_utils = types.ModuleType("aqt.utils")
    aqt_utils.showWarning = MagicMock()
    aqt_utils.tooltip = tooltip
    modules["aqt.utils"] = aqt_utils

    tracker_module = types.ModuleType("Ankimon.pyobj.ankimon_tracker")
    tracker_module.AnkimonTracker = object
    modules[tracker_module.__name__] = tracker_module

    class _Presence:
        def __init__(self, client_id):
            self.client_id = client_id

        def connect(self):
            return None

    presence_module = types.ModuleType("Ankimon.addon_files.lib.pypresence")
    presence_module.Presence = _Presence
    # The real package re-exports these from pypresence.exceptions;
    # discord_function imports them to tell "this request failed" apart from
    # "the pipe is dead".
    presence_module.DiscordError = _DiscordError
    presence_module.ResponseTimeout = _ResponseTimeout
    presence_module.ServerError = _ServerError
    modules[presence_module.__name__] = presence_module

    error_module = types.ModuleType("Ankimon.pyobj.error_handler")
    error_module.show_warning_with_traceback = MagicMock()
    modules[error_module.__name__] = error_module

    services_module = types.ModuleType("Ankimon.services")
    services_module.services = types.SimpleNamespace(
        ui=types.SimpleNamespace(notify=presenter_notify)
    )
    modules[services_module.__name__] = services_module

    events_module = types.ModuleType("Ankimon.events")
    events_module.events = types.SimpleNamespace(emit=event_emit)
    modules[events_module.__name__] = events_module

    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    source = (
        Path(__file__).parents[1]
        / "src"
        / "Ankimon"
        / "functions"
        / "discord_function.py"
    )
    spec = importlib.util.spec_from_file_location(
        "Ankimon.functions.discord_function", source
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)

    return types.SimpleNamespace(
        event_emit=event_emit,
        logger=logger,
        module=module,
        mw=mw,
        presenter_notify=presenter_notify,
        taskman=taskman,
        tooltip=tooltip,
    )


def _assert_queued_tooltip(env, message):
    env.tooltip.assert_not_called()
    env.presenter_notify.assert_not_called()
    env.event_emit.assert_called_once_with("tooltip", message=message)
    assert len(env.taskman.callbacks) == 1

    env.taskman.callbacks.pop()()

    env.tooltip.assert_called_once_with(message)
    env.presenter_notify.assert_not_called()


def _presence_without_init(env, **overrides):
    """A DiscordPresence with __init__ bypassed, set up as if it had connected.

    Most tests here drive one method in isolation, so this fills in exactly the
    state the real constructor would have left behind.
    """
    instance = object.__new__(env.module.DiscordPresence)
    instance.logger_obj = env.logger
    instance.connected = True
    instance.loop = False
    instance.thread = None
    instance.RPC = None
    instance.settings = types.SimpleNamespace(get=lambda key: 1)
    instance.quotes = ["Reviewing"]
    instance.large_image_url = "image"
    instance.start_time = 0
    instance._client_id = "client-id"
    instance._checked_conflicts = True
    instance._first_attempt = False
    instance._last_connect_attempt = float("-inf")
    instance._rpc_lock = threading.RLock()
    for name, value in overrides.items():
        setattr(instance, name, value)
    return instance


def _patch_clock(env, monkeypatch, sleep=lambda _s: None, monotonic=time.monotonic):
    """Give the module its own clock.

    ``monkeypatch.setattr(env.module.time, "sleep", ...)`` would patch the
    stdlib module process-wide — ``env.module.time`` *is* ``sys.modules["time"]``
    — so every other thread in the process would get the stub too. Replacing the
    module's own ``time`` attribute keeps the blast radius to this module.
    """
    clock = types.SimpleNamespace(time=time.time, monotonic=monotonic, sleep=sleep)
    monkeypatch.setattr(env.module, "time", clock)
    return clock


def _stop_after_one_update(instance):
    """A `sleep` that ends the worker loop, so a test can join the thread."""

    def _sleep(_seconds):
        instance.loop = False

    return _sleep


def test_constructor_never_touches_the_rpc(discord_env, monkeypatch):
    """setup_discord_hooks() builds this object at add-on import time on Anki's
    main thread, and pypresence's handshake reads have no timeout, so the
    constructor must not connect. The first start() worker does it instead."""

    class _ExplodingPresence:
        def __init__(self, client_id):
            raise AssertionError("the constructor must not open the RPC")

    monkeypatch.setattr(discord_env.module, "Presence", _ExplodingPresence)

    instance = discord_env.module.DiscordPresence(
        "client-id",
        "image",
        object(),
        discord_env.logger,
        types.SimpleNamespace(get=lambda key: 1),
    )

    assert instance.RPC is None
    assert instance.connected is False
    assert instance.loop is False
    # Every attribute the other methods need exists even though nothing connected.
    for attribute in ("settings", "large_image_url", "quotes", "state", "start_time"):
        assert hasattr(instance, attribute)
    discord_env.tooltip.assert_not_called()


def test_setup_failure_queues_non_modal_tooltip(discord_env, monkeypatch):
    class _FailingPresence:
        def __init__(self, client_id):
            pass

        def connect(self):
            raise RuntimeError("Discord is closed")

    monkeypatch.setattr(discord_env.module, "Presence", _FailingPresence)
    _patch_clock(discord_env, monkeypatch)

    instance = discord_env.module.DiscordPresence(
        "client-id",
        "image",
        object(),
        discord_env.logger,
        types.SimpleNamespace(get=lambda key: 1),
    )
    instance.start()
    instance.thread.join(timeout=2)

    assert instance.thread.is_alive() is False
    assert instance.loop is False
    _assert_queued_tooltip(
        discord_env, "Error with Discord setup. Is Discord running?"
    )


def test_update_failure_queues_tooltip_from_worker(discord_env, monkeypatch):
    main_thread = threading.get_ident()
    _patch_clock(discord_env, monkeypatch)
    rpc = types.SimpleNamespace(
        update=MagicMock(side_effect=BrokenPipeError("connection lost")),
        close=MagicMock(),
    )
    instance = _presence_without_init(discord_env, loop=True, RPC=rpc)

    worker = threading.Thread(target=instance.update_presence)
    worker.start()
    worker.join()

    assert discord_env.taskman.calling_threads == [worker.ident]
    assert worker.ident != main_thread
    # The worker cleared both flags on failure so start() treats it as a dead
    # worker and spawns a fresh one on the next answered card.
    assert instance.loop is False
    assert instance.connected is False
    assert instance.RPC is None
    # The worker shuts the client down properly — nothing in pypresence does it
    # for us, and this is the one thread where close() is safe to run.
    rpc.close.assert_called_once_with()
    _assert_queued_tooltip(
        discord_env,
        "Error with Discord Rich Presence. Is Discord running?",
    )


def test_transient_rpc_error_keeps_the_connection(discord_env, monkeypatch):
    """A slow reply is not a dead pipe. read_output() raises ResponseTimeout
    with the socket still open, so tearing the connection down over one costs
    the user their presence for up to RECONNECT_INTERVAL for nothing."""
    _patch_clock(discord_env, monkeypatch)
    updates = {"n": 0}

    def _update(**_kwargs):
        updates["n"] += 1
        if updates["n"] == 1:
            raise discord_env.module.ResponseTimeout()

    rpc = types.SimpleNamespace(update=_update, close=MagicMock())
    instance = _presence_without_init(discord_env, loop=True, RPC=rpc)
    monkeypatch.setattr(
        discord_env.module.time, "sleep", _stop_after_one_update(instance)
    )

    instance.update_presence()

    assert instance.connected is True
    assert instance.RPC is rpc
    rpc.close.assert_not_called()
    # No tooltip: the connection is fine, so there is nothing to alarm about.
    discord_env.event_emit.assert_not_called()
    assert discord_env.taskman.callbacks == []


def test_a_closed_event_loop_drops_the_connection(discord_env, monkeypatch):
    """_rpc_lock rules out asyncio's "this event loop is already running", so the
    only RuntimeError left on this path is "Event loop is closed" — fatal.
    Treating it as transient would spin the worker every 30s forever, never
    reconnecting and never telling anyone."""
    rpc = types.SimpleNamespace(
        update=MagicMock(side_effect=RuntimeError("Event loop is closed")),
        close=MagicMock(),
    )
    instance = _presence_without_init(discord_env, loop=True, RPC=rpc)
    # Bound the loop from the outside as well: if this error were ever
    # classified as transient the worker would spin here forever, and the test
    # should report that as a failed assertion rather than hang.
    _patch_clock(discord_env, monkeypatch, sleep=_stop_after_one_update(instance))

    instance.update_presence()

    assert instance.loop is False
    assert instance.connected is False
    assert instance.RPC is None
    _assert_queued_tooltip(
        discord_env,
        "Error with Discord Rich Presence. Is Discord running?",
    )


def test_a_broken_taskman_never_falls_back_to_the_worker(discord_env, monkeypatch):
    """_run_on_main runs inline only when aqt is absent — the headless case. A
    half-built mw (a hot reload, say) must raise instead, or we are back to
    building the QMessageBox on the worker after all."""
    _patch_clock(discord_env, monkeypatch)
    monkeypatch.setattr(
        discord_env.module,
        "check_conflicting_discord_addons",
        lambda: ["AnkiCord"],
    )
    monkeypatch.setattr(discord_env.mw, "taskman", types.SimpleNamespace())

    class _RPC:
        def __init__(self, client_id):
            pass

        def connect(self):
            return None

        def update(self, **_kwargs):
            return None

        def close(self):
            return None

    monkeypatch.setattr(discord_env.module, "Presence", _RPC)
    instance = _presence_without_init(
        discord_env, connected=False, _checked_conflicts=False
    )
    monkeypatch.setattr(
        discord_env.module.time, "sleep", _stop_after_one_update(instance)
    )

    instance.start()
    instance.thread.join(timeout=2)

    discord_env.logger.log_and_showinfo.assert_not_called()
    assert instance.loop is False
    levels = {call.args[0] for call in discord_env.logger.log.call_args_list}
    assert "error" in levels, "the worker must report why it gave up"


def test_start_failure_queues_non_modal_tooltip(discord_env):
    instance = _presence_without_init(
        discord_env,
        thread=types.SimpleNamespace(
            is_alive=MagicMock(side_effect=RuntimeError("thread failed"))
        ),
    )

    instance.start()

    assert instance.loop is False
    _assert_queued_tooltip(
        discord_env,
        "Error starting Discord Rich Presence. Is Discord running?",
    )


def test_stop_failure_queues_non_modal_tooltip(discord_env):
    rpc = types.SimpleNamespace(
        clear=MagicMock(side_effect=BrokenPipeError("clear failed")),
        close=MagicMock(),
    )
    instance = _presence_without_init(discord_env, loop=True, RPC=rpc)

    instance.stop()

    assert instance.connected is False
    assert instance.RPC is None
    # Not closed from here: Presence.close() writes to the pipe and closes the
    # event loop, and this runs on Anki's main thread. The GC gets it instead.
    rpc.close.assert_not_called()
    _assert_queued_tooltip(
        discord_env,
        "Error clearing Discord Rich Presence. Please check Logger for info.",
    )


def test_stop_presence_failure_queues_non_modal_tooltip(discord_env):
    instance = _presence_without_init(
        discord_env,
        loop=True,
        RPC=types.SimpleNamespace(
            update=MagicMock(side_effect=BrokenPipeError("update failed")),
            close=MagicMock(),
        ),
    )

    instance.stop_presence()

    assert instance.connected is False
    assert instance.RPC is None
    _assert_queued_tooltip(
        discord_env,
        "Error stopping Discord Rich Presence. Please check Logger for info.",
    )


def test_guarded_methods_are_silent_when_disconnected(discord_env):
    """With no connection the hooks still fire on every card and every sync;
    they must be no-ops, not tracebacks against a None RPC."""
    instance = _presence_without_init(discord_env, connected=False, loop=True)

    instance.update_presence()
    instance.stop()
    instance.stop_presence()

    assert instance.loop is False
    discord_env.logger.log.assert_not_called()
    discord_env.event_emit.assert_not_called()


def test_stop_does_not_orphan_a_live_worker(discord_env, monkeypatch):
    """stop() used to null self.thread while the worker was still parked in its
    sleep. The next card answer then passed start()'s liveness check and spawned
    a second worker beside the first — two threads sharing one asyncio loop."""
    _patch_clock(discord_env, monkeypatch)
    parked = threading.Event()
    updating_threads = []

    class _RPC:
        def update(self, **_kwargs):
            updating_threads.append(threading.get_ident())

        def clear(self):
            return None

        def close(self):
            return None

    instance = _presence_without_init(discord_env, RPC=_RPC())
    monkeypatch.setattr(
        discord_env.module.time, "sleep", lambda _s: parked.wait(5)
    )

    instance.start()  # a card is answered: worker A starts and parks
    worker_a = instance.thread
    for _ in range(500):
        if updating_threads:
            break
        time.sleep(0.002)
    assert updating_threads, "worker A never pushed an update"

    instance.stop()  # a sync finishes while worker A is still parked
    assert worker_a.is_alive()
    assert instance.thread is worker_a, "a live worker must keep its handle"

    instance.start()  # the next card is answered inside the same sleep window
    assert instance.thread is worker_a, "no second worker may be spawned"
    assert instance.loop is True, "the parked worker is re-armed instead"

    instance.loop = False
    parked.set()
    worker_a.join(timeout=2)
    assert set(updating_threads) == {worker_a.ident}


def test_start_resumes_a_worker_parked_after_a_break(discord_env, monkeypatch):
    """Leaving the reviewer pushes "Break time!" and clears loop. Coming back
    inside the same 30s window must resume the parked worker, or the presence
    sits on the break message while the user is actively reviewing."""
    _patch_clock(discord_env, monkeypatch)
    parked = threading.Event()
    states = []

    class _RPC:
        def update(self, **kwargs):
            states.append(kwargs.get("state"))

        def close(self):
            return None

    instance = _presence_without_init(discord_env, RPC=_RPC())
    monkeypatch.setattr(
        discord_env.module.time, "sleep", lambda _s: parked.wait(5)
    )

    instance.start()
    for _ in range(500):
        if states:
            break
        time.sleep(0.002)
    assert states == ["Reviewing"]

    instance.stop_presence()  # reviewer_will_end
    assert states[-1].startswith("Break time!")

    instance.start()  # reviewer_did_answer_card, worker still parked
    parked.set()
    for _ in range(500):
        if len(states) > 2:
            break
        time.sleep(0.002)
    instance.loop = False
    instance.thread.join(timeout=2)

    assert states[-1] == "Reviewing", "the worker resumed instead of exiting"


def test_conflict_warning_is_dispatched_to_the_main_thread(discord_env, monkeypatch):
    """_connect() runs on the worker, and the conflicting-add-on warning ends in
    a QMessageBox (ShowInfoLogger.log_and_showinfo). Qt widgets may only be
    built on the GUI thread, so the warning has to hop over via taskman."""
    _patch_clock(discord_env, monkeypatch)
    monkeypatch.setattr(
        discord_env.module,
        "check_conflicting_discord_addons",
        lambda: ["AnkiCord"],
    )

    class _RPC:
        def __init__(self, client_id):
            pass

        def connect(self):
            return None

        def update(self, **_kwargs):
            return None

        def close(self):
            return None

    monkeypatch.setattr(discord_env.module, "Presence", _RPC)

    instance = _presence_without_init(
        discord_env, connected=False, _checked_conflicts=False
    )
    monkeypatch.setattr(
        discord_env.module.time, "sleep", _stop_after_one_update(instance)
    )

    instance.start()
    instance.thread.join(timeout=2)
    worker_ident = instance.thread.ident

    # Queued from the worker, but not executed there.
    discord_env.logger.log_and_showinfo.assert_not_called()
    assert discord_env.taskman.calling_threads == [worker_ident]
    assert worker_ident != threading.get_ident()
    assert len(discord_env.taskman.callbacks) == 1

    discord_env.taskman.callbacks.pop()()  # Anki runs it on the main thread

    level, message = discord_env.logger.log_and_showinfo.call_args[0]
    assert level == "warning"
    assert "AnkiCord" in message


def test_main_thread_stop_never_enters_the_workers_event_loop(discord_env, monkeypatch):
    """pypresence drives one asyncio loop per client and both update() and
    clear() call run_until_complete() on it. Two threads inside it raise "This
    event loop is already running" and desynchronise the request/response
    stream, so a main-thread caller skips its turn rather than colliding."""
    _patch_clock(discord_env, monkeypatch)
    inside_update = threading.Event()
    release = threading.Event()
    rpc = types.SimpleNamespace(
        update=lambda **_kwargs: (inside_update.set(), release.wait(5)),
        clear=MagicMock(),
        close=MagicMock(),
    )
    instance = _presence_without_init(discord_env, loop=True, RPC=rpc)
    instance.RPC_LOCK_TIMEOUT = 0.05
    monkeypatch.setattr(
        discord_env.module.time, "sleep", _stop_after_one_update(instance)
    )

    worker = threading.Thread(target=instance.update_presence)
    worker.start()
    assert inside_update.wait(2), "worker never entered the RPC"

    started = time.monotonic()
    instance.stop()  # sync_did_finish, on the main thread
    elapsed = time.monotonic() - started

    rpc.clear.assert_not_called()
    assert elapsed < 2, "stop() must not block the UI thread on the round-trip"
    # Skipped, not torn down: the connection is healthy, the worker is just busy.
    assert instance.connected is True
    assert instance.RPC is rpc

    release.set()
    worker.join(timeout=2)


def test_reconnect_is_throttled_on_a_monotonic_clock(discord_env, monkeypatch):
    """Only failures arm the throttle, and it reads a monotonic clock: a
    backward wall-clock step (resume from sleep, an NTP correction) must not
    block reconnection for the size of the jump."""
    now = {"t": 1000.0}
    _patch_clock(discord_env, monkeypatch, monotonic=lambda: now["t"])
    attempts = {"n": 0}

    class _FlakyPresence:
        def __init__(self, client_id):
            pass

        def connect(self):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("Discord is closed")

    monkeypatch.setattr(discord_env.module, "Presence", _FlakyPresence)
    instance = _presence_without_init(
        discord_env, connected=False, _first_attempt=True
    )

    assert instance._connect() is False  # the first attempt is never throttled
    assert attempts["n"] == 1

    now["t"] += 30
    assert instance._connect() is False, "inside the interval: no socket touched"
    assert attempts["n"] == 1

    now["t"] += 31
    assert instance._connect() is True  # interval elapsed, Discord is up now
    assert attempts["n"] == 2
    assert instance.connected is True

    # A success must not arm the throttle, or a drop seconds later would sit out
    # the whole interval before the first retry.
    instance.connected = False
    instance.RPC = None
    now["t"] += 1
    assert instance._connect() is True
    assert attempts["n"] == 3


def test_no_thread_per_card_while_discord_is_closed(discord_env, monkeypatch):
    """The throttle is checked in start() as well as in the worker, so a session
    spent with Discord closed doesn't create a thread per answered card only for
    it to fail the same test and exit."""
    now = {"t": 1000.0}
    _patch_clock(discord_env, monkeypatch, monotonic=lambda: now["t"])
    spawned = []

    class _FailingPresence:
        def __init__(self, client_id):
            pass

        def connect(self):
            raise RuntimeError("Discord is closed")

    monkeypatch.setattr(discord_env.module, "Presence", _FailingPresence)

    real_thread = threading.Thread

    class _CountingThread(real_thread):
        def start(self):
            spawned.append(1)
            super().start()

    monkeypatch.setattr(discord_env.module.threading, "Thread", _CountingThread)

    instance = _presence_without_init(
        discord_env, connected=False, _first_attempt=True
    )
    for _ in range(50):  # 50 answered cards, all within one throttle window
        instance.start()
        if instance.thread is not None:
            instance.thread.join(timeout=2)
        now["t"] += 1

    assert len(spawned) == 1


def test_reconnect_failures_are_logged_at_a_level_the_logger_records(discord_env, monkeypatch):
    """The quiet retries used to log at "debug", which ShowInfoLogger._record
    has no branch for — so nothing reached app.log and a user reporting
    "presence never came back" had no record of them."""
    _patch_clock(discord_env, monkeypatch)

    class _FailingPresence:
        def __init__(self, client_id):
            pass

        def connect(self):
            raise RuntimeError("Discord is closed")

    monkeypatch.setattr(discord_env.module, "Presence", _FailingPresence)
    instance = _presence_without_init(
        discord_env, connected=False, _first_attempt=False
    )

    assert instance._connect() is False

    levels = {call.args[0] for call in discord_env.logger.log.call_args_list}
    assert levels, "the failure must be logged"
    assert levels <= RECORDED_LOG_LEVELS
    # Quiet: no popup for a background retry.
    discord_env.event_emit.assert_not_called()
    assert discord_env.taskman.callbacks == []


def test_worker_death_does_not_wedge_start(discord_env, monkeypatch):
    """start() gates on thread liveness, not on self.loop. A worker that dies
    with loop still True used to make every later start() a no-op — presence was
    then dead until Anki restarted."""
    _patch_clock(discord_env, monkeypatch)
    monkeypatch.setattr(
        discord_env.module,
        "check_conflicting_discord_addons",
        MagicMock(side_effect=RuntimeError("addon manager exploded")),
    )

    class _RPC:
        def __init__(self, client_id):
            pass

        def connect(self):
            return None

        def update(self, **_kwargs):
            return None

        def close(self):
            return None

    monkeypatch.setattr(discord_env.module, "Presence", _RPC)
    instance = _presence_without_init(
        discord_env, connected=False, _checked_conflicts=False
    )
    monkeypatch.setattr(
        discord_env.module.time, "sleep", _stop_after_one_update(instance)
    )

    instance.start()
    instance.thread.join(timeout=2)
    assert instance.thread.is_alive() is False
    assert instance.loop is False, "a dead worker never looks like a running one"

    # The next answered card starts a fresh worker rather than no-opping.
    first = instance.thread
    instance.start()
    instance.thread.join(timeout=2)
    assert instance.thread is not first


def test_worker_restart_after_failure_reconnects(discord_env, monkeypatch):
    """A card answer that lands right after the worker's failure handler must
    end with a fresh, usable RPC that is actually pushing updates — not a
    half-torn-down one, and not a worker that connects and then does nothing."""
    connect_calls = {"n": 0}

    class _Presence:
        def __init__(self, client_id):
            self.update = MagicMock()
            self.clear = MagicMock()
            self.close = MagicMock()

        def connect(self):
            connect_calls["n"] += 1

    monkeypatch.setattr(discord_env.module, "Presence", _Presence)

    instance = _presence_without_init(discord_env)
    # End the worker's loop after one iteration instead of the real 30s sleep,
    # so the thread finishes and its final state can be asserted.
    _patch_clock(discord_env, monkeypatch, sleep=_stop_after_one_update(instance))

    # 1) the worker hits a dead pipe mid-session and tears its own state down.
    instance.loop = True
    instance.RPC = types.SimpleNamespace(
        update=MagicMock(side_effect=BrokenPipeError("connection lost")),
        close=MagicMock(),
    )
    instance.update_presence()
    assert instance.loop is False
    assert instance.connected is False
    assert instance.RPC is None

    # 2) the next card answer restarts the worker; it reconnects cleanly.
    instance.start()
    instance.thread.join(timeout=2)

    assert instance.thread.is_alive() is False
    assert connect_calls["n"] == 1
    assert instance.connected is True
    assert instance.RPC is not None
    assert instance.RPC.update.call_count == 1, "the worker must actually push a state"


def test_reconnect_does_not_reset_the_elapsed_timer(discord_env, monkeypatch):
    """Discord renders start_time as "elapsed". Re-stamping it on every
    reconnect snaps it back to 0:00 for someone who has been studying for hours."""

    class _Presence:
        def __init__(self, client_id):
            self.update = MagicMock()
            self.close = MagicMock()

        def connect(self):
            return None

    monkeypatch.setattr(discord_env.module, "Presence", _Presence)
    instance = _presence_without_init(discord_env, connected=False, start_time=12345.0)
    _patch_clock(discord_env, monkeypatch, sleep=_stop_after_one_update(instance))

    assert instance._connect() is True

    assert instance.start_time == 12345.0
