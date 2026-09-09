import os
import sys
import json
import csv
import sqlite3
import pytest
import importlib.util
import contextlib
from pathlib import Path
from unittest.mock import MagicMock, patch
import types

# 1. SETUP CLEAN MOCKS BEFORE ANY IMPORTS
_src = Path(__file__).parent.parent / "src"

def setup_mocks():
    # Mock aqt/anki namespaces
    for name in [
        "aqt", "aqt.qt", "aqt.utils", "aqt.gui_hooks", "aqt.operations", 
        "aqt.reviewer", "aqt.webview", "aqt.main", "aqt.operations.QueryOp",
        "anki", "anki.hooks", "anki.collection", "anki.models", "anki.notes", "anki.template", "anki.buildinfo"
    ]:
        sys.modules[name] = MagicMock()
    
    # Define a robust mock for resources
    class MockResources:
        # These are used by database_manager
        user_path = Path("/tmp")
        csv_file_items_cost = Path("/tmp/items.csv")
        items_path = Path("/tmp/items.json")
        badges_path = Path("/tmp/badges.json")
        mypokemon_path = Path("/tmp/mypokemon.json")
        mainpokemon_path = Path("/tmp/mainpokemon.json")
        def __getattr__(self, name): return Path("/tmp") / name

    # Correct package structure for sys.modules
    sys.modules["Ankimon"] = types.ModuleType("Ankimon")
    sys.modules["Ankimon.resources"] = MaskedResources = MockResources()
    sys.modules["Ankimon.singletons"] = MagicMock()
    sys.modules["Ankimon.utils"] = MagicMock()
    sys.modules["Ankimon.pyobj"] = MagicMock()

setup_mocks()

# 2. DYNAMICALLY LOAD DATABASE_MANAGER
_spec = importlib.util.spec_from_file_location(
    "Ankimon.pyobj.database_manager",
    _src / "Ankimon" / "pyobj" / "database_manager.py",
)
_db_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _db_mod
_spec.loader.exec_module(_db_mod)

from Ankimon.pyobj.database_manager import AnkimonDB

class MockLogger:
    def log(self, level, msg): pass
    def log_and_showinfo(self, level, msg): pass
    def _log(self, level, msg): pass

@pytest.fixture
def temp_env(tmp_path):
    """Setup a temporary environment for the DB and its CSV files."""
    # Patch the resources in the database_manager namespace specifically
    with patch.object(_db_mod, "user_path", tmp_path), \
         patch.object(_db_mod, "csv_file_items_cost", str(tmp_path / "items.csv")), \
         patch.object(_db_mod, "items_path", tmp_path / "items_mig.json"), \
         patch.object(_db_mod, "badges_path", tmp_path / "badges_mig.json"):
        
        # Create mock items.csv
        csv_path = tmp_path / "items.csv"
        headers = ["id", "identifier", "category_id", "cost", "fling_power", "fling_effect_id"]
        rows = [
            ["1", "master-ball", "34", "0", "", ""],
            ["30", "fresh-water", "1", "200", "", ""],
            ["20225", "dragonbreath", "37", "0", "", ""],
        ]
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(rows)
            
        db = AnkimonDB(MockLogger())
        yield db, tmp_path


def _quantity_on_disk(db, item_name):
    """Read a quantity through a connection of our own.

    ``db.get_item`` goes through the connection under test, which cannot tell a
    committed row from one still sitting in an open transaction. A separate
    handle only ever sees what is durable.
    """
    raw = sqlite3.connect(str(db.db_path), timeout=1.0)
    try:
        row = raw.execute(
            "SELECT quantity FROM items WHERE item_name = ?", (item_name,)
        ).fetchone()
    finally:
        raw.close()
    return None if row is None else row[0]


class _CorruptOnCommit:
    """A connection whose COMMIT fails the way a corrupt database file does.

    Patching ``ConnectionWrapper.commit`` -- what the busy-timeout tests below
    do -- cannot reach the wrapper's own corruption branch, because that branch
    lives inside the method being replaced. Patching the raw handle is not an
    option either: ``sqlite3.Connection.commit`` is a read-only C attribute.
    So wrap the real connection and let everything except the commit through;
    ``ConnectionWrapper._conn`` is a plain Python attribute, so it can be swapped.
    """

    def __init__(self, conn):
        self._real = conn
        self.commit_attempts = 0

    def commit(self):
        self.commit_attempts += 1
        raise sqlite3.DatabaseError("database disk image is malformed")

    def __getattr__(self, name):
        return getattr(self._real, name)


@contextlib.contextmanager
def _corrupt_commits_on(conn):
    """Make ``conn``'s raw handle fail COMMIT, and put the real one back after."""
    real = conn._conn
    conn._conn = _CorruptOnCommit(real)
    try:
        yield conn._conn
    finally:
        conn._conn = real


def test_database_initialization(temp_env):
    db, _ = temp_env
    conn = db._get_connection()
    cursor = conn.cursor()
    
    # Table names updated based on database_manager.py _setup_database
    tables = ["metadata", "items", "badges", "captured_pokemon", "team", "pokemon_history"]
    for table in tables:
        cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'")
        assert cursor.fetchone() is not None, f"Table {table} should exist"


def test_base_stats_normalization_marker_is_internal_metadata(temp_env):
    """The startup marker must not make a virgin user-config table look populated."""
    db, _ = temp_env
    conn = db._get_connection()

    marker = conn.execute(
        "SELECT value FROM metadata WHERE key = 'base_stats_normalized'"
    ).fetchone()

    assert marker[0] == "true"
    assert db.has_config() is False
    assert db.get_all_config() == {}

def test_item_save_and_smart_sync(temp_env):
    db, tmp_path = temp_env
    # 1. First add (uses CSV to discover metadata)
    db.add_item("fresh-water", 5)
    item = db.get_item("fresh-water")
    assert item["cost"] == 200

    # 2. Second save (should use DB cache, even if CSV is gone)
    os.remove(tmp_path / "items.csv")
    db.save_item(None, "fresh-water", 10)

    item = db.get_item("fresh-water")
    assert item["quantity"] == 10
    assert item["cost"] == 200 # Preserved from DB cache

def test_tm_auto_tagging(temp_env):
    db, _ = temp_env
    # add_item looks up CSV metadata, discovering category_id=37 (TM)
    db.add_item("dragonbreath", 1)

    item = db.get_item("dragonbreath")
    assert (item.get("extra_data") or {}).get("type") == "TM"

def test_badge_schema(temp_env):
    db, _ = temp_env
    db.save_badge("1", {"achieved": True})
    
    badge = db.get_badge("1")
    assert badge["badge_id"] == "1"
    assert badge["achieved"] in [True, 1]

def test_json_migration(temp_env):
    db, tmp_path = temp_env
    
    # Setup legacy files in the paths we'll pass to migrate_from_json
    mypokemon_json = tmp_path / "mypokemon.json"
    mypokemon_json.write_text(json.dumps([]))
    
    mainpokemon_json = tmp_path / "mainpokemon.json"
    mainpokemon_json.write_text(json.dumps({}))
    
    items_json = tmp_path / "items_mig.json"
    items_json.write_text(json.dumps([{"item": "master-ball", "quantity": 1}]))
    
    badges_json = tmp_path / "badges_mig.json"
    badges_json.write_text(json.dumps(["1", "2"]))
    
    with patch("Ankimon.pyobj.database_manager.Path.is_file", return_value=True):
        db.migrate_from_json(
            mypokemon_path=mypokemon_json,
            mainpokemon_path=mainpokemon_json,
            items_path=items_json,
            badges_path=badges_json
        )
        
    # Check items
    item = db.get_item("master-ball")
    assert item["id"] == 1
    
    # Check badges
    badges = db.get_all_badges()
    achieved_ids = [b["badge_id"] for b in badges]
    assert "1" in achieved_ids
    assert "2" in achieved_ids

def test_update_item_quantity_preserves_metadata(temp_env):
    db, _ = temp_env
    db.save_item(100, "elixir", 5, category_id=10, cost=500)
    db.update_item_quantity("elixir", -2)
    
    item = db.get_item("elixir")
    assert item["quantity"] == 3
    assert item["id"] == 100
    assert item["cost"] == 500

def test_consume_item_decrements_and_preserves_metadata(temp_env):
    """Spending one unit must not cost the row its identity."""
    db, _ = temp_env
    db.save_item(100, "elixir", 5, category_id=10, cost=500)

    assert db.consume_item("elixir") is True

    item = db.get_item("elixir")
    assert item["quantity"] == 4
    assert item["id"] == 100
    assert item["cost"] == 500


def test_consume_item_removes_the_row_on_the_last_unit(temp_env):
    db, _ = temp_env
    db.save_item(101, "last-potion", 1)

    assert db.consume_item("last-potion") is True
    assert db.get_item("last-potion") is None


def test_consume_item_refuses_what_is_not_there(temp_env):
    """The distinction ``update_item_quantity`` cannot draw.

    It answers 0 both for "the row was missing" and for "you just spent your
    last one", so a caller handing out an effect on that answer hands it out
    for free. ``consume_item`` reports whether it actually decremented.
    """
    db, _ = temp_env

    assert db.consume_item("never-owned") is False
    assert db.get_item("never-owned") is None


def test_consume_item_refuses_a_leftover_zero_row(temp_env):
    """A row at quantity 0 is not stock, and must not go negative."""
    db, _ = temp_env
    db.save_item(102, "ghost-potion", 0)

    assert db.consume_item("ghost-potion") is False
    item = db.get_item("ghost-potion")
    assert item is None or item["quantity"] == 0


def test_consume_item_cannot_spend_the_same_unit_twice(temp_env):
    """The atomicity that matters: one potion pays for exactly one effect."""
    db, _ = temp_env
    db.save_item(103, "single", 1)

    assert db.consume_item("single") is True
    assert db.consume_item("single") is False
    assert db.get_item("single") is None


def test_consume_item_refuses_a_short_bag_without_partial_payment(temp_env):
    """Asking for more than is there takes nothing at all."""
    db, _ = temp_env
    db.save_item(104, "bulk", 2)

    assert db.consume_item("bulk", count=3) is False
    assert db.get_item("bulk")["quantity"] == 2, "a refused consume still charged the bag"

    assert db.consume_item("bulk", count=2) is True
    assert db.get_item("bulk") is None


def test_consume_item_rolls_back_a_failed_commit(temp_env):
    """A commit that fails must not leave the decrement waiting for a ride.

    sqlite does not necessarily end the transaction when COMMIT fails (a
    busy_timeout expiring under write contention, a full disk), so without a
    rollback the decrement stays pending on this thread's connection. Nobody
    is paid for it -- ``consume_item`` raises rather than returning True, so
    ``Check_Heal_Item`` refuses the heal -- but the next unrelated write on the
    same connection commits, and the pending decrement rides along: the potion
    is gone and the HP was never granted.
    """
    db, _ = temp_env
    db.save_item(105, "contended-potion", 5)

    conn = db._get_connection()
    with patch.object(conn, "commit",
                      side_effect=sqlite3.OperationalError("database is locked")):
        with pytest.raises(sqlite3.OperationalError):
            db.consume_item("contended-potion")

    assert db.get_item("contended-potion")["quantity"] == 5, \
        "the failed payment still charged the bag"

    # The scenario that makes it matter: some later write succeeds on the very
    # same connection. Anything still pending is committed by it.
    db.save_item(106, "unrelated", 1)

    assert _quantity_on_disk(db, "contended-potion") == 5, \
        "the pending decrement rode along on a later successful commit"


def test_consume_item_leaves_no_transaction_open_after_a_failed_commit(temp_env):
    """The mechanism behind the test above: the transaction is actually gone.

    Reading back through the same connection cannot tell a committed row from
    an uncommitted one, so assert on the connection itself.
    """
    db, _ = temp_env
    db.save_item(107, "doomed-potion", 2)

    conn = db._get_connection()
    with patch.object(conn, "commit", side_effect=sqlite3.OperationalError("disk I/O error")):
        with pytest.raises(sqlite3.OperationalError):
            db.consume_item("doomed-potion")

    assert conn.in_transaction is False, \
        "the decrement is still pending and will be committed by the next write"


def test_commit_does_not_report_success_after_a_repair_loses_the_transaction(temp_env):
    """Corruption found at COMMIT time must not be healed into a fake success.

    ``ConnectionWrapper.commit`` recognises "malformed"/"disk image" and calls
    ``repair_database``, which rebuilds the file from a *separate* connection's
    ``iterdump()`` -- so it cannot see rows still pending on this one -- then
    quiesces every registered connection and swaps the rebuilt file into place.
    Neither step carries the pending transaction across. Committing the fresh
    post-repair connection therefore commits nothing, and returning from that
    reports success for a write that no longer exists.

    That is the free-heal ``consume_item`` was written to close: the potion is
    still in the bag, and the caller was told it paid for one.
    """
    db, _ = temp_env
    db.save_item(108, "cursed-potion", 3)

    conn = db._get_connection()
    backup = db.db_path.with_name(db.db_path.name + ".corrupt_backup")
    assert not backup.exists()

    with _corrupt_commits_on(conn) as fake:
        # Through the wrapper, without keeping the cursor: nothing holds a lease,
        # so the repair's quiesce really does drain and the file really is
        # swapped -- the case where the old code returned success.
        conn.execute(
            "UPDATE items SET quantity = 1 WHERE item_name = 'cursed-potion'"
        )
        assert conn._lease_count == 0
        with pytest.raises(sqlite3.DatabaseError, match="malformed"):
            conn.commit()
        assert fake.commit_attempts == 1

    assert backup.exists(), \
        "the repair never ran, so this asserts nothing about the repair branch"
    assert _quantity_on_disk(db, "cursed-potion") == 3, \
        "the pending write survived the repair -- premise of the test is wrong"


def test_consume_item_pays_nothing_when_the_commit_finds_corruption(temp_env):
    """The invariant end-to-end: a decrement lost to a repair is never paid for.

    Today the decrement is doubly safe -- ``consume_item`` holds its cursor, so
    the lease keeps the repair's quiesce from draining and the repair aborts.
    Pin the outcome rather than that mechanism: whichever way the commit fails,
    ``consume_item`` must raise instead of returning True, leave the bag intact,
    and leave nothing pending for the next write to carry.
    """
    db, _ = temp_env
    db.save_item(109, "hexed-potion", 2)

    conn = db._get_connection()
    with _corrupt_commits_on(conn):
        with pytest.raises(sqlite3.DatabaseError, match="malformed"):
            db.consume_item("hexed-potion")

    assert db.get_item("hexed-potion")["quantity"] == 2, \
        "the failed payment still charged the bag"

    db.save_item(110, "unrelated-after-corruption", 1)

    assert _quantity_on_disk(db, "hexed-potion") == 2, \
        "the pending decrement rode along on a later successful commit"


def test_get_item_returns_empty_dict_extras(temp_env):
    db, _ = temp_env
    # Inject a row with NULL data manually to test the default extra_data logic
    conn = db._get_connection()
    conn.execute("INSERT INTO items (id, item_name, quantity, data) VALUES (999, 'null-item', 1, NULL)")
    conn.commit()

    item = db.get_item("null-item")
    assert item["extra_data"] == {} # Should be {} not None


def test_busy_timeout_set_on_gui_connection(temp_env):
    """Concurrency guard: every connection must carry the generous busy-timeout
    so a GUI-thread write does not immediately raise 'database is locked' while
    mobile-sync's bulk 'Resolve All' holds its long background write transaction.
    Without the fix the connect() default (5000ms) would be in effect."""
    db, _ = temp_env
    conn = db._get_connection()
    got = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
    assert got == AnkimonDB._BUSY_TIMEOUT_MS
    assert got >= 30000


def test_busy_timeout_set_on_background_thread_connection(temp_env):
    """The per-background-thread connection (used by the bulk mobile resolve) must
    get the same busy-timeout as the GUI connection — it is the one that races.
    Force the off-GUI-thread branch of _get_connection() so the dedicated
    per-thread connection is what gets probed."""
    db, _ = temp_env
    with patch.object(_db_mod, "_is_main_thread", return_value=False):
        conn = db._get_connection()  # dedicated per-thread connection
        got = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
    assert got == AnkimonDB._BUSY_TIMEOUT_MS


def test_database_corruption_self_healing(temp_env):
    """Verify that repair_database() successfully prunes duplicates (keeping the one
    with the highest progress) and recovers the unique PRIMARY KEY index constraint."""
    db, _ = temp_env
    # Save base pokemon
    pk1 = {"individual_id": "duplicate-uuid", "name": "archen", "id": 566, "level": 5, "xp": 100}
    db.save_pokemon(pk1)
    db.set_main_pokemon("duplicate-uuid")
    
    # Close connections so we can bypass constraints with a custom raw connection
    db.close()
    
    raw_conn = sqlite3.connect(str(db.db_path))
    cursor = raw_conn.cursor()
    
    # Temporarily drop constraint by renaming and copying to a non-constrained table
    cursor.execute("ALTER TABLE captured_pokemon RENAME TO captured_pokemon_old")
    cursor.execute("""
        CREATE TABLE captured_pokemon (
            individual_id TEXT,
            is_main INTEGER DEFAULT 0,
            data TEXT NOT NULL,
            name TEXT GENERATED ALWAYS AS (json_extract(data, '$.name')) VIRTUAL,
            pokedex_id INTEGER GENERATED ALWAYS AS (json_extract(data, '$.id')) VIRTUAL,
            shiny BOOLEAN GENERATED ALWAYS AS (json_extract(data, '$.shiny')) VIRTUAL,
            level INTEGER GENERATED ALWAYS AS (json_extract(data, '$.level')) VIRTUAL
        )
    """)
    cursor.execute("INSERT INTO captured_pokemon (individual_id, is_main, data) SELECT individual_id, is_main, data FROM captured_pokemon_old")
    cursor.execute("DROP TABLE captured_pokemon_old")
    
    # Insert a duplicate with a higher level (Level 34)
    pk2 = {"individual_id": "duplicate-uuid", "name": "archen", "id": 566, "level": 34, "xp": 1000}
    cursor.execute("INSERT INTO captured_pokemon (individual_id, is_main, data) VALUES (?, 0, ?)", 
                   ("duplicate-uuid", json.dumps(pk2)))
                   
    # Insert another duplicate with intermediate level (Level 30)
    pk3 = {"individual_id": "duplicate-uuid", "name": "archen", "id": 566, "level": 30, "xp": 500}
    cursor.execute("INSERT INTO captured_pokemon (individual_id, is_main, data) VALUES (?, 0, ?)", 
                   ("duplicate-uuid", json.dumps(pk3)))
    
    raw_conn.commit()
    raw_conn.close()
    
    # Trigger repair
    db.repair_database()
    
    # Reopen and check: only the highest level (Level 34) should survive, constraint must be restored
    conn = db._get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT is_main, data FROM captured_pokemon WHERE individual_id = 'duplicate-uuid'")
    rows = cursor.fetchall()
    assert len(rows) == 1
    is_main, data = rows[0]
    saved_pk = json.loads(data)
    assert saved_pk["level"] == 34
    assert is_main == 1
    
    # Confirm unique constraint is back by verifying that inserting a duplicate now raises IntegrityError
    with pytest.raises(sqlite3.IntegrityError):
        cursor.execute("INSERT INTO captured_pokemon (individual_id, is_main, data) VALUES ('duplicate-uuid', 0, '{}')")

def test_thread_local_connection_closes_and_reopens(temp_env):
    import threading
    db, _ = temp_env
    
    results = {}
    event_start = threading.Event()
    event_closed = threading.Event()
    
    def thread_func():
        with patch("Ankimon.pyobj.database_manager._is_main_thread", return_value=False):
            # Get connection and verify it works
            conn = db._get_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
                assert cursor.fetchone()[0] == 1
            
            # Notify main thread
            event_start.set()
            assert event_closed.wait(timeout=5), "database close was not signaled"
            
            # Get connection again. It should be refreshed automatically
            conn2 = db._get_connection()
            with conn2.cursor() as cursor2:
                cursor2.execute("SELECT 1")
                results["success"] = (cursor2.fetchone()[0] == 1)
            
    t = threading.Thread(target=thread_func)
    t.start()
    
    assert event_start.wait(timeout=5), "worker did not initialize"
    
    # Close database from main thread
    db.close()
    
    event_closed.set()
    t.join(timeout=5)
    assert not t.is_alive(), "worker did not finish"
    
    assert results.get("success") is True


def test_close_reports_timeout_while_cursor_lease_is_active(temp_env):
    db, _ = temp_env
    conn = db._get_connection()
    cursor = conn.cursor()
    epoch = db._connection_epoch

    assert db.close(0.01) is False
    assert db._connection_epoch == epoch
    assert db._get_connection() is conn
    assert conn._close_pending is False

    cursor.execute("SELECT 1")
    assert cursor.fetchone()[0] == 1
    cursor.close()

    assert conn._closed is False
    assert db.close(0.1) is True
    assert db._connection_epoch == epoch + 1
    assert conn._closed is True


def test_failed_close_preserves_active_transaction_generation(temp_env):
    db, _ = temp_env
    conn = db._get_connection()
    epoch = db._connection_epoch

    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
            ("before_failed_close", "1"),
        ).close()

        assert db.close(0.0) is False
        assert db._connection_epoch == epoch
        assert db._get_connection() is conn

        db.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
            ("after_failed_close", "2"),
        ).close()

    assert db.execute(
        "SELECT value FROM metadata WHERE key = ?",
        ("after_failed_close",),
    ).fetchone()[0] == "2"


def test_switch_database_aborts_when_connections_do_not_drain(temp_env):
    import contextlib

    db, _ = temp_env
    original_path = db.db_path

    @contextlib.contextmanager
    def not_drained(wait_seconds=0.0):
        assert wait_seconds == 2.0
        yield False

    with patch.object(db, "quiesce", not_drained):
        with pytest.raises(RuntimeError, match="active operations did not finish"):
            db.switch_database("ankimonDEV.db")

    assert db.db_path == original_path


def test_close_deduplicates_wrappers_and_shares_deadline(temp_env):
    import weakref

    db, _ = temp_env
    if db._connection is not None:
        db._connection.close()

    clock = [100.0]

    class FakeWrapper:
        def __init__(self, advance):
            self.advance = advance
            self.calls = []

        def close(self, wait_seconds):
            self.calls.append(wait_seconds)
            clock[0] += self.advance
            return False

    first = FakeWrapper(0.75)
    second = FakeWrapper(0.50)
    db._connection = first
    db._all_connections = [
        weakref.ref(first),
        weakref.ref(first),
        weakref.ref(second),
    ]
    db._local_conn.conn = second

    with patch.object(_db_mod.time, "monotonic", side_effect=lambda: clock[0]):
        assert db.close(2.0) is False

    assert first.calls == [pytest.approx(2.0)]
    assert second.calls == [pytest.approx(1.25)]


def test_connection_lease_prevents_closure_during_inflight_operation(temp_env):
    import threading

    db, _ = temp_env
    event_paused = threading.Event()
    event_close_done = threading.Event()
    results = {}

    conn = db._get_connection()
    with conn.cursor() as cursor:
        cursor_wrapper_class = cursor.__class__
    db_module = sys.modules[db.__class__.__module__]
    original_execute = cursor_wrapper_class.execute

    def patched_cursor_execute(cursor_self, sql, *args, **kwargs):
        if "SELECT 'test_inflight'" in sql:
            event_paused.set()
            assert event_close_done.wait(timeout=5), "database close did not complete"
        return original_execute(cursor_self, sql, *args, **kwargs)

    def worker():
        try:
            with db.execute("SELECT 'test_inflight'") as cursor_thread:
                results["value"] = cursor_thread.fetchone()[0]
        except Exception as exc:
            results["error"] = exc

    with (
        patch.object(cursor_wrapper_class, "execute", patched_cursor_execute),
        patch.object(db_module, "_is_main_thread", return_value=False),
    ):
        thread = threading.Thread(target=worker)
        thread.start()
        try:
            assert event_paused.wait(timeout=5), "worker did not initialize"
            db.close()
            event_close_done.set()
            thread.join(timeout=5)
            assert not thread.is_alive(), "worker did not finish"
        finally:
            event_close_done.set()
            thread.join(timeout=5)

    assert results.get("value") == "test_inflight"
    assert "error" not in results


def test_cursor_handoff_holds_lease_before_close(temp_env):
    """Closing during cursor wrapping must not invalidate the raw cursor."""
    import threading

    db, _ = temp_env
    cursor_created = threading.Event()
    continue_wrapping = threading.Event()
    results = {}
    original_init = _db_mod.CursorWrapper.__init__

    def paused_init(cursor_self, raw_cursor, conn_wrapper, *, lease_acquired=False):
        cursor_created.set()
        assert continue_wrapping.wait(timeout=5), "cursor handoff was not resumed"
        original_init(
            cursor_self,
            raw_cursor,
            conn_wrapper,
            lease_acquired=lease_acquired,
        )

    def worker():
        try:
            with patch.object(_db_mod, "_is_main_thread", return_value=False):
                conn = db._get_connection()
                with conn.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    results["value"] = cursor.fetchone()[0]
        except Exception as exc:
            results["error"] = exc

    with patch.object(_db_mod.CursorWrapper, "__init__", paused_init):
        thread = threading.Thread(target=worker)
        thread.start()
        assert cursor_created.wait(timeout=5), "worker did not create its cursor"
        db.close()
        continue_wrapping.set()
        thread.join(timeout=5)
        assert not thread.is_alive(), "worker did not finish"

    assert results.get("value") == 1
    assert "error" not in results


def test_close_waits_for_connection_registration(temp_env):
    import threading
    import time

    db, _ = temp_env
    entered_prepare = threading.Event()
    resume_prepare = threading.Event()
    result = {}
    original_prepare = db._prepare_connection

    def paused_prepare(raw_conn):
        entered_prepare.set()
        assert resume_prepare.wait(timeout=5), "connection registration was not resumed"
        return original_prepare(raw_conn)

    def create_connection():
        with patch.object(_db_mod, "_is_main_thread", return_value=False):
            result["conn"] = db._get_connection()

    def close_database():
        result["closed"] = db.close(1.0)

    db._prepare_connection = paused_prepare
    creator = threading.Thread(target=create_connection)
    closer = threading.Thread(target=close_database)
    creator.start()
    assert entered_prepare.wait(timeout=5), "worker did not open its raw connection"

    closer.start()
    time.sleep(0.05)
    assert closer.is_alive(), "close bypassed in-progress connection registration"

    resume_prepare.set()
    creator.join(timeout=5)
    closer.join(timeout=5)
    db._prepare_connection = original_prepare

    assert not creator.is_alive()
    assert not closer.is_alive()
    assert result["closed"] is True
    assert result["conn"]._closed is True
    assert db._all_connections == []


def test_quiesce_blocks_reopen_until_transition_finishes(temp_env):
    import threading
    import time

    db, _ = temp_env
    started = threading.Event()
    acquired = threading.Event()
    result = {}

    def create_connection():
        started.set()
        with patch.object(_db_mod, "_is_main_thread", return_value=False):
            result["conn"] = db._get_connection()
        acquired.set()

    with db.quiesce(0.1) as closed:
        assert closed is True
        worker = threading.Thread(target=create_connection)
        worker.start()
        assert started.wait(timeout=5)
        time.sleep(0.05)
        assert not acquired.is_set()

    worker.join(timeout=5)
    assert not worker.is_alive()
    assert acquired.is_set()
    assert result["conn"]._closed is False


def test_epoch_double_read_race(temp_env):
    db, _ = temp_env
    original_prepare = db._prepare_connection
    close_called = False
    
    def patched_prepare(conn):
        nonlocal close_called
        res = original_prepare(conn)
        if not close_called:
            close_called = True
            db.close()
        return res
        
    db._prepare_connection = patched_prepare
    
    import threading
    results = {}
    
    def thread_func():
        try:
            with patch("Ankimon.pyobj.database_manager._is_main_thread", return_value=False):
                conn1 = db._get_connection()
                conn2 = db._get_connection()
                with conn2.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    results["success"] = (cursor.fetchone()[0] == 1)
        except Exception as e:
            results["error"] = str(e)
            
    t = threading.Thread(target=thread_func)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive()
    assert results.get("success") is True
    assert "error" not in results


def test_retry_on_closed_database_error(temp_env):
    db, _ = temp_env
    conn = db._get_connection()
    conn._conn.close()
    db._connection_epoch += 1
    
    with conn.cursor() as cursor:
        cursor.execute("SELECT 1")
        assert cursor.fetchone()[0] == 1


@pytest.mark.parametrize(
    ("method_name", "operation_args", "expected"),
    [
        ("execute", ("SELECT 1",), 1),
        (
            "executemany",
            (
                "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                [("retry_handoff_a", "1"), ("retry_handoff_b", "2")],
            ),
            2,
        ),
    ],
)
def test_retry_handoff_holds_lease_before_cursor_wrapper(
    temp_env, method_name, operation_args, expected
):
    import threading

    db, _ = temp_env
    stale = db._get_connection()
    stale._conn.close()
    db._connection_epoch += 1

    entered_handoff = threading.Event()
    resume_handoff = threading.Event()
    result = {}
    original_init = _db_mod.CursorWrapper.__init__
    paused = False

    def paused_init(cursor_self, raw_cursor, conn_wrapper, *, lease_acquired=False):
        nonlocal paused
        if conn_wrapper is not stale and not paused:
            paused = True
            entered_handoff.set()
            assert resume_handoff.wait(timeout=5), "retry handoff was not resumed"
        original_init(
            cursor_self,
            raw_cursor,
            conn_wrapper,
            lease_acquired=lease_acquired,
        )

    def worker():
        try:
            with patch.object(_db_mod, "_is_main_thread", return_value=False):
                cursor = getattr(stale, method_name)(*operation_args)
                try:
                    result["value"] = (
                        cursor.fetchone()[0]
                        if method_name == "execute"
                        else cursor.rowcount
                    )
                finally:
                    cursor.close()
        except Exception as exc:
            result["error"] = exc

    with patch.object(_db_mod.CursorWrapper, "__init__", paused_init):
        thread = threading.Thread(target=worker)
        thread.start()
        assert entered_handoff.wait(timeout=5), "retry did not reach cursor handoff"
        assert db.close(0.01) is False
        resume_handoff.set()
        thread.join(timeout=5)
        assert not thread.is_alive()

    assert result.get("value") == expected
    assert "error" not in result


def test_repair_aborts_when_connections_do_not_drain(temp_env):
    import contextlib

    db, _ = temp_env

    @contextlib.contextmanager
    def not_drained(wait_seconds=0.0):
        assert wait_seconds == 2.0
        yield False

    with patch.object(db, "quiesce", not_drained):
        with pytest.raises(RuntimeError, match="active operations did not finish"):
            db.repair_database()

    assert db.db_path.exists()
    assert not db.db_path.with_name(db.db_path.name + ".tmp").exists()


def test_legacy_base_stats_normalization(temp_env):
    """Verify that old database entries without 'base_stats' key are normalized on repair and startup."""
    db, _ = temp_env
    # Create a pokemon with 'stats' but no 'base_stats'
    legacy_pk = {
        "individual_id": "legacy-uuid",
        "name": "pikachu",
        "id": 25,
        "level": 5,
        "xp": 100,
        "stats": {"hp": 35, "atk": 55, "def": 40, "spa": 50, "spd": 50, "spe": 90},
        "iv": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
        "ev": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0}
    }

    # Save manually to bypass the save_pokemon normalization/check
    obfuscated_data = db._obfuscate(legacy_pk)
    conn = db._get_connection()
    with conn.cursor() as cursor:
        cursor.execute(
            "INSERT INTO captured_pokemon (individual_id, is_main, data) VALUES (?, 0, ?)",
            ("legacy-uuid", obfuscated_data),
        )
    conn.commit()

    # Check that it starts without base_stats
    pk_loaded = db.get_pokemon("legacy-uuid")
    assert "base_stats" not in pk_loaded

    mock_base_stats = {"hp": 35, "atk": 55, "def": 40, "spa": 50, "spd": 50, "spe": 90}
    with patch("Ankimon.functions.pokedex_functions.search_pokedex", return_value=mock_base_stats) as mock_search:
        db.repair_database()
    mock_search.assert_called_once_with("pikachu", "baseStats")

    # Check that base_stats was populated from pokedex lookup
    pk_repaired = db.get_pokemon("legacy-uuid")
    assert "base_stats" in pk_repaired
    assert pk_repaired["base_stats"]["hp"] == 35
    assert pk_repaired["base_stats"]["spe"] == 90


def test_base_stats_normalization_startup_sweep(temp_env):
    """The startup sweep heals legacy records without requiring a full repair."""
    db, _ = temp_env
    legacy_pk = {
        "individual_id": "legacy-uuid-2",
        "name": "pikachu",
        "id": 25,
        "level": 5,
        "stats": {"hp": 35, "atk": 55, "def": 40, "spa": 50, "spd": 50, "spe": 90},
    }
    conn = db._get_connection()
    conn.cursor().execute(
        "INSERT INTO captured_pokemon (individual_id, is_main, data) VALUES (?, 0, ?)",
        ("legacy-uuid-2", db._obfuscate(legacy_pk))
    )
    conn.commit()

    mock_base_stats = {"hp": 35, "atk": 55, "def": 40, "spa": 50, "spd": 50, "spe": 90}
    with patch("Ankimon.functions.pokedex_functions.search_pokedex", return_value=mock_base_stats):
        db._normalize_pokemon_base_stats()

    assert db.get_pokemon("legacy-uuid-2")["base_stats"] == mock_base_stats


def test_unresolvable_base_stats_record_left_untouched(temp_env):
    """A record whose species is missing from the pokedex must not be modified."""
    db, _ = temp_env
    legacy_pk = {
        "individual_id": "legacy-uuid-3",
        "name": "not-a-real-species",
        "id": 9999,
        "level": 5,
        "stats": {"hp": 35, "atk": 55, "def": 40, "spa": 50, "spd": 50, "spe": 90},
    }
    conn = db._get_connection()
    conn.cursor().execute(
        "INSERT INTO captured_pokemon (individual_id, is_main, data) VALUES (?, 0, ?)",
        ("legacy-uuid-3", db._obfuscate(legacy_pk))
    )
    conn.commit()

    with patch("Ankimon.functions.pokedex_functions.search_pokedex", return_value=[]):
        db._normalize_pokemon_base_stats()

    assert db.get_pokemon("legacy-uuid-3") == legacy_pk


def test_user_data_to_config_migration_preserves_existing_values(temp_env):
    db, _ = temp_env
    db.set_user_data("username", "legacy-user")
    db.set_user_data("api_key", "legacy-key")
    db.set_config_value("leaderboard.username", "settings-user")

    migrated = db.migrate_user_data_to_config(
        {
            "username": "leaderboard.username",
            "api_key": "leaderboard.api_key",
        }
    )

    assert migrated == {"leaderboard.api_key": "legacy-key"}
    assert db.get_config_value("leaderboard.username") == "settings-user"
    assert db.get_config_value("leaderboard.api_key") == "legacy-key"
    assert db.get_user_data("username") is None
    assert db.get_user_data("api_key") is None


def test_user_data_to_config_migration_rolls_back_on_write_failure(temp_env):
    db, _ = temp_env
    db.set_user_data("username", "legacy-user")
    db.set_user_data("api_key", "legacy-key")
    conn = db._get_connection()
    conn.execute(
        """
        CREATE TRIGGER fail_leaderboard_api_key
        BEFORE INSERT ON config
        WHEN NEW.key = 'leaderboard.api_key'
        BEGIN
            SELECT RAISE(ABORT, 'injected migration failure');
        END
        """
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="injected migration failure"):
        db.migrate_user_data_to_config(
            {
                "username": "leaderboard.username",
                "api_key": "leaderboard.api_key",
            }
        )

    assert db.get_config_value("leaderboard.username") is None
    assert db.get_config_value("leaderboard.api_key") is None
    assert db.get_user_data("username") == "legacy-user"
    assert db.get_user_data("api_key") == "legacy-key"

