"""A remembered migration answer applies only to the save actually shown."""

import os
import hashlib
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


@pytest.mark.parametrize("diverged_name", ["ankimon.db", "_diverged_ankimon.db"])
def test_eligible_rescue_is_offered_before_a_higher_ranked_diverged_save(
    migration, diverged_name,
):
    m = migration
    m.active.unlink()
    _make_save(m.active, pokemon=10, badges=10, history=10)
    diverged = _make_save(m.folder / diverged_name, pokemon=100)
    _make_save(m.folder / "_eligible_ankimon.db", pokemon=20, badges=20, history=20)
    eligible = _make_save(m.folder / "_best_ankimon.db", pokemon=30, badges=20, history=20)
    original = diverged.read_bytes()

    result = st._migration_scan(m.folder, m.active)
    chosen = result["media_path"]
    st._apply_migration_result(result, m.logger)

    assert len(m.prompts) == 1
    assert chosen == eligible
    assert "Pokemon: 30" in m.prompts[0]
    assert st._migration_done()
    st.run_media_migration(None, m.logger)
    assert len(m.prompts) == 1
    assert diverged.read_bytes() == original
    if diverged_name == "ankimon.db":
        protected = list(m.folder.glob("_ankimon_save_*.db"))
        assert len(protected) == 1
        assert protected[0].read_bytes() == original


@pytest.mark.parametrize("target_db", ["ankimon.db", "ankimonDEV.db"])
@pytest.mark.parametrize("damage", ["corrupt", "different_save"])
def test_preservation_verifies_existing_copy_and_keeps_both_files(
    migration, target_db, damage,
):
    source = _make_save(migration.folder / target_db, pokemon=4)
    original = source.read_bytes()
    digest = hashlib.sha256(original).hexdigest()[:32]
    prefix = "_ankimon_save_dev_" if target_db == "ankimonDEV.db" else "_ankimon_save_"
    damaged = migration.folder / f"{prefix}{digest}.db"
    if damage == "corrupt":
        damaged.write_bytes(b"damaged protected save")
    else:
        _make_save(damaged, pokemon=1)
    damaged_bytes = damaged.read_bytes()

    active = migration.active
    if target_db == "ankimonDEV.db":
        active = _make_save(active.with_name(target_db), pokemon=3)
    result = st._migration_scan(migration.folder, active)
    st._discard_snapshot(result.get("snapshot_path"))

    protected = [p for p in migration.folder.glob(f"{prefix}*.db") if p != damaged]
    assert len(protected) == 1
    assert protected[0].read_bytes() == original
    assert source.read_bytes() == original
    assert damaged.read_bytes() == damaged_bytes

    written = []
    assert st._preserve(source, migration.folder, target_db, [], [], written) == protected[0]
    assert written == []
    assert len(list(migration.folder.glob(f"{prefix}*.db"))) == 2
