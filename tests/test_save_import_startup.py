"""Import outcome reporting across the database factory and profile hook."""

import importlib
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from test_profile_hooks import _exec_profile_hooks, _fresh_gui_hooks, _fresh_services
from test_save_import import child, make_save


@pytest.mark.parametrize("installed", [False, True])
def test_startup_reports_the_save_that_is_active(tmp_path, monkeypatch, installed):
    importer = importlib.import_module("Ankimon.save_import")
    manager = importlib.import_module("Ankimon.pyobj.database_manager")
    events_module = importlib.import_module("Ankimon.events")
    event_bus = events_module._EventBus()
    event_bus.enable()
    monkeypatch.setattr(events_module, "events", event_bus)
    services = _fresh_services(monkeypatch)
    monkeypatch.setattr(manager, "_db_instance", None)
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    for path in (target, source):
        # Supply the real database manager's indexed columns and normalization
        # marker, so constructing it needs no external Pokemon assets.
        with sqlite3.connect(path) as conn:
            for column in ("name", "pokedex_id", "shiny", "level", "is_main"):
                conn.execute(f"ALTER TABLE captured_pokemon ADD COLUMN {column} TEXT")
            conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("INSERT INTO metadata VALUES ('base_stats_normalized', 'true')")
    child("module.stage_import(Path(sys.argv[3]), target)\n", target, source)
    replace, sync = os.replace, importer._fsync_directory

    def fail_replace(source, destination):
        if Path(destination) == target:
            raise OSError("injected replacement failure")
        return replace(source, destination)

    def fail_sync(path):
        if path == target.parent and importer._installed_token(target) is not None:
            raise OSError("injected final sync failure")
        return sync(path)

    if installed:
        monkeypatch.setattr(importer, "_fsync_directory", fail_sync)
    else:
        monkeypatch.setattr(importer.os, "replace", fail_replace)
    logs, warnings = [], []
    logger = SimpleNamespace(log=lambda level, message: logs.append((level, message)))
    services.ui = SimpleNamespace(warn=warnings.append)
    runtime = manager.get_db(logger=logger, db_path=target)
    try:
        assert runtime.get_config_value("trainer.name") == ("incoming" if installed else "local")
        failures = getattr(services, "_save_import_errors", [])
        finalization = getattr(services, "_save_import_warnings", [])
        assert bool(failures) is not installed
        assert bool(finalization) is installed
        import_events = [event for event in event_bus.peek()
                         if event["type"] == "save_import_installed"]
        assert len(import_events) == int(installed)
        if installed:
            assert import_events[0]["target"] == str(target)

        hooks = _exec_profile_hooks(monkeypatch, _fresh_gui_hooks())
        hooks.mw.col = None
        hooks.mw.taskman = SimpleNamespace(run_in_background=lambda *args: None)
        hooks._on_profile_did_open(False)()
        assert len(warnings) == 1
        message = warnings[0].lower()
        if installed:
            assert "imported save" in message and "active" in message
            assert "could not install" not in message
            assert "injected final sync failure" in message
            assert any(level == "warning" and "injected final sync failure" in text
                       for level, text in logs)
            assert not any("could not be installed" in text for _, text in logs)
        else:
            assert "could not install" in message
            assert "injected replacement failure" in message
        assert not getattr(services, "_save_import_errors", [])
        assert not getattr(services, "_save_import_warnings", [])
        hooks._on_profile_did_open(False)()
        assert len(warnings) == 1
    finally:
        runtime.close()
