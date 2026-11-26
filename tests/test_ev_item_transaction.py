"""Real SQLite coverage for committing EV training and item payment together."""

from contextlib import closing
from copy import deepcopy
import importlib
import json
import sqlite3
import sys
import threading

import pytest

from conftest import isolated_modules


@pytest.fixture
def training_db(tmp_path):
    """Use the production manager with a disposable save, independent of Qt."""
    with isolated_modules(
        "aqt", extra=("Ankimon.resources", "Ankimon.pyobj.database_manager")
    ):
        sys.modules["aqt"] = None
        module = importlib.import_module("Ankimon.pyobj.database_manager")
        db = module.AnkimonDB(db_path=tmp_path / "training.db")
        before = {
            "individual_id": "main",
            "id": 150,
            "name": "mewtwo",
            "nickname": "Keep me",
            "level": 50,
            "ev": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
            "current_hp": 75,
        }
        bench = dict(before, individual_id="bench", id=151, name="mew")
        conn = db._get_connection()
        with conn.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO captured_pokemon (individual_id, is_main, data) "
                "VALUES (?, ?, ?)",
                [
                    ("main", 1, db._obfuscate(before)),
                    ("bench", 0, db._obfuscate(bench)),
                ],
            )
        conn.commit()
        after = deepcopy(before)
        after["ev"]["atk"] = 10
        try:
            yield db, before, after
        finally:
            db.close()


def _stock(db, quantity):
    db.save_item(
        64,
        "protein",
        quantity,
        extra_data={"source": "earned", "custom": [1, 2]},
        category_id=26,
        cost=10000,
        fling_power=30,
        fling_effect_id=1,
    )


def _on_disk(db):
    """Observe committed state through a separate SQLite connection."""
    with closing(sqlite3.connect(db.db_path)) as conn:
        return {
            table: conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            for table in (
                "captured_pokemon",
                "items",
                "user_data",
                "metadata",
                "team",
                "pokemon_history",
            )
        }


def _assert_no_leaked_training(db, before, stock):
    """A later, unrelated successful write must not commit a failed attempt."""
    db.save_item(1, "unrelated", 1)
    assert db.get_main_pokemon() == before
    assert db.get_item("protein") == stock
    with closing(sqlite3.connect(db.db_path)) as conn:
        saved = conn.execute(
            "SELECT data FROM captured_pokemon WHERE individual_id = 'main'"
        ).fetchone()[0]
        assert json.loads(saved) == before
        assert (
            conn.execute(
                "SELECT quantity FROM items WHERE item_name = 'protein'"
            ).fetchone()[0]
            == stock["quantity"]
        )


@pytest.mark.parametrize("quantity", [1, 3])
def test_training_and_one_item_commit_together(training_db, quantity):
    db, _before, after = training_db
    _stock(db, quantity)
    stock = db.get_item("protein")
    original = _on_disk(db)
    statements = []
    conn = db._get_connection()
    conn.set_trace_callback(statements.append)
    try:
        assert db.consume_item_and_save_main_pokemon("protein", after) is True
    finally:
        conn.set_trace_callback(None)

    assert db.get_main_pokemon() == after
    assert not conn.in_transaction
    assert statements.count("COMMIT") == 1
    saved = _on_disk(db)
    assert saved["captured_pokemon"][0] == original["captured_pokemon"][0]
    assert saved["captured_pokemon"][1][0:2] == ("main", 1)
    assert json.loads(saved["captured_pokemon"][1][2]) == after
    for table in ("user_data", "metadata", "team", "pokemon_history"):
        assert saved[table] == original[table]
    if quantity == 1:
        assert db.get_item("protein") is None
        assert saved["items"] == []
    else:
        assert db.get_item("protein") == dict(stock, quantity=quantity - 1)
        assert saved["items"][0][2] == quantity - 1


@pytest.mark.parametrize(
    "payload", [None, {}, {"individual_id": ""}, {"individual_id": 1}]
)
def test_invalid_pokemon_is_refused_without_charging(training_db, payload):
    db, _before, _after = training_db
    _stock(db, 1)
    original = _on_disk(db)

    assert db.consume_item_and_save_main_pokemon("protein", payload) is False
    assert _on_disk(db) == original
    assert not db._get_connection().in_transaction


@pytest.mark.parametrize("item_name", [None, ""])
def test_invalid_item_name_is_refused_without_training(training_db, item_name):
    db, _before, after = training_db
    _stock(db, 1)
    original = _on_disk(db)

    assert db.consume_item_and_save_main_pokemon(item_name, after) is False
    assert _on_disk(db) == original
    assert not db._get_connection().in_transaction


def test_serialization_failure_happens_before_payment(training_db):
    db, _before, after = training_db
    _stock(db, 1)
    original = _on_disk(db)
    after["not_serializable"] = object()

    with pytest.raises(TypeError):
        db.consume_item_and_save_main_pokemon("protein", after)

    assert _on_disk(db) == original
    assert not db._get_connection().in_transaction


@pytest.mark.parametrize(
    "main_change", ["deleted", "different_main", "no_main", "unowned"]
)
def test_training_cannot_recreate_or_reselect_a_stale_main(training_db, main_change):
    db, _before, after = training_db
    _stock(db, 1)
    conn = db._get_connection()
    if main_change == "deleted":
        db.execute("DELETE FROM captured_pokemon WHERE individual_id = 'main'")
    elif main_change == "different_main":
        db.execute("UPDATE captured_pokemon SET is_main = (individual_id = 'bench')")
    elif main_change == "no_main":
        db.execute("UPDATE captured_pokemon SET is_main = 0")
    else:
        after["individual_id"] = "unowned"
    conn.commit()
    original = _on_disk(db)

    assert db.consume_item_and_save_main_pokemon("protein", after) is False

    assert _on_disk(db) == original
    assert not conn.in_transaction


@pytest.mark.parametrize("quantity", [None, 0, -1])
def test_missing_or_short_stock_leaves_main_unchanged(training_db, quantity):
    db, _before, after = training_db
    if quantity is not None:
        _stock(db, quantity)
    original = _on_disk(db)

    assert db.consume_item_and_save_main_pokemon("protein", after) is False

    assert _on_disk(db) == original
    assert not db._get_connection().in_transaction


@pytest.mark.parametrize("scope", ["pending_transaction", "disabled_commit"])
def test_training_does_not_commit_or_rollback_a_callers_scope(training_db, scope):
    db, _before, after = training_db
    _stock(db, 2)
    original = _on_disk(db)
    conn = db._get_connection()
    if scope == "pending_transaction":
        db.execute("UPDATE items SET quantity = 7 WHERE item_name = 'protein'")
    else:
        conn._disable_commit = True
    try:
        assert db.consume_item_and_save_main_pokemon("protein", after) is False
        assert _on_disk(db) == original
        if scope == "pending_transaction":
            assert conn.in_transaction
            assert db.get_item("protein")["quantity"] == 7
        else:
            assert not conn.in_transaction
    finally:
        conn._disable_commit = False
        conn.rollback()
    assert _on_disk(db) == original


@pytest.mark.parametrize(
    "operation",
    ["UPDATE OF quantity ON items", "DELETE ON items", "UPDATE ON captured_pokemon"],
)
def test_failed_sql_rolls_back_training_payment_and_metadata(training_db, operation):
    db, before, after = training_db
    _stock(db, 1)
    stock = db.get_item("protein")
    original = _on_disk(db)
    db.execute(
        f"CREATE TEMP TRIGGER fail_training BEFORE {operation} "
        "BEGIN SELECT RAISE(ABORT, 'injected training failure'); END"
    )

    with pytest.raises(sqlite3.IntegrityError, match="injected training failure"):
        db.consume_item_and_save_main_pokemon("protein", after)

    assert not db._get_connection().in_transaction
    assert _on_disk(db) == original
    db.execute("DROP TRIGGER fail_training")
    _assert_no_leaked_training(db, before, stock)


def test_ignored_main_update_does_not_charge_the_item(training_db):
    db, before, after = training_db
    _stock(db, 1)
    stock = db.get_item("protein")
    original = _on_disk(db)
    db.execute(
        "CREATE TEMP TRIGGER ignore_training BEFORE UPDATE ON captured_pokemon "
        "BEGIN SELECT RAISE(IGNORE); END"
    )

    assert db.consume_item_and_save_main_pokemon("protein", after) is False
    assert not db._get_connection().in_transaction
    assert _on_disk(db) == original
    db.execute("DROP TRIGGER ignore_training")
    _assert_no_leaked_training(db, before, stock)


@pytest.mark.parametrize("quantity", [1, 3])
def test_real_read_lock_failed_commit_cannot_leak_into_later_save(
    training_db, quantity
):
    db, before, after = training_db
    _stock(db, quantity)
    stock = db.get_item("protein")
    original = _on_disk(db)
    conn = db._get_connection()
    conn.execute("PRAGMA busy_timeout = 25").close()

    # In the normal single-file journal mode a second connection's read lock
    # lets our writes start, then prevents their commit. This exercises actual
    # SQLite failure after both the last-unit DELETE and Pokemon UPDATE ran.
    with closing(sqlite3.connect(db.db_path)) as reader:
        assert reader.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM items").fetchall()
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            db.consume_item_and_save_main_pokemon("protein", after)
        assert not conn.in_transaction
        reader.rollback()

    assert _on_disk(db) == original
    _assert_no_leaked_training(db, before, stock)


def test_real_writer_lock_refuses_before_any_training_or_payment(training_db):
    db, before, after = training_db
    _stock(db, 1)
    stock = db.get_item("protein")
    original = _on_disk(db)
    conn = db._get_connection()
    conn.execute("PRAGMA busy_timeout = 25").close()

    with closing(sqlite3.connect(db.db_path)) as writer:
        writer.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            db.consume_item_and_save_main_pokemon("protein", after)
        assert not conn.in_transaction
        writer.rollback()

    assert _on_disk(db) == original
    _assert_no_leaked_training(db, before, stock)


def test_matching_snapshot_allows_preserving_live_battle_hp(training_db):
    db, before, after = training_db
    _stock(db, 1)
    # Combat HP need not have reached disk when the bag is opened. The
    # concurrency guard compares the persisted snapshot, not the candidate.
    after["hp"] = after["current_hp"] = 20

    assert (
        db.consume_item_and_save_main_pokemon(
            "protein", after, expected_pokemon_data=before
        )
        is True
    )

    expected_saved = dict(after)
    expected_saved.pop("hp", None)
    assert db.get_main_pokemon() == expected_saved
    assert db.get_item("protein") is None
    assert not db._get_connection().in_transaction


def test_changed_main_snapshot_preserves_rewards_and_all_item_metadata(training_db):
    db, before, after = training_db
    _stock(db, 2)
    stock = db.get_item("protein")
    rewarded = deepcopy(before)
    rewarded["ev"]["atk"] = 3
    rewarded.update(xp=1, friendship=9, pokemon_defeated=1)
    db.save_main_pokemon(rewarded)
    original = _on_disk(db)

    assert (
        db.consume_item_and_save_main_pokemon(
            "protein", after, expected_pokemon_data=before
        )
        is False
    )

    assert _on_disk(db) == original
    assert not db._get_connection().in_transaction
    _assert_no_leaked_training(db, rewarded, stock)

    # A refreshed attempt uses the newly committed rewards as its baseline.
    refreshed = deepcopy(rewarded)
    refreshed["ev"]["atk"] += 10
    assert (
        db.consume_item_and_save_main_pokemon(
            "protein", refreshed, expected_pokemon_data=rewarded
        )
        is True
    )
    assert db.get_main_pokemon() == refreshed
    assert db.get_item("protein") == dict(stock, quantity=1)


def test_reward_committed_during_writer_lock_wait_is_not_overwritten(training_db):
    db, before, after = training_db
    _stock(db, 1)
    stock = db.get_item("protein")
    rewarded = deepcopy(before)
    rewarded["ev"]["atk"] = 3
    rewarded.update(xp=1, friendship=9, pokemon_defeated=1)
    writer_locked = threading.Event()
    item_started = threading.Event()
    errors = []

    def commit_rewards():
        try:
            with closing(sqlite3.connect(db.db_path)) as writer:
                writer.execute("BEGIN IMMEDIATE")
                writer.execute(
                    "UPDATE captured_pokemon SET data = ? WHERE individual_id = ?",
                    (json.dumps(rewarded), "main"),
                )
                writer_locked.set()
                assert item_started.wait(5), "item transaction never started"
                writer.commit()
        except BaseException as exc:
            errors.append(exc)

    # SQLite's trace callback runs before BEGIN tries to acquire the writer
    # lock, so the worker can commit precisely after the stale payload exists.
    conn = db._get_connection()
    conn.set_trace_callback(
        lambda statement: item_started.set() if statement == "BEGIN IMMEDIATE" else None
    )
    thread = threading.Thread(target=commit_rewards)
    thread.start()
    try:
        assert writer_locked.wait(5), "reward worker never acquired its lock"
        assert (
            db.consume_item_and_save_main_pokemon(
                "protein", after, expected_pokemon_data=before
            )
            is False
        )
    finally:
        item_started.set()
        thread.join(5)
        conn.set_trace_callback(None)

    assert not thread.is_alive()
    assert errors == []
    assert db.get_main_pokemon() == rewarded
    assert db.get_item("protein") == stock
    assert not conn.in_transaction
    _assert_no_leaked_training(db, rewarded, stock)
