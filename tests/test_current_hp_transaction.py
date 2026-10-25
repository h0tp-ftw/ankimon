"""HP-only saves must preserve concurrent rewards and caller transactions."""

import importlib
import sqlite3
import sys
import threading

import pytest

from conftest import isolated_modules


@pytest.fixture
def hp_db(tmp_path, monkeypatch):
    with isolated_modules(
        "aqt", extra=("Ankimon.resources", "Ankimon.pyobj.database_manager")
    ):
        sys.modules["aqt"] = None
        module = importlib.import_module("Ankimon.pyobj.database_manager")
        # Anki supplies this distinction through Qt; keep the test Qt-free.
        monkeypatch.setattr(
            module,
            "_is_main_thread",
            lambda: threading.current_thread() is threading.main_thread(),
        )
        db = module.AnkimonDB(db_path=tmp_path / "hp.db")
        db.save_main_pokemon(
            {
                "individual_id": "main",
                "id": 25,
                "name": "pikachu",
                "level": 50,
                "base_stats": {"hp": 35},
                "current_hp": 70,
                "xp": 0,
                "attacks": ["thunderbolt"],
                "custom_metadata": {"keep": [1, 2]},
            }
        )
        try:
            yield db
        finally:
            db.close()


def test_hp_write_preserves_reward_committed_during_save(hp_db, monkeypatch):
    """Force a reward writer to run after the HP reader takes its snapshot."""
    start = threading.Event()
    attempted = threading.Event()
    hp_done = threading.Event()
    errors = []
    original = hp_db._obfuscate

    def serialize_hp(data):
        if threading.current_thread() is threading.main_thread():
            start.set()
            assert attempted.wait(5), "reward writer did not attempt its update"
        return original(data)

    monkeypatch.setattr(hp_db, "_obfuscate", serialize_hp)

    def reward():
        try:
            assert start.wait(5), "HP writer did not read the Pokemon"
            raw = hp_db._get_connection()._conn
            raw.execute("PRAGMA busy_timeout=0")
            try:
                with hp_db.pokemon_reward_transaction("main") as pokemon:
                    pokemon["xp"] += 123
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower():
                    raise
                # A protected HP write finishes before this reward retries.
                attempted.set()
                assert hp_done.wait(5), "HP write did not release its lock"
                with hp_db.pokemon_reward_transaction("main") as pokemon:
                    pokemon["xp"] += 123
            finally:
                attempted.set()
        except BaseException as exc:
            errors.append(exc)
            attempted.set()

    worker = threading.Thread(target=reward)
    worker.start()
    try:
        assert hp_db.update_pokemon_current_hp("main", 3)
    finally:
        hp_done.set()
        worker.join(5)
    assert not worker.is_alive()
    assert not errors, errors
    saved = hp_db.get_main_pokemon()
    assert saved["current_hp"] == 3
    assert saved["xp"] == 123
    assert saved["attacks"] == ["thunderbolt"]
    assert saved["custom_metadata"] == {"keep": [1, 2]}


def test_hp_write_refuses_busy_save_without_leaking_timeout(hp_db):
    """A long mobile transaction must not hold up a reviewer repaint."""
    ready = threading.Event()
    finish = threading.Event()
    errors = []

    def reward():
        try:
            with hp_db.pokemon_reward_transaction("main") as pokemon:
                pokemon["xp"] = 123
                ready.set()
                assert finish.wait(5), "HP writer did not return promptly"
        except BaseException as exc:
            errors.append(exc)
            ready.set()

    worker = threading.Thread(target=reward)
    worker.start()
    raw = hp_db._get_connection()._conn
    raw.execute("PRAGMA busy_timeout=20")
    try:
        assert ready.wait(5)
        assert hp_db.update_pokemon_current_hp("main", 3) is False
        assert raw.execute("PRAGMA busy_timeout").fetchone()[0] == 20
        assert not raw.in_transaction
    finally:
        finish.set()
        worker.join(5)
    assert not worker.is_alive()
    assert not errors, errors
    assert hp_db.update_pokemon_current_hp("main", 3)
    assert hp_db.get_main_pokemon()["xp"] == 123


def test_hp_write_does_not_commit_or_roll_back_callers_work(hp_db):
    raw = hp_db._get_connection()._conn
    raw.execute("INSERT INTO metadata (key, value) VALUES ('pending', 'value')")
    assert hp_db.update_pokemon_current_hp("main", 3) is False
    assert raw.in_transaction
    assert raw.execute("SELECT value FROM metadata WHERE key='pending'").fetchone()
    raw.rollback()
    assert hp_db.get_main_pokemon()["current_hp"] == 70


def test_hp_write_refuses_bulk_scope(hp_db):
    conn = hp_db._get_connection()
    conn._disable_commit = True
    try:
        assert hp_db.update_pokemon_current_hp("main", 3) is False
        assert not conn.in_transaction
    finally:
        conn._disable_commit = False
    assert hp_db.get_main_pokemon()["current_hp"] == 70
