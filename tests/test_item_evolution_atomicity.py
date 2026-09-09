"""Real SQLite invariants for item evolution, including failed commits."""

from copy import deepcopy
import importlib
import json
import sqlite3
import sys

import pytest

from conftest import isolated_modules


@pytest.fixture
def evolution_db(tmp_path):
    """Keep every save and connection in a disposable, isolated database."""
    with isolated_modules(
        "aqt", extra=("Ankimon.resources", "Ankimon.pyobj.database_manager")
    ):
        sys.modules["aqt"] = None
        module = importlib.import_module("Ankimon.pyobj.database_manager")
        db = module.AnkimonDB(db_path=tmp_path / "evolution.db")
        before = {
            "individual_id": "kadabra",
            "id": 64,
            "name": "Kadabra",
            "nickname": "Keep me",
            "level": 30,
            "attacks": ["Tackle"],
        }
        # Seed without save_pokemon's automatic history repair: the transaction
        # must preserve the pre-evolution's history even in an older save.
        conn = db._get_connection()
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO captured_pokemon (individual_id, is_main, data) "
                "VALUES (?, 1, ?)",
                (before["individual_id"], db._obfuscate(before)),
            )
        conn.commit()
        after = deepcopy(before)
        after.update(id=65, name="Alakazam", attacks=["Tackle", "Kinesis"])
        try:
            yield db, before, after
        finally:
            db._get_connection().close()


def _save(db, before, after):
    """Exercise the production persistence entry point."""
    from Ankimon.functions.item_evolution import save_item_evolution

    return save_item_evolution(db, before, after, "linking-cord")


def _on_disk(db):
    """An independent connection sees committed state, never pending writes."""
    with sqlite3.connect(db.db_path) as conn:
        return {
            table: conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            for table in ("captured_pokemon", "items", "user_data")
        }


@pytest.mark.parametrize("quantity", [1, 3])
def test_evolution_charges_one_item_and_preserves_history(evolution_db, quantity):
    db, before, after = evolution_db
    db.save_item(2160, "linking-cord", quantity, category_id=10, cost=8000)

    assert _save(db, before, after) is True

    assert db.get_pokemon("kadabra") == after
    assert (
        db.execute(
            "SELECT is_main FROM captured_pokemon WHERE individual_id = 'kadabra'"
        ).fetchone()[0]
        == 1
    )
    item = db.get_item("linking-cord")
    if quantity == 1:
        assert item is None
    else:
        assert item["quantity"] == quantity - 1
        assert item["id"] == 2160
        assert item["cost"] == 8000
    for key in ("pokedex_caught", "pokedex_seen"):
        assert {64, 65} <= set(db.get_user_data(key, []))
    assert not db._get_connection().in_transaction
    with sqlite3.connect(db.db_path) as conn:
        assert (
            json.loads(
                conn.execute(
                    "SELECT data FROM captured_pokemon WHERE individual_id = 'kadabra'"
                ).fetchone()[0]
            )
            == after
        )


@pytest.mark.parametrize("quantity", [None, 0])
def test_missing_item_leaves_pokemon_and_history_unchanged(evolution_db, quantity):
    db, before, after = evolution_db
    if quantity is not None:
        db.save_item(2160, "linking-cord", quantity)
    original = _on_disk(db)

    assert _save(db, before, after) is False
    assert _on_disk(db) == original


@pytest.mark.parametrize("change", ["nickname", "species", "deleted"])
def test_stale_pokemon_cannot_overwrite_newer_state(evolution_db, change):
    db, before, after = evolution_db
    db.save_item(2160, "linking-cord", 2)
    if change == "deleted":
        db.execute("DELETE FROM captured_pokemon WHERE individual_id = 'kadabra'")
        db._get_connection().commit()
    else:
        newer = dict(
            before, **({"nickname": "New name"} if change == "nickname" else {"id": 65})
        )
        db.save_pokemon(newer)
    original = _on_disk(db)

    assert _save(db, before, after) is False
    assert _on_disk(db) == original


@pytest.mark.parametrize(
    "operation",
    [
        "UPDATE OF quantity ON items",
        "UPDATE ON captured_pokemon",
        "DELETE ON items",
        "INSERT ON user_data",
    ],
)
def test_failed_write_rolls_back_every_change(evolution_db, operation):
    db, before, after = evolution_db
    db.save_item(2160, "linking-cord", 1)
    original = _on_disk(db)
    db.execute(
        f"CREATE TEMP TRIGGER fail_evolution BEFORE {operation} "
        "BEGIN SELECT RAISE(ABORT, 'injected evolution failure'); END"
    )

    with pytest.raises(sqlite3.IntegrityError, match="injected evolution failure"):
        _save(db, before, after)

    assert not db._get_connection().in_transaction
    assert _on_disk(db) == original
    db.execute("DROP TRIGGER fail_evolution")
    db.save_item(1, "unrelated", 1)
    assert db.get_pokemon("kadabra") == before
    assert db.get_item("linking-cord")["quantity"] == 1


def test_ignored_pokemon_update_rolls_back_charge(evolution_db):
    db, before, after = evolution_db
    db.save_item(2160, "linking-cord", 1)
    original = _on_disk(db)
    db.execute(
        "CREATE TEMP TRIGGER ignore_evolution BEFORE UPDATE ON captured_pokemon "
        "BEGIN SELECT RAISE(IGNORE); END"
    )

    with pytest.raises(RuntimeError):
        _save(db, before, after)
    assert _on_disk(db) == original


def test_failed_commit_cannot_leak_into_later_save(evolution_db):
    db, before, after = evolution_db
    db.save_item(2160, "linking-cord", 2)
    original = _on_disk(db)
    conn = db._get_connection()
    raw = conn._conn

    class FailedCommit:
        """Delegate real SQL but fail the actual transaction commit."""

        def commit(self):
            raise sqlite3.OperationalError("injected commit failure")

        def __getattr__(self, name):
            return getattr(raw, name)

    conn._conn = FailedCommit()
    try:
        with pytest.raises(sqlite3.OperationalError, match="injected commit failure"):
            _save(db, before, after)
    finally:
        conn._conn = raw
    assert not conn.in_transaction
    assert _on_disk(db) == original
    db.save_item(1, "unrelated", 1)
    assert db.get_pokemon("kadabra") == before
    assert db.get_item("linking-cord")["quantity"] == 2


def test_repeated_confirmation_cannot_charge_twice(evolution_db):
    db, before, after = evolution_db
    db.save_item(2160, "linking-cord", 3)
    assert _save(db, before, after) is True
    original = _on_disk(db)
    assert _save(db, before, after) is False
    assert _on_disk(db) == original


@pytest.mark.parametrize("bulk", [False, True])
def test_cannot_commit_an_existing_transaction(evolution_db, bulk):
    db, before, after = evolution_db
    db.save_item(2160, "linking-cord", 1)
    original = _on_disk(db)
    conn = db._get_connection()
    conn._disable_commit = bulk
    conn.execute("BEGIN")
    conn.execute("UPDATE items SET quantity = 2 WHERE item_name = 'linking-cord'")
    try:
        with pytest.raises(RuntimeError):
            _save(db, before, after)
        assert conn.in_transaction
        assert _on_disk(db) == original
    finally:
        conn._disable_commit = False
        conn.rollback()
