"""A remembered migration answer applies only to the save actually shown."""

import os
import sqlite3
import sys
from types import ModuleType, SimpleNamespace

import pytest

from test_save_transfer import _Logger, _make_save, st
from Ankimon.pyobj import ankimon_sync
from Ankimon.services import services


@pytest.fixture
def migration(tmp_path, monkeypatch):
    registry = ModuleType("Ankimon.services")
    registry.services = services
    monkeypatch.setitem(sys.modules, "Ankimon.services", registry)
    monkeypatch.setitem(sys.modules, "Ankimon.pyobj.ankimon_sync", ankimon_sync)
    active = _make_save(tmp_path / "ankimon.db", pokemon=3, badges=1, history=2)
    folder = tmp_path / "collection.media"
    folder.mkdir()
    profile, prompts = {}, []
    monkeypatch.setattr(st, "_active_db_path", lambda: active)
    monkeypatch.setattr(st, "_media_dir", lambda: folder)
    monkeypatch.setattr(st.mw.pm, "profile", profile, raising=False)
    monkeypatch.setattr(st.mw.pm, "save", lambda: None, raising=False)
    monkeypatch.setattr(st, "_notify_affected_user", lambda logger: None)
    monkeypatch.setattr(st, "askUser", lambda prompt, **kw: prompts.append(prompt) or False)
    monkeypatch.setattr(services, "col", SimpleNamespace())
    return SimpleNamespace(active=active, folder=folder, profile=profile,
                           prompts=prompts, logger=_Logger())


def test_unlocking_a_different_candidate_reoffers_it_without_a_folder_change(migration):
    m = migration
    _make_save(m.folder / "_old_ankimon.db", pokemon=5, badges=2, history=3)
    newer = _make_save(m.folder / "_new_ankimon.db", pokemon=42, badges=8, history=99)
    lock = sqlite3.connect(newer)
    try:
        lock.execute("BEGIN EXCLUSIVE")
        first = st._migration_scan(m.folder, m.active)
        fingerprint = first["fingerprint"]
        assert first["media_stats"]["pokemon"] == 5
        assert first["unreadable"] == [newer]
        st._apply_migration_result(first, m.logger)
        assert len(m.prompts) == 1
        assert not st._migration_done()
    finally:
        lock.rollback()
        lock.close()

    second = st._migration_scan(m.folder, m.active)
    assert second["fingerprint"] == fingerprint
    assert second["media_stats"]["pokemon"] == 42
    assert not second["unreadable"]
    st._apply_migration_result(second, m.logger)

    assert len(m.prompts) == 2
    assert st._migration_done()


def test_changed_candidate_content_reoffers_even_with_identical_stats_and_metadata(migration):
    m = migration
    candidate = _make_save(m.folder / "_old_ankimon.db", pokemon=5, badges=2, history=3)
    (m.folder / "_locked_ankimon.db").write_bytes(b"not a readable SQLite database")
    first = st._migration_scan(m.folder, m.active)
    fingerprint, stats = first["fingerprint"], first["media_stats"]
    st._apply_migration_result(first, m.logger)
    st._apply_migration_result(st._migration_scan(m.folder, m.active), m.logger)
    assert len(m.prompts) == 1

    before = candidate.stat()
    connection = sqlite3.connect(candidate)
    try:
        with connection:
            connection.execute("UPDATE captured_pokemon SET individual_id = 'peer-' || individual_id")
    finally:
        connection.close()
    os.utime(candidate, ns=(before.st_atime_ns, before.st_mtime_ns))
    changed = st._migration_scan(m.folder, m.active)
    assert changed["fingerprint"] == fingerprint
    assert changed["media_stats"] == stats
    st._apply_migration_result(changed, m.logger)

    assert len(m.prompts) == 2
    assert not st._migration_done()


def test_legacy_folder_only_answer_reoffers_once(migration):
    m = migration
    _make_save(m.folder / "_old_ankimon.db", pokemon=5, badges=2, history=3)
    (m.folder / "_locked_ankimon.db").write_bytes(b"not a readable SQLite database")
    first = st._migration_scan(m.folder, m.active)
    m.profile[st._MIGRATION_ANSWERED_FLAG] = first["fingerprint"]
    st._apply_migration_result(first, m.logger)
    st._apply_migration_result(st._migration_scan(m.folder, m.active), m.logger)

    assert len(m.prompts) == 1
    assert not st._migration_done()
