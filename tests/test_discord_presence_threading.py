import importlib.util
import sys
import threading
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest


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


def _presence_without_init(env):
    instance = object.__new__(env.module.DiscordPresence)
    instance.logger_obj = env.logger
    return instance


def test_setup_failure_queues_non_modal_tooltip(discord_env, monkeypatch):
    class _FailingPresence:
        def __init__(self, client_id):
            pass

        def connect(self):
            raise RuntimeError("Discord is closed")

    monkeypatch.setattr(discord_env.module, "Presence", _FailingPresence)

    discord_env.module.DiscordPresence(
        "client-id",
        "image",
        object(),
        discord_env.logger,
        types.SimpleNamespace(get=lambda key: 1),
    )

    _assert_queued_tooltip(
        discord_env, "Error with Discord setup. Is Discord running?"
    )


def test_update_failure_queues_tooltip_from_worker(discord_env):
    main_thread = threading.get_ident()
    instance = _presence_without_init(discord_env)
    instance.loop = True
    instance.RPC = types.SimpleNamespace(
        update=MagicMock(side_effect=RuntimeError("connection lost"))
    )
    instance.settings = types.SimpleNamespace(get=lambda key: 1)
    instance.quotes = ["Reviewing"]
    instance.large_image_url = "image"
    instance.start_time = 0

    worker = threading.Thread(target=instance.update_presence)
    worker.start()
    worker.join()

    assert discord_env.taskman.calling_threads == [worker.ident]
    assert worker.ident != main_thread
    _assert_queued_tooltip(
        discord_env,
        "Error with Discord Rich Presence. Is Discord running?",
    )


def test_start_failure_queues_non_modal_tooltip(discord_env):
    instance = _presence_without_init(discord_env)
    instance.thread = types.SimpleNamespace(
        is_alive=MagicMock(side_effect=RuntimeError("thread failed"))
    )

    instance.start()

    _assert_queued_tooltip(
        discord_env,
        "Error starting Discord Rich Presence. Is Discord running?",
    )


def test_stop_failure_queues_non_modal_tooltip(discord_env):
    instance = _presence_without_init(discord_env)
    instance.loop = True
    instance.thread = None
    instance.RPC = types.SimpleNamespace(
        clear=MagicMock(side_effect=RuntimeError("clear failed"))
    )

    instance.stop()

    _assert_queued_tooltip(
        discord_env,
        "Error clearing Discord Rich Presence. Please check Logger for info.",
    )


def test_stop_presence_failure_queues_non_modal_tooltip(discord_env):
    instance = _presence_without_init(discord_env)
    instance.loop = True
    instance.RPC = types.SimpleNamespace(
        update=MagicMock(side_effect=RuntimeError("update failed"))
    )
    instance.large_image_url = "image"

    instance.stop_presence()

    _assert_queued_tooltip(
        discord_env,
        "Error stopping Discord Rich Presence. Please check Logger for info.",
    )
