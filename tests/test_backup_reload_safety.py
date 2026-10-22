"""Save publication keeps its ownership when developer reload replaces modules."""

import importlib.util
import sqlite3
import sys
import threading
from contextlib import closing
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src" / "Ankimon"


def load_importer():
    spec = importlib.util.spec_from_file_location(
        "reload_save_import_under_test", SOURCE / "save_import.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_save(path, marker):
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript(
            "CREATE TABLE captured_pokemon (individual_id TEXT, data TEXT);"
            "CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT);"
        )
        connection.execute("INSERT INTO config VALUES ('trainer.name', ?)", (marker,))
    return path


@pytest.mark.parametrize("operation", ["stage", "cancel"])
def test_reloaded_importer_serializes_against_old_worker(
    tmp_path, monkeypatch, operation
):
    original = load_importer()
    target = make_save(tmp_path / "ankimon.db", "live")
    old_source = make_save(tmp_path / "old.db", "old")
    new_source = make_save(tmp_path / "new.db", "new")
    original.commit_pending_import(target)
    entered = threading.Event()
    release = threading.Event()
    next_started = threading.Event()
    next_finished = threading.Event()
    retired = threading.Event()
    results = {}
    prepare = original._prepare_incoming

    def paused_prepare(*args, **kwargs):
        prepare(*args, **kwargs)
        entered.set()
        assert release.wait(5), "test did not release the old restore"

    def validate():
        if retired.is_set():
            raise RuntimeError("outgoing backup manager retired")

    def old_restore():
        try:
            results["old"] = original.stage_import(
                old_source, target, before_publish=validate
            )
        except Exception as error:
            results["old_error"] = error

    monkeypatch.setattr(original, "_prepare_incoming", paused_prepare)
    old_worker = threading.Thread(target=old_restore)
    old_worker.start()
    next_worker = None
    try:
        assert entered.wait(5)
        replacement = load_importer()
        # The module was replaced; its lock and process startup gate survive.
        assert replacement is not original
        assert replacement._STAGING_LOCK is original._STAGING_LOCK
        assert replacement.commit_pending_import(target) is False
        if operation == "stage":
            retired.set()

        def next_operation():
            next_started.set()
            try:
                results["next"] = (
                    replacement.stage_import(new_source, target)
                    if operation == "stage"
                    else replacement.cancel_pending_import(target)
                )
            except Exception as error:
                results["next_error"] = error
            finally:
                next_finished.set()

        next_worker = threading.Thread(target=next_operation)
        next_worker.start()
        assert next_started.wait(5)
        if operation == "stage":
            assert next_finished.wait(1), "a competing request waited for disk work"
        else:
            assert not next_finished.wait(0.1)
    finally:
        release.set()
        old_worker.join(5)
        if next_worker is not None:
            next_worker.join(5)
    assert not old_worker.is_alive()
    assert next_worker is not None and not next_worker.is_alive()
    if operation == "stage":
        assert isinstance(results.pop("next_error"), replacement.SaveOperationBusyError)
        assert isinstance(results.get("old_error"), RuntimeError)
        assert "old" not in results
        results["next"] = replacement.stage_import(new_source, target)
        info = replacement.pending_import_info(target)
        assert info["token"] == results["next"]["token"]
        with closing(sqlite3.connect(info["pending_path"])) as connection:
            assert connection.execute(
                "SELECT value FROM config WHERE key='trainer.name'"
            ).fetchone() == ("new",)
        assert list(info["pending_path"].parent.glob("*.db")) == [info["pending_path"]]
    else:
        assert "next_error" not in results
        info = replacement.pending_import_info(target)
        assert "old_error" not in results
        assert results["next"] is True
        assert info is None
    with closing(sqlite3.connect(target)) as connection:
        assert connection.execute(
            "SELECT value FROM config WHERE key='trainer.name'"
        ).fetchone() == ("live",)


def test_request_owner_survives_reload_and_an_old_release_cannot_free_a_new_owner(
    tmp_path,
):
    original = load_importer()
    first = original.reserve_save_operation()
    replacement = load_importer()
    try:
        with pytest.raises(replacement.SaveOperationBusyError):
            replacement.reserve_save_operation()
        released = threading.Thread(target=first.release)
        released.start()
        released.join(5)
        assert not released.is_alive()
        second = replacement.reserve_save_operation()
        try:
            first.release()
            assert replacement.save_operation_busy()
            target = make_save(tmp_path / "ankimon.db", "live")
            source = make_save(tmp_path / "incoming.db", "incoming")
            # Borrowed ownership is duck typed across module generations.
            original.stage_import(source, target, operation=second)
            assert replacement.save_operation_busy()
            with pytest.raises(original.SaveOperationBusyError):
                original.reserve_save_operation()
        finally:
            second.release()
        assert not original.save_operation_busy()
        with pytest.raises(RuntimeError, match="finished"):
            second.validate()
    finally:
        first.release()


def test_teardown_retires_manager_before_any_event_pump_or_widget_delete(monkeypatch):
    calls = []
    addon = ModuleType("reload_test_addon")
    addon.backup_manager = SimpleNamespace(
        invalidate_context=lambda: calls.append("invalidate")
    )
    services_module = ModuleType("reload_test_addon.services")
    services_module.services = SimpleNamespace()
    background_module = ModuleType("reload_test_addon.background_ops")
    background_module.cancel_pending_callbacks = lambda: calls.append("cancel_idle")
    database_module = ModuleType("reload_test_addon.pyobj.database_manager")
    database_module.reset_db = lambda: calls.append("reset_db")
    aqt = ModuleType("aqt")
    aqt.gui_hooks = SimpleNamespace()
    aqt.mw = SimpleNamespace()
    qtcore = ModuleType("PyQt6.QtCore")
    qtcore.QCoreApplication = SimpleNamespace()
    qtcore.QEvent = SimpleNamespace()
    qtwidgets = ModuleType("PyQt6.QtWidgets")
    qtwidgets.QApplication = SimpleNamespace()
    qtwidgets.QWidget = type("QWidget", (), {})
    for name, module in (
        (addon.__name__, addon),
        (services_module.__name__, services_module),
        (background_module.__name__, background_module),
        (database_module.__name__, database_module),
        ("aqt", aqt),
        ("PyQt6.QtCore", qtcore),
        ("PyQt6.QtWidgets", qtwidgets),
    ):
        monkeypatch.setitem(sys.modules, name, module)
    spec = importlib.util.spec_from_file_location(
        "reload_test_addon.reloader", SOURCE / "reloader.py"
    )
    reloader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reloader)
    steps = (
        "_remove_addon_gui_hooks",
        "_restore_reviewer_wraps",
        "_delete_reload_shortcuts",
        "_teardown_windows",
        "_flush_deferred_widget_deletes",
        "_purge_addon_modules",
    )
    for name in steps:
        monkeypatch.setattr(reloader, name, lambda *args, name=name: calls.append(name))
    # Repeat to cover the second reload, whose database module cache is empty.
    for _ in range(2):
        calls.clear()
        reloader.teardown_ankimon(addon.__name__)
        assert calls == ["cancel_idle", "invalidate", *steps[:-1], "reset_db", steps[-1]]
