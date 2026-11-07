import re

with open("src/Ankimon/pyobj/database_manager.py", "r") as f:
    content = f.read()

create_table_str = """                is_main INTEGER DEFAULT 0,
                data TEXT NOT NULL,
                name TEXT GENERATED ALWAYS AS (json_extract(data, '$.name')) VIRTUAL,"""
new_create_table_str = """                is_main INTEGER DEFAULT 0,
                data TEXT NOT NULL,
                name TEXT GENERATED ALWAYS AS (json_extract(data, '$.name')) VIRTUAL,
                is_monthly BOOLEAN GENERATED ALWAYS AS (json_extract(data, '$.is_monthly')) VIRTUAL,"""

content = content.replace(create_table_str, new_create_table_str)

alter_table_str = """        # Check if is_main column exists (for migration from old schema)
        cursor.execute("PRAGMA table_info(captured_pokemon)")
        columns = [row[1] for row in cursor.fetchall()]
        if "is_main" not in columns:
            self._log("info", "Migrating schema: adding is_main column...")
            cursor.execute(
                "ALTER TABLE captured_pokemon ADD COLUMN is_main INTEGER DEFAULT 0"
            )
            # Migrate data from old main_pokemon table if it exists"""

is_monthly_patch = """
        if "is_monthly" not in columns:
            self._log("info", "Migrating schema: adding is_monthly column...")
            try:
                cursor.execute(
                    "ALTER TABLE captured_pokemon ADD COLUMN is_monthly BOOLEAN GENERATED ALWAYS AS (json_extract(data, '$.is_monthly')) VIRTUAL"
                )
            except Exception as e:
                if "duplicate column name" not in str(e).lower():
                    raise e
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_pokemon_is_monthly ON captured_pokemon(is_monthly)"
            )
"""

content = content.replace(alter_table_str, alter_table_str + is_monthly_patch)

index_str = """        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_pokemon_level ON captured_pokemon(level)"
        )"""
new_index_str = """        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_pokemon_level ON captured_pokemon(level)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_pokemon_is_monthly ON captured_pokemon(is_monthly)"
        )"""

content = content.replace(index_str, new_index_str)

method_str = """    def set_monthly_challenge_state(self, challenge_id: str, status: int):"""
new_method_str = """    def reconcile_monthly_pokemon(self, individual_ids: list, month_names: dict):
        \"\"\"Retroactively apply is_monthly flag and month string to existing challenge pokemon.\"\"\"
        if not individual_ids:
            return

        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            placeholders = ",".join("?" for _ in individual_ids)
            cursor.execute(f"SELECT individual_id, data FROM captured_pokemon WHERE individual_id IN ({placeholders})", individual_ids)

            updates = []
            import json
            for row in cursor.fetchall():
                ind_id = row[0]
                data = json.loads(row[1])
                # Only update if missing
                if "is_monthly" not in data or data.get("is_monthly") is not True:
                    data["is_monthly"] = True
                    # Set month if missing
                    if ind_id in month_names:
                        data["monthly_month"] = month_names[ind_id]
                    updates.append((json.dumps(data, ensure_ascii=False), ind_id))

            if updates:
                cursor.executemany("UPDATE captured_pokemon SET data = ? WHERE individual_id = ?", updates)
                conn.commit()
                self._clear_reviewer_ownership_cache()
        except Exception as e:
            self._log("error", f"Error reconciling monthly pokemon: {e}")
            conn.rollback()

    def set_monthly_challenge_state(self, challenge_id: str, status: int):"""

content = content.replace(method_str, new_method_str)

with open("src/Ankimon/pyobj/database_manager.py", "w") as f:
    f.write(content)
