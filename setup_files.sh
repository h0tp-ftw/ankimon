#!/bin/bash
cat << 'PY_EOF' > patch_schema.py
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
PY_EOF

cat << 'PY_EOF' > patch_trade.py
import re

with open("src/Ankimon/pyobj/pokemon_trade.py", "r") as f:
    content = f.read()

create_str = """def create_monthly_challenge_pokemon(pokemon_data, make_shiny=False):"""
new_create_str = """def create_monthly_challenge_pokemon(pokemon_data, month_name=None, make_shiny=False):"""
content = content.replace(create_str, new_create_str)

is_monthly_str = """        "is_favorite": pokemon_data.get("is_favorite", False),
        "held_item": pokemon_data.get("held_item", None),
    }"""
new_is_monthly_str = """        "is_favorite": pokemon_data.get("is_favorite", False),
        "held_item": pokemon_data.get("held_item", None),
        "is_monthly": True,
        "monthly_month": month_name,
    }"""
content = content.replace(is_monthly_str, new_is_monthly_str)

call_str = """        new_pokemon = create_monthly_challenge_pokemon(
            challenge_pokemon_data, make_shiny=make_shiny
        )"""
new_call_str = """        new_pokemon = create_monthly_challenge_pokemon(
            challenge_pokemon_data, month_name=current_challenge.get("month"), make_shiny=make_shiny
        )"""
content = content.replace(call_str, new_call_str)

process_func_str = """    def _process_on_main_thread(
        result_data, db, db_token, col, current_month_str, reclaim
    ):
        \"\"\"Apply the fetched challenge to the database on the main thread.\"\"\"
        if not _session_unchanged(db, db_token, col):
            logger.log(
                "info",
                "Discarded the monthly challenge result: the Ankimon database or Anki profile changed while it was being fetched.",
            )
            return

        if result_data is None:
            if reclaim:
                services.ui.notify(
                    "warning",
                    "No monthly challenge could be loaded. Please try again later.",
                )
            return"""

new_process_func_str = """    def _process_on_main_thread(
        result_data, db, db_token, col, current_month_str, reclaim
    ):
        \"\"\"Apply the fetched challenge to the database on the main thread.\"\"\"
        if not _session_unchanged(db, db_token, col):
            logger.log(
                "info",
                "Discarded the monthly challenge result: the Ankimon database or Anki profile changed while it was being fetched.",
            )
            return

        # Reconcile all challenges first
        if result_data and "all_challenges" in result_data:
            try:
                challenge_ids = []
                month_names = {}
                for c in result_data["all_challenges"]:
                    ind_id = c.get("pokemon", {}).get("individual_id")
                    month = c.get("month")
                    if ind_id and month:
                        challenge_ids.append(ind_id)
                        month_names[ind_id] = month
                if challenge_ids:
                    db.reconcile_monthly_pokemon(challenge_ids, month_names)
            except Exception as e:
                logger.log("error", f"Reconciliation error: {e}")

        if not result_data or not result_data.get("current_challenge"):
            if reclaim:
                services.ui.notify(
                    "warning",
                    "No monthly challenge could be loaded. Please try again later.",
                )
            return"""

content = content.replace(process_func_str, new_process_func_str)

fetch_func_str = """            if not current_challenge:
                logger.log(
                    "info", f"No monthly challenge found for {current_month_str}."
                )
                return None

            challenge_pokemon_data = current_challenge.get("pokemon")
            if not isinstance(challenge_pokemon_data, dict):
                logger.log(
                    "error",
                    f"Monthly challenge for {current_month_str} is missing 'pokemon' data.",
                )
                return None"""
new_fetch_func_str = """            if not current_challenge:
                logger.log(
                    "info", f"No monthly challenge found for {current_month_str}."
                )
                return {
                    "current_challenge": None,
                    "challenge_pokemon_data": None,
                    "all_challenges": monthly_challenges,
                }

            challenge_pokemon_data = current_challenge.get("pokemon")
            if not isinstance(challenge_pokemon_data, dict):
                logger.log(
                    "error",
                    f"Monthly challenge for {current_month_str} is missing 'pokemon' data.",
                )
                return {
                    "current_challenge": None,
                    "challenge_pokemon_data": None,
                    "all_challenges": monthly_challenges,
                }"""
content = content.replace(fetch_func_str, new_fetch_func_str)

fetch_return_str = """            return {
                "current_challenge": current_challenge,
                "challenge_pokemon_data": challenge_pokemon_data,
            }"""
new_fetch_return_str = """            return {
                "current_challenge": current_challenge,
                "challenge_pokemon_data": challenge_pokemon_data,
                "all_challenges": monthly_challenges,
            }"""
content = content.replace(fetch_return_str, new_fetch_return_str)

with open("src/Ankimon/pyobj/pokemon_trade.py", "w") as f:
    f.write(content)
PY_EOF

cat << 'PY_EOF' > patch_pc_box.py
import re

with open("src/Ankimon/pyobj/pc_box.py", "r") as f:
    content = f.read()

ui_str = """        self.filter_is_holding_item = None
        self.filter_shiny = None
        self.sort_by_id = None"""
new_ui_str = """        self.filter_is_holding_item = None
        self.filter_shiny = None
        self.filter_monthly = None
        self.sort_by_id = None"""
content = content.replace(ui_str, new_ui_str)

checkbox_str = """        # Shiny filter
        is_checked = (
            self.filter_shiny.isChecked() if self.filter_shiny is not None else False
        )
        self.filter_shiny = QCheckBox("Shiny")
        self.filter_shiny.setChecked(is_checked)
        self.filter_shiny.stateChanged.connect(lambda: self.go_to_box(0))
        # Sorting options"""
new_checkbox_str = """        # Shiny filter
        is_checked = (
            self.filter_shiny.isChecked() if self.filter_shiny is not None else False
        )
        self.filter_shiny = QCheckBox("Shiny")
        self.filter_shiny.setChecked(is_checked)
        self.filter_shiny.stateChanged.connect(lambda: self.go_to_box(0))

        # Monthly filter
        is_checked = (
            self.filter_monthly.isChecked() if self.filter_monthly is not None else False
        )
        self.filter_monthly = QCheckBox("Monthly")
        self.filter_monthly.setChecked(is_checked)
        self.filter_monthly.stateChanged.connect(lambda: self.go_to_box(0))
        # Sorting options"""
content = content.replace(checkbox_str, new_checkbox_str)

layout_str = """        checkboxes_layout = QHBoxLayout()
        checkboxes_layout.addWidget(self.filter_favorites)
        checkboxes_layout.addWidget(self.filter_is_holding_item)
        checkboxes_layout.addWidget(self.filter_shiny)
        checkboxes_layout.addWidget(self.desc_sort)  # Moved here"""
new_layout_str = """        checkboxes_layout = QHBoxLayout()
        checkboxes_layout.addWidget(self.filter_favorites)
        checkboxes_layout.addWidget(self.filter_is_holding_item)
        checkboxes_layout.addWidget(self.filter_shiny)
        checkboxes_layout.addWidget(self.filter_monthly)
        checkboxes_layout.addWidget(self.desc_sort)  # Moved here"""
content = content.replace(layout_str, new_layout_str)

state_str = """            "shiny": self.filter_shiny.isChecked() if self.filter_shiny else False,
            "favorite": self.filter_favorites.isChecked()"""
new_state_str = """            "shiny": self.filter_shiny.isChecked() if self.filter_shiny else False,
            "monthly": self.filter_monthly.isChecked() if self.filter_monthly else False,
            "favorite": self.filter_favorites.isChecked()"""
content = content.replace(state_str, new_state_str)

query_str = '''            "SELECT individual_id, name, level, pokedex_id as id, shiny as shiny, "
            "rowid as original_index, json_extract(data, \'$.nickname\') as nickname, "'''
new_query_str = '''            "SELECT individual_id, name, level, pokedex_id as id, shiny as shiny, "
            "is_monthly as is_monthly, json_extract(data, \'$.monthly_month\') as monthly_month, rowid as original_index, json_extract(data, \'$.nickname\') as nickname, "'''
content = content.replace(query_str, new_query_str)

filter_sql_str = """        # Shiny filtering
        if self.filter_shiny is not None and self.filter_shiny.isChecked():
            query_parts.append("AND shiny = 1")

        # Generation filtering"""
new_filter_sql_str = """        # Shiny filtering
        if self.filter_shiny is not None and self.filter_shiny.isChecked():
            query_parts.append("AND shiny = 1")

        # Monthly filtering
        if self.filter_monthly is not None and self.filter_monthly.isChecked():
            query_parts.append("AND is_monthly = 1")

        # Generation filtering"""
content = content.replace(filter_sql_str, new_filter_sql_str)

dict_str = """                    "shiny": bool(row["shiny"]),"""
new_dict_str = """                    "shiny": bool(row["shiny"]),
                    "is_monthly": bool(row["is_monthly"] if "is_monthly" in row.keys() and row["is_monthly"] is not None else False),
                    "monthly_month": row["monthly_month"] if "monthly_month" in row.keys() else None,"""
content = content.replace(dict_str, new_dict_str)

call_str = """            gif_in_collection=self.gif_in_collection,
            remove_levelcap=self.settings.get("misc.remove_level_cap"),
            logger=self.logger,"""
new_call_str = """            gif_in_collection=self.gif_in_collection,
            remove_levelcap=self.settings.get("misc.remove_level_cap"),
            logger=self.logger,
            monthly_month=pokemon.get("monthly_month"),"""
content = content.replace(call_str, new_call_str)

with open("src/Ankimon/pyobj/pc_box.py", "w") as f:
    f.write(content)
PY_EOF

cat << 'PY_EOF' > patch_ui.py
import re

with open("src/Ankimon/gui_classes/pokemon_details.py", "r") as f:
    content = f.read()

sig_str = """    gif_in_collection,
    remove_levelcap: bool,
    logger: ShowInfoLogger,
    refresh_callback,"""
new_sig_str = """    gif_in_collection,
    remove_levelcap: bool,
    logger: ShowInfoLogger,
    refresh_callback,
    monthly_month: str = None,"""
content = content.replace(sig_str, new_sig_str)

label_str = """        name_label = QLabel(display_full_name)
        name_label.setFont(namefont)
        description_label = QLabel(description_txt)"""
new_label_str = """        name_label = QLabel(display_full_name)
        name_label.setFont(namefont)

        monthly_label = None
        if monthly_month:
            monthly_label = QLabel(f"Monthly Challenge: {monthly_month}")
            monthly_font = load_custom_font(12, language)
            monthly_label.setFont(monthly_font)
            monthly_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            monthly_label.setStyleSheet("color: #fbbf24; font-weight: bold; letter-spacing: 0.5px;")

        description_label = QLabel(description_txt)"""
content = content.replace(label_str, new_label_str)

layout_str = """        header_layout = QVBoxLayout(header_widget)
        header_layout.addWidget(name_label)
        header_layout.addLayout(first_layout)"""
new_layout_str = """        header_layout = QVBoxLayout(header_widget)
        header_layout.addWidget(name_label)
        if monthly_label:
            header_layout.addWidget(monthly_label)
        header_layout.addLayout(first_layout)"""
content = content.replace(layout_str, new_layout_str)

wrapper_call = """    header_widget, stats_tabs, footer_widget, _stats_dict = (
        PokemonCollectionDetailsSplit(
            name=name,
            level=level,
            id=id,
            shiny=shiny,
            ability=ability,
            type=type,
            detail_stats=detail_stats,
            attacks=attacks,
            base_experience=base_experience,
            growth_rate=growth_rate,
            ev=ev,
            iv=iv,
            gender=gender,
            nickname=nickname,
            individual_id=individual_id,
            pokemon_defeated=pokemon_defeated,
            everstone=everstone,
            captured_date=captured_date,
            language=language,
            gif_in_collection=gif_in_collection,
            remove_levelcap=remove_levelcap,
            logger=logger,
            refresh_callback=refresh_callback,
            initial_tab_index=initial_tab_index,
            tab_changed_callback=tab_changed_callback,
            nature=nature,
            base_stats=base_stats,
            old_stats=old_stats,
            friendship=friendship,
            evolution_rejected=evolution_rejected,
            friendship_time_enabled=friendship_time_enabled,
            trigger_evo_callback=trigger_evo_callback,
            show_sprites=show_sprites,
        )
    )"""

new_wrapper_call = """    header_widget, stats_tabs, footer_widget, _stats_dict = (
        PokemonCollectionDetailsSplit(
            name=name,
            level=level,
            id=id,
            shiny=shiny,
            ability=ability,
            type=type,
            detail_stats=detail_stats,
            attacks=attacks,
            base_experience=base_experience,
            growth_rate=growth_rate,
            ev=ev,
            iv=iv,
            gender=gender,
            nickname=nickname,
            individual_id=individual_id,
            pokemon_defeated=pokemon_defeated,
            everstone=everstone,
            captured_date=captured_date,
            language=language,
            gif_in_collection=gif_in_collection,
            remove_levelcap=remove_levelcap,
            logger=logger,
            refresh_callback=refresh_callback,
            monthly_month=monthly_month,
            initial_tab_index=initial_tab_index,
            tab_changed_callback=tab_changed_callback,
            nature=nature,
            base_stats=base_stats,
            old_stats=old_stats,
            friendship=friendship,
            evolution_rejected=evolution_rejected,
            friendship_time_enabled=friendship_time_enabled,
            trigger_evo_callback=trigger_evo_callback,
            show_sprites=show_sprites,
        )
    )"""

content = content.replace(wrapper_call, new_wrapper_call)

with open("src/Ankimon/gui_classes/pokemon_details.py", "w") as f:
    f.write(content)
PY_EOF

python3 patch_schema.py
python3 patch_trade.py
python3 patch_pc_box.py
python3 patch_ui.py
