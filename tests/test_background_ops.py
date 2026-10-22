"""Scheduling regressions for startup work that must not hold Anki's sync queue."""

from concurrent.futures import Future, ThreadPoolExecutor
import importlib.util
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def env(monkeypatch):
    timers = []
    services = SimpleNamespace()
    mw = SimpleNamespace(col=object(), progress=SimpleNamespace(busy=lambda: False))
    application = SimpleNamespace(activeModalWidget=lambda: None)
    for name, attrs in {
        "aqt": {"mw": mw},
        "aqt.qt": {
            "QApplication": application,
            "QTimer": SimpleNamespace(singleShot=lambda ms, cb: timers.append(cb)),
        },
        "aqt.operations": {"QueryOp": type("LegacyQueryOp", (), {})},
        "Ankimon.services": {"services": services},
    }.items():
        mod = ModuleType(name)
        mod.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, mod)
    spec = importlib.util.spec_from_file_location(
        "Ankimon.background_ops",
        Path(__file__).parents[1] / "src/Ankimon/background_ops.py",
    )
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, mod)
    spec.loader.exec_module(mod)
    return SimpleNamespace(
        mod=mod, mw=mw, services=services, timers=timers, app=application
    )


class LegacyTaskman:
    def __init__(self):
        self.calls = []

    def run_in_background(self, task, on_done=None, args=None):
        self.calls.append((task, on_done))


class ModernTaskman(LegacyTaskman):
    def run_in_background(self, task, on_done=None, args=None, uses_collection=True):
        self.uses_collection = uses_collection
        return super().run_in_background(task, on_done, args)


@pytest.mark.parametrize("taskman", [LegacyTaskman, ModernTaskman])
@pytest.mark.parametrize("fails", [False, True])
def test_query_legacy_host_callbacks_and_single_submission(env, taskman, fails):
    env.mw.taskman = taskman()
    successes, failures = [], []
    env.mod.run_query(lambda: 7, successes.append, failures.append)
    assert len(env.mw.taskman.calls) == 1
    if isinstance(env.mw.taskman, ModernTaskman):
        assert env.mw.taskman.uses_collection is False
    task, done = env.mw.taskman.calls[0]
    future = Future()
    if fails:
        future.set_exception(ValueError("work failed"))
    else:
        future.set_result(task())
    done(future)
    assert successes == ([] if fails else [7])
    assert len(failures) == int(fails)


def test_modern_query_uses_collection_free_api(env, monkeypatch):
    calls = []

    class ModernQueryOp:
        def __init__(self, *, parent, op, success):
            self.op, self.success = op, success

        def failure(self, callback):
            self.on_failure = callback
            return self

        def without_collection(self):
            calls.append("without_collection")
            return self

        def run_in_background(self):
            calls.append("submit")
            self.success(self.op(None))

    monkeypatch.setattr(sys.modules["aqt.operations"], "QueryOp", ModernQueryOp)
    env.mod.run_query(lambda: 42, calls.append, pytest.fail)
    assert calls == ["without_collection", "submit", 42]


def test_dispatch_typeerror_is_never_retried(env):
    submissions = []

    def submit(task, on_done=None, args=None, uses_collection=True):
        submissions.append(uses_collection)
        raise TypeError("failure after submission")

    env.mw.taskman = SimpleNamespace(run_in_background=submit)
    with pytest.raises(TypeError, match="after submission"):
        env.mod.run_query(lambda: None, lambda _: None, pytest.fail)
    assert submissions == [False]


def test_slow_addon_task_does_not_occupy_collection_executor(env):
    entered, release = threading.Event(), threading.Event()
    with (
        ThreadPoolExecutor(max_workers=1) as collection,
        ThreadPoolExecutor(max_workers=1) as other,
    ):

        def submit(task, on_done=None, args=None, uses_collection=True):
            return (collection if uses_collection else other).submit(task)

        env.mw.taskman = SimpleNamespace(run_in_background=submit)

        def slow_addon_task():
            entered.set()
            assert release.wait(3)

        worker = env.mod.run_in_background(slow_addon_task)
        try:
            assert entered.wait(1)
            # Anki's Checking/sync work must run while add-on I/O is stalled.
            assert (
                collection.submit(lambda: "sync complete").result(timeout=1)
                == "sync complete"
            )
        finally:
            release.set()
            worker.result(timeout=1)


def test_idle_gate_leaves_profile_open_stack_and_waits_for_sync(env):
    calls = []
    env.mod.call_when_anki_idle(lambda: calls.append("dialog"), "test")
    assert calls == []
    # Anki starts auto-sync after profile_did_open returns.
    env.mw.progress.busy = lambda: True
    env.timers.pop(0)()
    assert calls == []
    env.mw.progress.busy = lambda: False
    env.timers.pop(0)()
    assert calls == ["dialog"]
    assert env.services._idle_callbacks == set()


def test_idle_gate_never_opens_over_busy_progress_even_after_long_wait(
    env, monkeypatch
):
    now = [0]
    monkeypatch.setattr(env.mod.time, "monotonic", lambda: now[0])
    calls, logs = [], []
    env.services.logger = SimpleNamespace(log=lambda *args: logs.append(args))
    env.mw.progress.busy = lambda: True
    env.mod.call_when_anki_idle(lambda: calls.append("dialog"), "startup")
    now[0] = 300
    env.timers.pop(0)()
    assert calls == []
    assert len(logs) == 1
    env.mw.progress.busy = lambda: False
    env.timers.pop(0)()
    assert calls == ["dialog"]


def test_idle_gate_waits_for_collection_and_other_modal(env):
    calls = []
    env.mw.col = None
    env.mod.call_when_anki_idle(lambda: calls.append("dialog"), "startup")
    env.timers.pop(0)()
    assert calls == []
    env.mw.col = object()
    env.app.activeModalWidget = lambda: object()
    env.timers.pop(0)()
    assert calls == []
    env.app.activeModalWidget = lambda: None
    env.timers.pop(0)()
    assert calls == ["dialog"]


@pytest.mark.parametrize("change", ["close", "switch", "reload", "cancel"])
def test_stale_idle_callback_is_cancelled_once(env, change):
    calls = []
    cancel = env.mod.call_when_anki_idle(
        lambda: calls.append("dialog"), "test", on_cancel=lambda: calls.append("cancel")
    )
    if change == "close":
        env.mw.col = None
    elif change == "switch":
        env.mw.col = object()
    elif change == "reload":
        env.mod.cancel_pending_callbacks()
    else:
        cancel()
    env.timers.pop(0)()
    cancel()
    assert calls == ["cancel"]
    assert env.services._idle_callbacks == set()


def test_ui_exception_is_not_retained_for_retry(env):
    def fail():
        raise ValueError("dialog error")

    env.mod.call_when_anki_idle(fail, "test")
    with pytest.raises(ValueError, match="dialog error"):
        env.timers.pop(0)()
    assert env.services._idle_callbacks == set()
    assert env.timers == []


@pytest.mark.parametrize("taskman_type", [LegacyTaskman, ModernTaskman])
def test_background_submission_keeps_the_callers_task_manager(env, taskman_type):
    env.mw.taskman = LegacyTaskman()
    owner = taskman_type()
    callback = lambda future: None
    task = lambda: 7
    env.mod.run_in_background(task, callback, taskman=owner)
    assert owner.calls == [(task, callback)]
    assert env.mw.taskman.calls == []
    if isinstance(owner, ModernTaskman):
        assert owner.uses_collection is False
