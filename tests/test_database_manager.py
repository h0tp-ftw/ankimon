import os
import sys
import json
import csv
import sqlite3
import pytest
import importlib.util
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

def test_database_initialization(temp_env):
    db, _ = temp_env
    conn = db._get_connection()
    cursor = conn.cursor()
    
    # Table names updated based on database_manager.py _setup_database
    tables = ["metadata", "items", "badges", "captured_pokemon", "team", "pokemon_history"]
    for table in tables:
        cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'")
        assert cursor.fetchone() is not None, f"Table {table} should exist"

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
