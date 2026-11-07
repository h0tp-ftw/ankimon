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
