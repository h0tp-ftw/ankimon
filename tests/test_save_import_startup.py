"""Import outcome reporting across the database factory and profile hook."""

import importlib
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from test_profile_hooks import _exec_profile_hooks, _fresh_gui_hooks, _fresh_services
from test_save_import import child, make_save, names


def _runtime_saves(tmp_path):
    """A local and an incoming save that the real database manager can open.

    Both get its indexed columns and normalization marker, so constructing it
    needs no external Pokemon assets.
    """
    target = make_save(tmp_path / "ankimon.db", "local")
    source = make_save(tmp_path / "source.db", "incoming")
    for path in (target, source):
        with sqlite3.connect(path) as conn:
            conn.executescript(
                "ALTER TABLE captured_pokemon ADD COLUMN name TEXT;"
                "ALTER TABLE captured_pokemon ADD COLUMN pokedex_id TEXT;"
                "ALTER TABLE captured_pokemon ADD COLUMN shiny TEXT;"
                "ALTER TABLE captured_pokemon ADD COLUMN level TEXT;"
                "ALTER TABLE captured_pokemon ADD COLUMN is_main TEXT;"
            )
            conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("INSERT INTO metadata VALUES ('base_stats_normalized', 'true')")
    return target, source


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
    target, source = _runtime_saves(tmp_path)
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
            # Only the listed saves were left alone: get_db tries both modes,
            # and the other may have been replaced in this same start.
            assert "no save was replaced" not in message
            assert "ankimon → game → cancel pending save import" in message
        assert not getattr(services, "_save_import_errors", [])
        assert not getattr(services, "_save_import_warnings", [])
        hooks._on_profile_did_open(False)()
        assert len(warnings) == 1
    finally:
        runtime.close()


def test_failed_warning_delivery_keeps_the_notice_and_registers_sync_hooks(monkeypatch):
    """A raising presenter must not silence an import outcome or skip the hooks."""
    services = _fresh_services(monkeypatch)
    services._save_import_errors = ["ankimon.db: injected replacement failure"]
    services._save_import_warnings = ["ankimonDEV.db: injected final sync failure"]
    delivered = []

    def refuse(message):
        delivered.append(message)
        raise RuntimeError("the warning dialog could not be shown")

    services.ui = SimpleNamespace(warn=refuse)
    hooks = _exec_profile_hooks(monkeypatch, _fresh_gui_hooks())
    hooks.mw.col = None
    hooks.mw.taskman = SimpleNamespace(run_in_background=lambda *args: None)
    hooks._on_profile_did_open(False)()

    # Both notices were attempted, both survive for the next profile open
    # instead of being cleared into nothing, and the AnkiWeb sync hooks that
    # follow them still registered.
    assert len(delivered) == 2
    assert "could not install" in delivered[0].lower()
    assert "are active" in delivered[1].lower()
    assert services._save_import_errors == ["ankimon.db: injected replacement failure"]
    assert services._save_import_warnings == ["ankimonDEV.db: injected final sync failure"]
    assert hooks.setup_ankimon_sync_hooks.called


def test_startup_installs_an_import_whose_save_was_deleted(tmp_path, monkeypatch):
    """No fresh save may be created in its place and replaced a start later.

    Refusing the install let the database manager create a new save in the same
    start, and the following start installed the import over it without a word.
    """
    importer = importlib.import_module("Ankimon.save_import")
    manager = importlib.import_module("Ankimon.pyobj.database_manager")
    events_module = importlib.import_module("Ankimon.events")
    event_bus = events_module._EventBus()
    event_bus.enable()
    monkeypatch.setattr(events_module, "events", event_bus)
    services = _fresh_services(monkeypatch)
    monkeypatch.setattr(manager, "_db_instance", None)
    target, source = _runtime_saves(tmp_path)
    child("module.stage_import(Path(sys.argv[3]), target)\n", target, source)
    target.unlink()

    runtime = manager.get_db(logger=None, db_path=target)
    try:
        assert runtime.get_config_value("trainer.name") == "incoming"
        assert not getattr(services, "_save_import_errors", [])
        assert importer.pending_import_info(target) is None
        assert [event["target"] for event in event_bus.peek()
                if event["type"] == "save_import_installed"] == [str(target)]
    finally:
        runtime.close()


@pytest.mark.parametrize("suffixes", [("-journal",), ("-wal", "-shm")])
def test_startup_never_opens_a_fresh_save_over_journals_it_could_not_set_aside(
    tmp_path, monkeypatch, suffixes,
):
    """Journals beside a deleted save may be all that is left of its progress.

    The install moves them into its recovery folder before anything else. When
    that move failed, get_db recorded the failure and opened the path anyway,
    creating a fresh save there, and SQLite discarded the journals beside it.
    An add-on reload after that refused start makes no install attempt, since
    this process already tried, and did the same; so the refusal has to come
    from the files on disk.
    """
    importer = importlib.import_module("Ankimon.save_import")
    manager = importlib.import_module("Ankimon.pyobj.database_manager")
    _fresh_services(monkeypatch)
    monkeypatch.setattr(manager, "_db_instance", None)
    target, source = _runtime_saves(tmp_path)
    child("module.stage_import(Path(sys.argv[3]), target)\n", target, source)
    if "-wal" in suffixes:
        # Progress committed to the WAL by a writer that never checkpointed.
        child(
            "conn = sqlite3.connect(target)\n"
            "conn.execute('PRAGMA journal_mode=WAL')\n"
            "conn.execute('PRAGMA wal_autocheckpoint=0')\n"
            "conn.execute(\"INSERT INTO captured_pokemon (individual_id, data) "
            "VALUES ('late-commit', '{}')\")\n"
            "conn.commit()\n"
            "os._exit(0)\n", target,
        )
    else:
        Path(str(target) + "-journal").write_bytes(b"unfinished transaction" * 64)
    target.unlink()
    journals = {Path(str(target) + suffix): Path(str(target) + suffix).read_bytes()
                for suffix in suffixes if Path(str(target) + suffix).exists()}
    assert Path(str(target) + suffixes[0]) in journals
    # A file where the recovery folder belongs: mkdir refuses it, so the
    # journals cannot be moved anywhere.
    blocker = tmp_path / "ankimon_recovery"
    blocker.write_text("not a folder")

    messages = []
    for _attempt in range(2):
        with pytest.raises(importer.ImportUnsafeToOpenError) as refused:
            manager.get_db(logger=None, db_path=target)
        messages.append(str(refused.value))
        assert manager._db_instance is None
        assert not target.exists()
        assert {path: path.read_bytes() for path in journals} == journals
        assert importer.pending_import_info(target) is not None
    for message in messages:
        assert all(path.name in message for path in journals)
        assert str(blocker) in message
    # The first attempt tried to move them and says why it could not. The
    # second, a reload after that refused start, is turned away by this
    # process's attempt gate before it tries, and is refused from the files.
    assert "It could not:" in messages[0]
    assert "It could not:" not in messages[1]

    # Once the folder can be used, the next full start sets them aside and installs.
    blocker.unlink()
    child("assert module.commit_pending_import(target) is True\n", target)
    assert names(target) == ["incoming"]
    assert importer.pending_import_info(target) is None
    kept = [path for path in (tmp_path / "ankimon_recovery").rglob("*") if path.is_file()]
    assert sorted(path.read_bytes() for path in kept) == sorted(journals.values())


def test_startup_never_opens_a_fresh_save_over_journals_beside_a_damaged_import_record(
    tmp_path, monkeypatch,
):
    """A record that cannot be read never reaches the step that sets journals aside."""
    importer = importlib.import_module("Ankimon.save_import")
    manager = importlib.import_module("Ankimon.pyobj.database_manager")
    _fresh_services(monkeypatch)
    monkeypatch.setattr(manager, "_db_instance", None)
    target, source = _runtime_saves(tmp_path)
    child("module.stage_import(Path(sys.argv[3]), target)\n", target, source)
    target.unlink()
    journal = Path(str(target) + "-journal")
    journal.write_bytes(b"unfinished transaction" * 64)
    (tmp_path / f".ankimon-import-{target.name}" / "pending.json").write_text("{damaged")

    with pytest.raises(importer.ImportUnsafeToOpenError) as refused:
        manager.get_db(logger=None, db_path=target)
    assert manager._db_instance is None
    assert not target.exists()
    assert journal.read_bytes() == b"unfinished transaction" * 64
    assert journal.name in str(refused.value)


def test_switching_saves_never_opens_a_fresh_save_over_journals_of_a_pending_import(
    tmp_path, monkeypatch,
):
    """Developer mode opens ankimonDEV.db long after startup has run."""
    importer = importlib.import_module("Ankimon.save_import")
    manager = importlib.import_module("Ankimon.pyobj.database_manager")
    _fresh_services(monkeypatch)
    monkeypatch.setattr(manager, "user_path", tmp_path)
    target, source = _runtime_saves(tmp_path)
    developer = make_save(tmp_path / "ankimonDEV.db", "developer")
    child("module.stage_import(Path(sys.argv[3]), target)\n", developer, source)
    developer.unlink()
    journal = Path(str(developer) + "-journal")
    journal.write_bytes(b"unfinished transaction" * 64)
    (tmp_path / "ankimon_recovery").write_text("not a folder")

    runtime = manager.AnkimonDB(None, db_path=target)
    try:
        with pytest.raises(importer.ImportUnsafeToOpenError) as refused:
            runtime.switch_database("ankimonDEV.db")
        assert "will not open ankimonDEV.db" in str(refused.value)
        assert runtime.db_path == target
        assert runtime.get_config_value("trainer.name") == "local"
    finally:
        runtime.close()
    assert not developer.exists()
    assert journal.read_bytes() == b"unfinished transaction" * 64
    assert importer.pending_import_info(developer) is not None


def test_the_save_startup_does_not_open_keeps_its_journals_through_the_notice_and_cancel(
    tmp_path, monkeypatch,
):
    """Startup opens one save; the other's failed install is only reported.

    That report named the failed move and recommended Cancel Pending Save Import,
    and cancelling retired the record that kept switch_database from opening a
    fresh save over the journals. The report now says the journals are at stake,
    and Cancel moves them aside first, cancelling nothing when it cannot.
    """
    importer = importlib.import_module("Ankimon.save_import")
    manager = importlib.import_module("Ankimon.pyobj.database_manager")
    services = _fresh_services(monkeypatch)
    monkeypatch.setattr(manager, "user_path", tmp_path)
    monkeypatch.setattr(manager, "_db_instance", None)
    target, source = _runtime_saves(tmp_path)
    developer = make_save(tmp_path / "ankimonDEV.db", "developer")
    child("module.stage_import(Path(sys.argv[3]), target)\n", developer, source)
    developer.unlink()
    journal = Path(str(developer) + "-journal")
    journal.write_bytes(b"unfinished transaction" * 64)
    blocker = tmp_path / "ankimon_recovery"
    blocker.write_text("not a folder")

    runtime = manager.get_db(logger=None)
    try:
        assert runtime.db_path == target
        [reported] = [entry for entry in services._save_import_errors
                      if "ankimonDEV.db" in entry]
        assert journal.name in reported and "will not open ankimonDEV.db" in reported

        with pytest.raises(OSError, match="Nothing was cancelled"):
            importer.cancel_pending_import(developer)
        assert importer.pending_import_info(developer) is not None
        with pytest.raises(importer.ImportUnsafeToOpenError):
            runtime.switch_database("ankimonDEV.db")
        assert journal.read_bytes() == b"unfinished transaction" * 64

        blocker.unlink()
        assert importer.cancel_pending_import(developer) is True
        assert importer.pending_import_info(developer) is None
        assert not journal.exists()
        kept = [path for path in blocker.rglob("*") if path.is_file()]
        assert [path.read_bytes() for path in kept] == [b"unfinished transaction" * 64]
    finally:
        runtime.close()
