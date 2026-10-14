"""Real SQLite invariants for applying a level-up dialog's move choice."""

import importlib
import json
import sqlite3
import sys
import types
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from threading import Event

import pytest
from conftest import isolated_modules


@pytest.fixture
def attacks_db(tmp_path, monkeypatch):
    """Use the production manager without importing Anki or a real user save."""
    with isolated_modules("Ankimon", "aqt"):
        src = Path(__file__).resolve().parents[1] / "src"
        for name in ("Ankimon", "Ankimon.pyobj"):
            package = types.ModuleType(name)
            package.__path__ = [str(src / name.replace(".", "/"))]
            package.__package__ = name
            sys.modules[name] = package
        sys.modules["aqt"] = None
        monkeypatch.setenv("ANKIMON_USER_PATH", str(tmp_path))
        module = importlib.import_module("Ankimon.pyobj.database_manager")
        db = module.AnkimonDB(db_path=tmp_path / "attacks.db")
        before = {
            "individual_id": "recipient",
            "id": 1,
            "name": "bulbasaur",
            "nickname": "Keep my name",
            "level": 15,
            "xp": 10,
            "friendship": 71,
            "attacks": ["tackle", "growl", "leechseed", "vinewhip"],
            "stats": {"xp": 10, "hp": 50},
            "current_hp": 30,
            "everstone": False,
            "evolution_rejected": False,
        }
        with db._get_connection().cursor() as cursor:
            cursor.execute(
                "INSERT INTO captured_pokemon (individual_id, is_main, data) "
                "VALUES (?, 1, ?)",
                (before["individual_id"], db._obfuscate(before)),
            )
        db._get_connection().commit()
        try:
            yield db, before
        finally:
            db.close()


CHOSEN = ["tackle", "growl", "poisonpowder", "sleeppowder"]


def _on_disk(db):
    """Check durable bytes independently from the tested connection's view."""
    conn = sqlite3.connect(db.db_path)
    try:
        return conn.execute(
            "SELECT individual_id, is_main, data FROM captured_pokemon ORDER BY 1"
        ).fetchall()
    finally:
        conn.close()


def _replace(db, record):
    """Write a newer record without history/cache side effects."""
    conn = db._get_connection()
    with conn.cursor() as cursor:
        cursor.execute(
            "UPDATE captured_pokemon SET data = ? WHERE individual_id = ?",
            (db._obfuscate(record), "recipient"),
        )
    conn.commit()


def test_only_attacks_change_while_every_newer_field_is_preserved(attacks_db):
    db, expected = attacks_db
    newer = deepcopy(expected)
    newer.update(
        level=17,
        xp=99,
        friendship=75,
        nickname="Mobile nickname",
        current_hp=12,
        evolution_rejected=True,
        held_item="soothe-bell",
        stats={"xp": 99, "hp": 57},
        custom_future_field={"preserve": [1, 2, 3]},
    )
    _replace(db, newer)

    result = db.update_pokemon_attacks("recipient", expected, CHOSEN)

    wanted = dict(newer, attacks=CHOSEN)
    assert result == wanted
    assert db.get_pokemon("recipient") == wanted
    assert json.loads(_on_disk(db)[0][2]) == wanted
    assert _on_disk(db)[0][1] == 1
    assert not db._get_connection().in_transaction
    assert expected["attacks"] != CHOSEN


@pytest.mark.parametrize(
    "change", ["deleted", "species_id", "species_name", "identity", "moves"]
)
def test_conflicts_cannot_change_a_newer_or_missing_recipient(attacks_db, change):
    db, expected = attacks_db
    if change == "deleted":
        db.execute(
            "DELETE FROM captured_pokemon WHERE individual_id = 'recipient'"
        ).close()
        db._get_connection().commit()
    else:
        newer = deepcopy(expected)
        changes = {
            "species_id": {"id": 2},
            "species_name": {"name": "ivysaur"},
            "identity": {"individual_id": "someone-else"},
            "moves": {"attacks": ["razorleaf", "growl", "leechseed", "vinewhip"]},
        }
        newer.update(changes[change])
        _replace(db, newer)
    original = _on_disk(db)

    assert db.update_pokemon_attacks("recipient", expected, CHOSEN) is None

    assert _on_disk(db) == original
    assert not db._get_connection().in_transaction


@pytest.mark.parametrize(
    "data", ["[]", "null", '"not-a-record"', '{"individual_id":"recipient"}']
)
def test_unreadable_record_is_left_untouched(attacks_db, data):
    db, expected = attacks_db
    db.execute(
        "UPDATE captured_pokemon SET data = ? WHERE individual_id = 'recipient'",
        (data,),
    ).close()
    db._get_connection().commit()
    original = _on_disk(db)

    assert db.update_pokemon_attacks("recipient", expected, CHOSEN) is None

    assert _on_disk(db) == original
    assert not db._get_connection().in_transaction


@pytest.mark.parametrize("attacks", [None, ["tackle", 2], "not-json", '{"tackle":1}'])
def test_invalid_stored_attacks_cannot_be_reconstructed(attacks_db, attacks):
    db, expected = attacks_db
    expected["attacks"] = attacks
    _replace(db, expected)
    original = _on_disk(db)

    assert db.update_pokemon_attacks("recipient", expected, CHOSEN) is None

    assert _on_disk(db) == original


def test_json_encoded_attacks_are_compared_before_local_decoding(attacks_db):
    """Older stored rows may contain JSON text instead of a list of move IDs."""
    db, expected = attacks_db
    expected["attacks"] = json.dumps(expected["attacks"])
    _replace(db, expected)

    result = db.update_pokemon_attacks("recipient", expected, CHOSEN)

    assert result == dict(expected, attacks=CHOSEN)
    assert db.get_pokemon("recipient")["attacks"] == CHOSEN


def test_raw_attack_representation_change_is_a_conflict(attacks_db):
    db, expected = attacks_db
    newer = dict(expected, attacks=json.dumps(expected["attacks"]))
    _replace(db, newer)
    original = _on_disk(db)

    assert db.update_pokemon_attacks("recipient", expected, CHOSEN) is None

    assert _on_disk(db) == original


def test_species_name_comparison_accepts_canonical_equivalents(attacks_db):
    db, expected = attacks_db
    newer = dict(expected, name="Bulba_saur", nickname="New name")
    _replace(db, newer)

    assert db.update_pokemon_attacks("recipient", expected, CHOSEN) == dict(
        newer, attacks=CHOSEN
    )


def test_a_replaced_save_generation_rejects_a_stale_dialog(attacks_db):
    db, expected = attacks_db
    identity = db.identity_token()
    original = _on_disk(db)
    assert db.close()
    assert db.identity_token() != identity

    assert (
        db.update_pokemon_attacks(
            "recipient", expected, CHOSEN, expected_db_identity=identity
        )
        is None
    )

    assert _on_disk(db) == original
    assert not db._get_connection().in_transaction


def test_commit_opt_out_keeps_a_new_patch_transaction_rollbackable(attacks_db):
    db, expected = attacks_db
    conn = db._get_connection()
    original = _on_disk(db)
    conn._disable_commit = True
    try:
        assert db.update_pokemon_attacks("recipient", expected, CHOSEN) == dict(
            expected, attacks=CHOSEN
        )
        assert conn.in_transaction
        assert _on_disk(db) == original
        conn.rollback()
        assert _on_disk(db) == original
    finally:
        conn._disable_commit = False
        conn.rollback()


@pytest.mark.parametrize("bulk", [False, True])
def test_patch_joins_outer_transaction_without_committing_it(attacks_db, bulk):
    db, expected = attacks_db
    conn = db._get_connection()
    original = _on_disk(db)
    conn.execute("BEGIN").close()
    conn.execute("INSERT INTO metadata VALUES ('caller-pending', 'keep')").close()
    conn._disable_commit = bulk
    try:
        assert db.update_pokemon_attacks("recipient", expected, CHOSEN) == dict(
            expected, attacks=CHOSEN
        )
        assert conn.in_transaction
        assert _on_disk(db) == original
        assert db.get_pokemon("recipient")["attacks"] == CHOSEN
        conn.rollback()
        assert _on_disk(db) == original
        assert (
            conn.execute(
                "SELECT 1 FROM metadata WHERE key = 'caller-pending'"
            ).fetchone()
            is None
        )
    finally:
        conn._disable_commit = False
        conn.rollback()


def test_failed_nested_patch_preserves_unrelated_pending_writes(attacks_db):
    db, expected = attacks_db
    conn = db._get_connection()
    conn.execute(
        "CREATE TEMP TRIGGER fail_attacks BEFORE UPDATE OF data ON captured_pokemon "
        "BEGIN SELECT RAISE(ABORT, 'injected attacks failure'); END"
    ).close()
    conn.execute("BEGIN").close()
    conn.execute("INSERT INTO metadata VALUES ('caller-pending', 'keep')").close()
    original = _on_disk(db)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="injected attacks failure"):
            db.update_pokemon_attacks("recipient", expected, CHOSEN)
        assert conn.in_transaction
        assert (
            conn.execute(
                "SELECT value FROM metadata WHERE key = 'caller-pending'"
            ).fetchone()[0]
            == "keep"
        )
        assert _on_disk(db) == original
        conn.commit()
        assert db.get_pokemon("recipient") == expected
    finally:
        conn.rollback()


def test_failed_commit_rolls_back_so_a_later_save_cannot_commit_the_choice(attacks_db):
    db, expected = attacks_db
    conn = db._get_connection()
    raw = conn._conn
    original = _on_disk(db)

    class FailedCommit:
        def commit(self):
            raise sqlite3.OperationalError("injected attacks commit failure")

        def __getattr__(self, name):
            return getattr(raw, name)

    conn._conn = FailedCommit()
    try:
        with pytest.raises(
            sqlite3.OperationalError, match="injected attacks commit failure"
        ):
            db.update_pokemon_attacks("recipient", expected, CHOSEN)
    finally:
        conn._conn = raw

    assert not conn.in_transaction
    assert _on_disk(db) == original
    db.execute("INSERT INTO metadata VALUES ('later-save', 'done')").close()
    conn.commit()
    assert _on_disk(db) == original


@pytest.mark.parametrize("outer", [False, True])
def test_failed_savepoint_setup_releases_only_its_own_transaction(attacks_db, outer):
    db, expected = attacks_db
    conn = db._get_connection()
    raw = conn._conn
    if outer:
        conn.execute("BEGIN").close()
        conn.execute("INSERT INTO metadata VALUES ('caller-pending', 'keep')").close()

    class FailedSavepoint:
        def execute(self, query, parameters=()):
            if query.startswith("SAVEPOINT"):
                raise sqlite3.OperationalError("injected savepoint failure")
            return raw.execute(query, parameters)

        def __getattr__(self, name):
            return getattr(raw, name)

    conn._conn = FailedSavepoint()
    try:
        with pytest.raises(
            sqlite3.OperationalError, match="injected savepoint failure"
        ):
            db.update_pokemon_attacks("recipient", expected, CHOSEN)
    finally:
        conn._conn = raw

    try:
        assert conn.in_transaction is outer
        assert db.get_pokemon("recipient") == expected
        if outer:
            assert (
                conn.execute(
                    "SELECT value FROM metadata WHERE key = 'caller-pending'"
                ).fetchone()[0]
                == "keep"
            )
    finally:
        conn.rollback()


def test_an_ignored_update_cannot_report_a_successful_move_choice(attacks_db):
    db, expected = attacks_db
    conn = db._get_connection()
    original = _on_disk(db)
    conn.execute(
        "CREATE TEMP TRIGGER ignore_attacks BEFORE UPDATE OF data ON captured_pokemon "
        "BEGIN SELECT RAISE(IGNORE); END"
    ).close()

    with pytest.raises(RuntimeError):
        db.update_pokemon_attacks("recipient", expected, CHOSEN)

    assert not conn.in_transaction
    assert _on_disk(db) == original


def test_an_already_running_mobile_writer_finishes_before_the_patch_reads(attacks_db):
    """The SELECT must run after waiting for the live writer, preserving its XP."""
    db, expected = attacks_db
    ready, patch_started, selected, release = Event(), Event(), Event(), Event()
    newer = dict(expected, level=17, xp=99, friendship=75)

    def mobile_reward():
        conn = sqlite3.connect(db.db_path, timeout=5)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE captured_pokemon SET data = ? WHERE individual_id = ?",
                (json.dumps(newer), "recipient"),
            )
            ready.set()
            assert release.wait(5)
            conn.commit()
        finally:
            conn.close()

    def patch_moves():
        assert ready.wait(5)
        conn = db._get_connection()

        def trace(statement):
            if (
                statement.lstrip().upper().startswith("SELECT")
                and "captured_pokemon" in statement.lower()
            ):
                selected.set()

        conn._conn.set_trace_callback(trace)
        try:
            patch_started.set()
            return db.update_pokemon_attacks("recipient", expected, CHOSEN)
        finally:
            conn._conn.set_trace_callback(None)

    with ThreadPoolExecutor(max_workers=2) as pool:
        mobile = pool.submit(mobile_reward)
        patch = pool.submit(patch_moves)
        assert patch_started.wait(5)
        try:
            # A nonlocking SELECT+UPDATE can read the pre-mobile row here and
            # overwrite its reward as soon as the write lock is released.
            assert not selected.wait(0.2), (
                "read the recipient before waiting for the mobile writer"
            )
        finally:
            release.set()
        mobile.result(timeout=10)
        result = patch.result(timeout=10)

    wanted = dict(newer, attacks=CHOSEN)
    assert result == wanted
    assert json.loads(_on_disk(db)[0][2]) == wanted
