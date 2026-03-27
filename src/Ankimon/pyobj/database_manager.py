"""
AnkimonDB - Consolidated Database Manager for Ankimon

This module provides a SQLite-based storage solution for all Ankimon game data,
replacing multiple JSON files with a single, obfuscated database file.
"""

import base64
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..resources import user_path


class AnkimonDB:
    """Handles all database operations for Ankimon. Stores data in SQLite."""
    
    DB_FILENAME = "ankimon.db"

    def __init__(self, logger=None):
        self.logger = logger
        self.db_path = user_path / self.DB_FILENAME
        self._connection: Optional[sqlite3.Connection] = None
        self._setup_database()

    def _log(self, level: str, message: str):
        """Helper for logging."""
        if self.logger:
            self.logger.log(level, message)
        else:
            print(f"[{level}] {message}")

    # --- Connection Management ---

    def _get_connection(self) -> sqlite3.Connection:
        """Gets or creates a database connection."""
        if self._connection is None:
            self._connection = sqlite3.connect(str(self.db_path))
            self._connection.row_factory = sqlite3.Row  # Access columns by name
        return self._connection

    def close(self):
        """Closes the database connection."""
        if self._connection:
            self._connection.close()
            self._connection = None

    # --- Obfuscation / De-obfuscation ---

    def _obfuscate(self, data: Any) -> str:
        """Serializes a Python object to a JSON string. (Formerly obfuscated)"""
        return json.dumps(data, ensure_ascii=False)

    def _deobfuscate(self, data_str: str) -> Optional[Any]:
        """Deserializes a JSON string to a Python object. (Formerly deobfuscated)"""
        if not data_str:
            return None
        try:
            return json.loads(data_str)
        except Exception as e:
            self._log("error", f"Failed to load json data: {e}")
            return None

    # --- Database Setup ---

    def _setup_database(self):
        """Creates all necessary tables if they don't exist."""
        conn = self._get_connection()
        cursor = conn.cursor()

        # Table for captured pokemon (replaces mypokemon.json AND mainpokemon.json)
        # is_main flag: 0 = not main, 1 = main pokemon
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS captured_pokemon (
                individual_id TEXT PRIMARY KEY,
                is_main INTEGER DEFAULT 0,
                data TEXT NOT NULL,
                name TEXT GENERATED ALWAYS AS (json_extract(data, '$.name')) VIRTUAL,
                pokedex_id INTEGER GENERATED ALWAYS AS (json_extract(data, '$.id')) VIRTUAL,
                shiny BOOLEAN GENERATED ALWAYS AS (json_extract(data, '$.shiny')) VIRTUAL,
                level INTEGER GENERATED ALWAYS AS (json_extract(data, '$.level')) VIRTUAL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pokemon_name ON captured_pokemon(name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pokemon_pokedex_id ON captured_pokemon(pokedex_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pokemon_shiny ON captured_pokemon(shiny)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pokemon_level ON captured_pokemon(level)")

        # Check if is_main column exists (for migration from old schema)
        cursor.execute("PRAGMA table_info(captured_pokemon)")
        columns = [row[1] for row in cursor.fetchall()]
        if "is_main" not in columns:
            self._log("info", "Migrating schema: adding is_main column...")
            cursor.execute("ALTER TABLE captured_pokemon ADD COLUMN is_main INTEGER DEFAULT 0")
            # Migrate data from old main_pokemon table if it exists
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='main_pokemon'")
            if cursor.fetchone():
                cursor.execute("SELECT individual_id, data FROM main_pokemon WHERE id = 1")
                row = cursor.fetchone()
                if row:
                    main_id = row[0]
                    main_data = row[1]
                    # Update the existing pokemon to be main, or insert if not exists
                    cursor.execute(
                        "INSERT OR REPLACE INTO captured_pokemon (individual_id, is_main, data) VALUES (?, 1, ?)",
                        (main_id, main_data)
                    )
                cursor.execute("DROP TABLE main_pokemon")
                self._log("info", "Migrated main_pokemon table to is_main flag")

        # Table for items (replaces items.json)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS items (
                item_name TEXT PRIMARY KEY,
                quantity INTEGER DEFAULT 0,
                data TEXT
            )
        """)

        # Table for badges (replaces badges.json)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS badges (
                badge_id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
        """)

        # Metadata table for tracking migration status, etc.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        # Table for team composition (replaces team.json)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS team (
                slot_position INTEGER PRIMARY KEY,
                individual_id TEXT NOT NULL
            )
        """)

        # Table for released pokemon history (replaces pokemon_history.json)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pokemon_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                individual_id TEXT UNIQUE,
                data TEXT NOT NULL
            )
        """)

        # Table for user data/credentials (replaces data.json)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_data (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        # Table for config settings (replaces config.obf)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        conn.commit()
        self._log("info", "AnkimonDB: Database schema initialized.")

    # --- Captured Pokemon Operations ---

    def save_pokemon(self, pokemon_data: Dict[str, Any]):
        """Saves or updates a captured pokemon. Preserves is_main flag if pokemon already exists."""
        individual_id = pokemon_data.get("individual_id")
        if not individual_id:
            self._log("error", "Cannot save pokemon without individual_id")
            return False

        obfuscated_data = self._obfuscate(pokemon_data)
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Check if pokemon already exists to preserve is_main flag
        cursor.execute("SELECT is_main FROM captured_pokemon WHERE individual_id = ?", (individual_id,))
        row = cursor.fetchone()
        
        if row:
            # Update existing - preserve is_main
            cursor.execute(
                "UPDATE captured_pokemon SET data = ? WHERE individual_id = ?",
                (obfuscated_data, individual_id)
            )
        else:
            # Insert new with is_main = 0
            cursor.execute(
                "INSERT INTO captured_pokemon (individual_id, is_main, data) VALUES (?, 0, ?)",
                (individual_id, obfuscated_data)
            )
        conn.commit()
        return True

    def get_pokemon(self, individual_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a specific pokemon by its individual_id."""
        cursor = self.execute(
            "SELECT data FROM captured_pokemon WHERE individual_id = ?",
            (individual_id,)
        )
        row = cursor.fetchone()
        if row:
            return self._deobfuscate(row["data"])
        return None

    def get_all_pokemon(self) -> List[Dict[str, Any]]:
        """Retrieves all captured pokemon."""
        cursor = self.execute("SELECT data FROM captured_pokemon")
        results = []
        for row in cursor.fetchall():
            pokemon = self._deobfuscate(row["data"])
            if pokemon:
                results.append(pokemon)

        return results

    def has_pokemon_by_name(self, name: str) -> bool:
        """
        Efficiently checks if a pokemon with the given name exists in the collection.
        Uses a direct SQL query on the virtual name index.
        """
        cursor = self.execute("SELECT 1 FROM captured_pokemon WHERE LOWER(name) = LOWER(?) LIMIT 1", (name,))
        return cursor.fetchone() is not None

    def delete_pokemon(self, individual_id: str) -> bool:
        """Deletes a pokemon from the captured collection."""
        cursor = self.execute(
            "DELETE FROM captured_pokemon WHERE individual_id = ?",
            (individual_id,)
        )
        self._get_connection().commit()
        return cursor.rowcount > 0

    def replace_pokemon(self, pokemon_data: Dict[str, Any], old_individual_id: str) -> bool:
        """Replaces a pokemon with the given individual_id with the given pokemon_data."""

        obfuscated_data = self._obfuscate(pokemon_data)
        conn = self._get_connection()
        cursor = conn.cursor()

        new_individual_id = pokemon_data["individual_id"]

        # Are we trying to replace ourselves?
        if new_individual_id == old_individual_id:
            self._log("error", f"You already have this {pokemon_data['name']} in your collection!")
            return False


        # Does the pokemon being replaced exist?
        cursor.execute(
            "SELECT is_main FROM captured_pokemon WHERE individual_id = ?",
            (old_individual_id,)
        )
        row = cursor.fetchone()

        if row is None:
            self._log("error", f"No Pokémon found with individual_id {old_individual_id}")
            return False

        is_main = row[0]

        # Does the incoming Pokémon already exist somewhere else?
        cursor.execute(
            "SELECT 1 FROM captured_pokemon WHERE individual_id = ?",
            (new_individual_id,)
        )
        if cursor.fetchone() is not None:
            self._log("error", f"You already have this {pokemon_data['name']} in your collection!")
            return False

        # You passed all the checks. Full steam ahead!
        # Replace the row in-place
        cursor.execute(
            """
            UPDATE captured_pokemon
            SET individual_id = ?, is_main = ?, data = ?
            WHERE individual_id = ?
            """,
            (new_individual_id, is_main, obfuscated_data, old_individual_id)
        )

        conn.commit()

        return cursor.rowcount > 0

    def get_pokemon_count(self) -> int:
        """Returns the count of captured pokemon."""
        cursor = self.execute("SELECT COUNT(*) FROM captured_pokemon")
        return cursor.fetchone()[0]

    def get_shiny_count(self) -> int:
        """Returns the count of shiny pokemon."""
        cursor = self.execute("SELECT COUNT(*) FROM captured_pokemon WHERE shiny = 1")
        return cursor.fetchone()[0]

    def execute(self, query: str, parameters: tuple = ()) -> sqlite3.Cursor:
        """Executes a custom SQL query and returns the cursor. 
        Useful for caller-specific fast-path queries without cluttering the manager."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(query, parameters)
        return cursor

    def get_pokemons_by_individual_ids(self, ids: List[str]) -> List[Dict[str, Any]]:
        """Retrieves multiple pokemon by their individual_ids."""
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        cursor = self.execute(f"SELECT data FROM captured_pokemon WHERE individual_id IN ({placeholders})", ids)
        results = []
        for row in cursor.fetchall():
            pokemon = self._deobfuscate(row["data"])
            if pokemon:
                results.append(pokemon)
        return results

    def get_all_pokemon_ids(self) -> set:
        """Returns a set of all captured pokemon's pokedex IDs using the virtual index."""
        cursor = self.execute("SELECT pokedex_id FROM captured_pokemon WHERE pokedex_id IS NOT NULL")
        return {row[0] for row in cursor.fetchall()}

    # --- Main Pokemon Operations ---

    def save_main_pokemon(self, pokemon_data: Dict[str, Any]):
        """Saves/updates the main pokemon. Sets is_main=1 on this pokemon, is_main=0 on all others."""
        individual_id = pokemon_data.get("individual_id")
        if not individual_id:
            self._log("error", "Cannot save main pokemon without individual_id")
            return False

        obfuscated_data = self._obfuscate(pokemon_data)
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Clear the main flag from all pokemon first
        cursor.execute("UPDATE captured_pokemon SET is_main = 0 WHERE is_main = 1")
        
        # Save/update this pokemon and set as main
        cursor.execute(
            "INSERT OR REPLACE INTO captured_pokemon (individual_id, is_main, data) VALUES (?, 1, ?)",
            (individual_id, obfuscated_data)
        )
        conn.commit()
        return True

    def get_main_pokemon(self) -> Optional[Dict[str, Any]]:
        """Retrieves the main pokemon (the one with is_main=1)."""
        cursor = self.execute("SELECT data FROM captured_pokemon WHERE is_main = 1")
        row = cursor.fetchone()
        if row:
            return self._deobfuscate(row["data"])
        return None

    def set_main_pokemon(self, individual_id: str) -> bool:
        """Sets a pokemon as the main pokemon by individual_id. Returns False if pokemon not found."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Check if pokemon exists
        cursor.execute("SELECT individual_id FROM captured_pokemon WHERE individual_id = ?", (individual_id,))
        if not cursor.fetchone():
            return False
        
        # Clear old main
        cursor.execute("UPDATE captured_pokemon SET is_main = 0 WHERE is_main = 1")
        # Set new main
        cursor.execute("UPDATE captured_pokemon SET is_main = 1 WHERE individual_id = ?", (individual_id,))
        conn.commit()
        return True

    # --- Item Operations ---

    def save_item(self, item_name: str, quantity: int, extra_data: Optional[Dict] = None):
        """Saves or updates an item."""
        obfuscated_data = self._obfuscate(extra_data) if extra_data else None
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO items (item_name, quantity, data) VALUES (?, ?, ?)",
            (item_name, quantity, obfuscated_data)
        )
        conn.commit()
        return True

    def get_item(self, item_name: str) -> Optional[Dict[str, Any]]:
        """Retrieves an item by name."""
        cursor = self.execute(
            "SELECT item_name, quantity, data FROM items WHERE item_name = ?",
            (item_name,)
        )
        row = cursor.fetchone()
        if row:
            return {
                "item_name": row["item_name"],
                "quantity": row["quantity"],
                "data": self._deobfuscate(row["data"]) if row["data"] else None
            }
        return None

    def get_all_items(self) -> List[Dict[str, Any]]:
        """Retrieves all items."""
        cursor = self.execute("SELECT item_name, quantity, data FROM items")
        results = []
        for row in cursor.fetchall():
            results.append({
                "item_name": row["item_name"],
                "quantity": row["quantity"],
                "data": self._deobfuscate(row["data"]) if row["data"] else None
            })
        return results

    def update_item_quantity(self, item_name: str, delta: int) -> int:
        """Updates item quantity by delta. Returns new quantity."""
        conn = self._get_connection()
        cursor = conn.cursor()

        # Get current quantity
        cursor.execute("SELECT quantity FROM items WHERE item_name = ?", (item_name,))
        row = cursor.fetchone()
        current_qty = row["quantity"] if row else 0
        new_qty = max(0, current_qty + delta)

        if new_qty > 0:
            cursor.execute(
                "INSERT OR REPLACE INTO items (item_name, quantity) VALUES (?, ?)",
                (item_name, new_qty)
            )
        else:
            cursor.execute("DELETE FROM items WHERE item_name = ?", (item_name,))

        conn.commit()
        return new_qty

    # --- Badge Operations ---

    def save_badge(self, badge_id: str, badge_data: Dict[str, Any]):
        """Saves or updates a badge."""
        obfuscated_data = self._obfuscate(badge_data)
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO badges (badge_id, data) VALUES (?, ?)",
            (badge_id, obfuscated_data)
        )
        conn.commit()
        return True

    def get_badge(self, badge_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a badge by ID."""
        cursor = self.execute("SELECT data FROM badges WHERE badge_id = ?", (badge_id,))
        row = cursor.fetchone()
        if row:
            return self._deobfuscate(row["data"])
        return None

    def get_all_badges(self) -> List[Dict[str, Any]]:
        """Retrieves all badges."""
        cursor = self.execute("SELECT badge_id, data FROM badges")
        results = []
        for row in cursor.fetchall():
            badge = self._deobfuscate(row["data"])
            if badge:
                badge["badge_id"] = row["badge_id"]
                results.append(badge)
        return results

    # --- Team Operations ---

    def save_team(self, team_list: List[Dict[str, Any]]):
        """Saves the team composition. Replaces existing team."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # clear existing team
        cursor.execute("DELETE FROM team")
        
        for i, member in enumerate(team_list):
            individual_id = member.get("individual_id")
            if individual_id:
                cursor.execute(
                    "INSERT INTO team (slot_position, individual_id) VALUES (?, ?)",
                    (i + 1, individual_id)
                )
        conn.commit()
        return True

    def get_team(self) -> List[Dict[str, Any]]:
        """Retrieves the current team as a list of dicts with individual_id."""
        cursor = self.execute("SELECT individual_id FROM team ORDER BY slot_position ASC")
        results = []
        for row in cursor.fetchall():
            results.append({"individual_id": row["individual_id"]})
        return results

    # --- Pokemon History Operations ---

    def add_to_history(self, pokemon_data: Dict[str, Any]):
        """Adds a released pokemon to history."""
        # Ensure individual_id exists to avoid duplicates if possible, or just generate one
        individual_id = pokemon_data.get("individual_id") or str(uuid.uuid4())
        
        obfuscated_data = self._obfuscate(pokemon_data)
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO pokemon_history (individual_id, data) VALUES (?, ?)",
                (individual_id, obfuscated_data)
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            self._log("warning", f"Pokemon {individual_id} already in history.")
            return False

    def get_history(self) -> List[Dict[str, Any]]:
        """Retrieves all released pokemon history."""
        cursor = self.execute("SELECT data FROM pokemon_history")
        results = []
        for row in cursor.fetchall():
            data = self._deobfuscate(row["data"])
            if data:
                results.append(data)
        return results

    # --- User Data Operations ---

    def set_user_data(self, key: str, value: Any):
        """Sets a user data key-value pair."""
        # Store as simple string if possible, or JSON string for complex objects
        str_value = json.dumps(value) if isinstance(value, (dict, list, bool)) else str(value)
        
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO user_data (key, value) VALUES (?, ?)",
            (key, str_value)
        )
        conn.commit()
        return True

    def get_user_data(self, key: str, default: Any = None) -> Any:
        """Retrieves user data by key."""
        cursor = self.execute("SELECT value FROM user_data WHERE key = ?", (key,))
        row = cursor.fetchone()
        if row:
            val = row["value"]
            # Try to parse as JSON, fallback to string
            try:
                return json.loads(val)
            except:
                return val
        return default

    def get_all_user_data(self) -> Dict[str, Any]:
        """Retrieves all user data as a dictionary."""
        cursor = self.execute("SELECT key, value FROM user_data")
        result = {}
        for row in cursor.fetchall():
            key = row["key"]
            val = row["value"]
            try:
                result[key] = json.loads(val)
            except:
                result[key] = val
        return result

    # --- Config Operations (replaces config.obf) ---

    def set_config_value(self, key: str, value: Any):
        """Sets a config key-value pair."""
        # Store as JSON string to preserve type information
        str_value = json.dumps(value) if isinstance(value, (dict, list, bool)) else str(value)
        
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
            (key, str_value)
        )
        conn.commit()
        return True

    def get_config_value(self, key: str, default: Any = None) -> Any:
        """Retrieves a config value by key."""
        cursor = self.execute("SELECT value FROM config WHERE key = ?", (key,))
        row = cursor.fetchone()
        if row:
            val = row["value"]
            # Try to parse as JSON, fallback to string
            try:
                return json.loads(val)
            except:
                return val
        return default

    def get_all_config(self) -> Dict[str, Any]:
        """Retrieves all config settings as a dictionary."""
        cursor = self.execute("SELECT key, value FROM config")
        result = {}
        for row in cursor.fetchall():
            key = row["key"]
            val = row["value"]
            try:
                result[key] = json.loads(val)
            except:
                result[key] = val
        return result

    def save_all_config(self, config_dict: Dict[str, Any]):
        """Bulk saves a config dictionary to the database."""
        conn = self._get_connection()
        cursor = conn.cursor()
        for key, value in config_dict.items():
            str_value = json.dumps(value) if isinstance(value, (dict, list, bool)) else str(value)
            cursor.execute(
                "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
                (key, str_value)
            )
        conn.commit()
        return True

    def has_config(self) -> bool:
        """Checks if config data exists in the database."""
        cursor = self.execute("SELECT COUNT(*) FROM config")
        return cursor.fetchone()[0] > 0

    def get_stats(self) -> Dict[str, int]:
        """Returns a summary of database contents for synchronization/backup comparison."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        stats = {}
        
        # Count pokemon
        cursor.execute("SELECT COUNT(*) as count FROM captured")
        stats["pokemon"] = cursor.fetchone()["count"]
        
        # Count items
        cursor.execute("SELECT COUNT(*) as count FROM items")
        stats["items"] = cursor.fetchone()["count"]
        
        # Count history
        cursor.execute("SELECT COUNT(*) as count FROM pokemon_history")
        stats["history"] = cursor.fetchone()["count"]
        
        # Count badges
        cursor.execute("SELECT COUNT(*) as count FROM badges")
        stats["badges"] = cursor.fetchone()["count"]
        
        return stats

    # --- Migration from JSON Files ---

    def migrate_from_json(self, mypokemon_path: Path, mainpokemon_path: Path,
                          items_path: Path, badges_path: Path,
                          team_path: Path = None, history_path: Path = None,
                          data_path: Path = None, rate_path: Path = None) -> Dict[str, int]:
        """
        Migrates data from JSON files to the database.
        Returns a dict with counts of migrated items.
        """
        stats = {"pokemon": 0, "main": 0, "items": 0, "badges": 0, 
                 "team": 0, "history": 0, "userdata": 0}

        # Check if already migrated
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM metadata WHERE key = 'migrated_phase2'")
        if cursor.fetchone():
            self._log("info", "Database Phase 2 (full) already migrated. Checking Phase 1...")
            # If Phase 2 is done, Phase 1 is definitely done.
            return stats
        
        # Check Phase 1 migration (captured, items, badges)
        cursor.execute("SELECT value FROM metadata WHERE key = 'migrated'")
        phase1_done = cursor.fetchone() is not None

        if not phase1_done:
            # Migrate mypokemon.json
            if mypokemon_path.is_file():
                try:
                    with open(mypokemon_path, 'r', encoding='utf-8') as f:
                        pokemon_list = json.load(f)
                    for pokemon in pokemon_list:
                        if self.save_pokemon(pokemon):
                            stats["pokemon"] += 1
                    self._log("info", f"Migrated {stats['pokemon']} pokemon from mypokemon.json")
                except Exception as e:
                    self._log("error", f"Failed to migrate mypokemon.json: {e}")

            # Migrate mainpokemon.json
            if mainpokemon_path.is_file():
                try:
                    with open(mainpokemon_path, 'r', encoding='utf-8') as f:
                        main_data = json.load(f)
                    if main_data:
                        # mainpokemon.json is a list with one item
                        main_pokemon = main_data[0] if isinstance(main_data, list) else main_data
                        if self.save_main_pokemon(main_pokemon):
                            stats["main"] = 1
                    self._log("info", "Migrated main pokemon from mainpokemon.json")
                except Exception as e:
                    self._log("error", f"Failed to migrate mainpokemon.json: {e}")

            # Migrate items.json
            if items_path.is_file():
                try:
                    with open(items_path, 'r', encoding='utf-8') as f:
                        items_list = json.load(f)
                    for item in items_list:
                        # items.json uses 'item' key, but also support 'name' and 'item_name'
                        item_name = item.get("item") or item.get("name") or item.get("item_name")
                        quantity = item.get("quantity", item.get("amount", 1))
                        if item_name:
                            extra_data = {"type": item.get("type")} if item.get("type") else None
                            self.save_item(item_name, quantity, extra_data)
                            stats["items"] += 1
                    self._log("info", f"Migrated {stats['items']} items from items.json")
                except Exception as e:
                    self._log("error", f"Failed to migrate items.json: {e}")

            # Migrate badges.json - handles both [1, 2, 3] and [{"id": 1}, ...] formats
            if badges_path.is_file():
                try:
                    with open(badges_path, 'r', encoding='utf-8') as f:
                        badges_list = json.load(f)
                    for badge in badges_list:
                        # Handle both integer and dict formats
                        if isinstance(badge, int):
                            badge_id = str(badge)
                            badge_data = {"id": badge}
                        else:
                            badge_id = str(badge.get("id", badge.get("badge_id", "")))
                            badge_data = badge
                        if badge_id:
                            self.save_badge(badge_id, badge_data)
                            stats["badges"] += 1
                    self._log("info", f"Migrated {stats['badges']} badges from badges.json")
                except Exception as e:
                    self._log("error", f"Failed to migrate badges.json: {e}")
            
            # Mark Phase 1 as done
            cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('migrated', 'true')")

        # --- Phase 2 Migration (Team, History, UserData) ---
        
        # Migrate team.json
        if team_path and team_path.is_file():
            try:
                with open(team_path, 'r', encoding='utf-8') as f:
                    team_list = json.load(f)
                if self.save_team(team_list):
                    stats["team"] = len(team_list)
                self._log("info", f"Migrated {stats['team']} team members from team.json")
            except Exception as e:
                self._log("error", f"Failed to migrate team.json: {e}")

        # Migrate pokemon_history.json
        if history_path and history_path.is_file():
            try:
                with open(history_path, 'r', encoding='utf-8') as f:
                    history_list = json.load(f)
                for pokemon in history_list:
                    if self.add_to_history(pokemon):
                        stats["history"] += 1
                self._log("info", f"Migrated {stats['history']} history entries from pokemon_history.json")
            except Exception as e:
                self._log("error", f"Failed to migrate pokemon_history.json: {e}")

        # Migrate data.json (User Credentials)
        if data_path and data_path.is_file():
            try:
                with open(data_path, 'r', encoding='utf-8') as f:
                    user_data = json.load(f)
                count = 0
                for key, value in user_data.items():
                    self.set_user_data(key, value)
                    count += 1
                stats["userdata"] = count
                self._log("info", f"Migrated {stats['userdata']} keys from data.json")
            except Exception as e:
                self._log("error", f"Failed to migrate data.json: {e}")

        # Step 8: Migrate rate_this.json
        if rate_path and rate_path.is_file():
            try:
                with open(rate_path, 'r', encoding='utf-8') as f:
                    rate_data = json.load(f)
                
                if isinstance(rate_data, dict) and rate_data.get("rate_this"):
                    self.set_user_data("rate_this", "true")
                    self._log("info", "Migrated rate_this.json")
            except Exception as e:
                self._log("error", f"Failed to migrate rate_this.json: {e}")

        # Mark Phase 2 as done
        cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('migrated_phase2', 'true')")
        conn.commit()

        # --- Integrity Check ---
        # Verify that database counts match expected counts from JSON files
        integrity_issues = []
        
        # Count JSON entries
        json_counts = {"pokemon": 0, "items": 0, "badges": 0}
        try:
            if mypokemon_path.is_file():
                with open(mypokemon_path, 'r', encoding='utf-8') as f:
                    json_counts["pokemon"] = len(json.load(f))
            if items_path.is_file():
                with open(items_path, 'r', encoding='utf-8') as f:
                    json_counts["items"] = len(json.load(f))
            if badges_path.is_file():
                with open(badges_path, 'r', encoding='utf-8') as f:
                    json_counts["badges"] = len(json.load(f))
        except Exception as e:
            self._log("warning", f"Could not read JSON files for integrity check: {e}")
        
        # Count database entries
        db_counts = {"pokemon": 0, "items": 0, "badges": 0}
        cursor.execute("SELECT COUNT(*) FROM all_pokemon")
        db_counts["pokemon"] = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM items")
        db_counts["items"] = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM badges")
        db_counts["badges"] = cursor.fetchone()[0]
        
        # Compare counts
        for key in ["pokemon", "items", "badges"]:
            if json_counts[key] > 0 and db_counts[key] < json_counts[key]:
                integrity_issues.append(
                    f"{key}: JSON has {json_counts[key]} entries but DB only has {db_counts[key]}"
                )
        
        if integrity_issues:
            self._log("warning", f"Migration integrity issues detected: {integrity_issues}")
            stats["integrity_issues"] = integrity_issues
        else:
            self._log("info", "Migration integrity check passed - all counts match.")

        self._log("info", f"Migration complete: {stats}")
        return stats

    # --- Utility ---

    def is_migrated(self) -> bool:
        """Checks if ALL JSON data (Phase 1 & 2) has been migrated to the database."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM metadata WHERE key = 'migrated_phase2'")
        row = cursor.fetchone()
        return row is not None and row["value"] == "true"

    def is_migrated_phase1(self) -> bool:
        """Checks if Phase 1 data (pokemon, items, badges) has been migrated."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM metadata WHERE key = 'migrated'")
        row = cursor.fetchone()
        return row is not None and row["value"] == "true"


# Singleton instance for use throughout the addon
_db_instance: Optional[AnkimonDB] = None


def get_db(logger=None) -> AnkimonDB:
    """Gets the singleton database instance."""
    global _db_instance
    if _db_instance is None:
        _db_instance = AnkimonDB(logger)
    return _db_instance
