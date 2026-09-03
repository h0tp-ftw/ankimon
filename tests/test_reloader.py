"""Regression tests for the developer hot-reload lifecycle."""

import importlib.util
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace


_SRC = Path(__file__).parent.parent / "src"


def _module(name, **attrs):
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


def _load_reloader(monkeypatch, services, calls, on_process_events=None):
    """Import reloader.py against fake Qt/aqt modules and return the module.

    ``on_process_events`` is invoked by the fake ``QApplication.processEvents``,
    which is how a test drives what happens while ``restart_ankimon`` is
    pumping the Qt event queue (startup finishing, a re-entrant trigger, …).
    """

    class FakeApplication:
        @staticmethod
        def processEvents():
            calls.append("process_events")
            if on_process_events is not None:
                on_process_events()

        @staticmethod
        def allWidgets():
            return []

    pyqt6 = _module("PyQt6")
    pyqt6.__path__ = []
    qtwidgets = _module(
        "PyQt6.QtWidgets",
        QApplication=FakeApplication,
        QWidget=type("QWidget", (), {}),
    )
    pyqt6.QtWidgets = qtwidgets
    monkeypatch.setitem(sys.modules, "PyQt6", pyqt6)
    monkeypatch.setitem(sys.modules, "PyQt6.QtWidgets", qtwidgets)

    aqt = _module("aqt", gui_hooks=SimpleNamespace(), mw=SimpleNamespace())
    aqt.__path__ = []
    monkeypatch.setitem(sys.modules, "aqt", aqt)
    monkeypatch.setitem(
        sys.modules,
        "aqt.utils",
        _module("aqt.utils", tooltip=lambda message: calls.append(("tooltip", message))),
    )
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.services",
        _module("Ankimon.services", services=services),
    )

    spec = importlib.util.spec_from_file_location(
        "Ankimon.reloader", _SRC / "Ankimon" / "reloader.py"
    )
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "Ankimon.reloader", mod)
    spec.loader.exec_module(mod)

    monkeypatch.setattr(
        mod,
        "teardown_ankimon",
        lambda addon_package: calls.append(("teardown", addon_package)),
    )
    monkeypatch.setattr(
        mod.importlib,
        "import_module",
        lambda addon_package: calls.append(("import", addon_package)),
    )
    monkeypatch.setattr(time, "sleep", lambda delay: calls.append(("sleep", delay)))
    return mod


def _fake_clock(monkeypatch):
    """Make time.monotonic() advance only when the reload sleeps."""
    now = {"t": 0.0}
    monkeypatch.setattr(time, "monotonic", lambda: now["t"])
    monkeypatch.setattr(
        time, "sleep", lambda delay: now.__setitem__("t", now["t"] + delay)
    )
    return now


def test_restart_waits_for_startup_before_teardown(monkeypatch):
    calls = []
    services = SimpleNamespace(_startup_in_progress=True)

    def finish_startup():
        services._startup_in_progress = False

    mod = _load_reloader(monkeypatch, services, calls, finish_startup)

    mod.restart_ankimon()

    assert calls[:4] == [
        "process_events",
        ("sleep", 0.02),
        ("teardown", "Ankimon"),
        ("import", "Ankimon"),
    ]
    assert calls[-1] == ("tooltip", "Ankimon reloaded.")
    # The asynchronous startup callbacks own clearing this flag.
    assert services._is_reloading is True
    # The guard is released so the next Ctrl+Shift+R still works.
    assert services._reload_in_progress is False


def test_restart_aborts_when_startup_never_finishes(monkeypatch):
    """A wedged startup must cost a tooltip, not a permanently frozen Anki."""
    calls = []
    services = SimpleNamespace(_startup_in_progress=True)
    mod = _load_reloader(monkeypatch, services, calls)
    _fake_clock(monkeypatch)

    mod.restart_ankimon()

    assert ("teardown", "Ankimon") not in calls
    assert ("import", "Ankimon") not in calls
    assert calls[-1] == ("tooltip", "Ankimon reload skipped — startup still running.")
    # Aborting must not leave the reload wedged for the next attempt either.
    assert services._reload_in_progress is False
    assert getattr(services, "_is_reloading", False) is False


def test_restart_ignores_a_reentrant_trigger(monkeypatch):
    """processEvents() can re-fire Ctrl+Shift+R; the second call must no-op."""
    calls = []
    services = SimpleNamespace(_startup_in_progress=True)
    holder = {}

    def reenter():
        services._startup_in_progress = False
        holder["mod"].restart_ankimon()

    mod = _load_reloader(monkeypatch, services, calls, reenter)
    holder["mod"] = mod

    mod.restart_ankimon()

    assert [c for c in calls if c == ("teardown", "Ankimon")] == [
        ("teardown", "Ankimon")
    ]
    assert [c for c in calls if c == ("import", "Ankimon")] == [("import", "Ankimon")]
    assert services._reload_in_progress is False


def test_restart_clears_reload_flag_when_teardown_fails(monkeypatch):
    calls = []
    services = SimpleNamespace(_startup_in_progress=False)
    mod = _load_reloader(monkeypatch, services, calls)
    monkeypatch.setattr(
        mod,
        "teardown_ankimon",
        lambda addon_package: (_ for _ in ()).throw(RuntimeError("teardown boom")),
    )

    mod.restart_ankimon()

    assert ("import", "Ankimon") not in calls
    assert calls[-1] == ("tooltip", "Ankimon reload failed — see console.")
    # No replacement startup was ever scheduled, so nothing else would clear
    # this; leaving it set would suppress backups for the rest of the session.
    assert services._is_reloading is False
    assert services._reload_in_progress is False


def test_restart_clears_reload_flag_when_reimport_fails(monkeypatch):
    """The common case: the developer saved a file with a syntax error."""
    calls = []
    services = SimpleNamespace(_startup_in_progress=False)
    mod = _load_reloader(monkeypatch, services, calls)
    monkeypatch.setattr(
        mod.importlib,
        "import_module",
        lambda addon_package: (_ for _ in ()).throw(SyntaxError("bad edit")),
    )

    mod.restart_ankimon()

    assert ("teardown", "Ankimon") in calls
    assert calls[-1] == ("tooltip", "Ankimon reload failed — see console.")
    assert services._is_reloading is False
    assert services._reload_in_progress is False


def test_restart_leaves_reload_flag_to_the_replacement_startup(monkeypatch):
    """If the re-import scheduled a new startup, that startup owns the flag."""
    calls = []
    services = SimpleNamespace(_startup_in_progress=False)

    def reimport(addon_package):
        calls.append(("import", addon_package))
        # What __init__.py does on re-import: kick off the new startup QueryOp.
        services._startup_in_progress = True

    def failing_tooltip(message):
        calls.append(("tooltip", message))
        raise RuntimeError("mw is going away")

    mod = _load_reloader(monkeypatch, services, calls)
    monkeypatch.setattr(mod.importlib, "import_module", reimport)
    monkeypatch.setitem(
        sys.modules, "aqt.utils", _module("aqt.utils", tooltip=failing_tooltip)
    )

    mod.restart_ankimon()

    # Clearing _is_reloading here would let the replacement startup run the
    # backups that the hot-reload path exists to skip.
    assert services._is_reloading is True
    assert services._reload_in_progress is False
