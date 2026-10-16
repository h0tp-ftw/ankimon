import json
import uuid
import pytest
from unittest.mock import patch
from PyQt6.QtWidgets import QApplication


# Ensure QApplication is initialized for QDialog subclasses in tests
@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


def test_migration_dialog_remaps_duplicate_team_ids(qapp, tmp_path):
    import sys

    for m in list(sys.modules.keys()):
        if m.startswith("PyQt6") or "migration_dialog" in m:
            sys.modules.pop(m, None)
    from Ankimon.pyobj.migration_dialog import MigrationDialog

    # 1. Create a corrupt/duplicate collection JSON
    dup_uuid = str(uuid.uuid4())
    mypokemon_data = [
        {"individual_id": dup_uuid, "name": "Pikachu", "level": 10, "species_id": 25},
        {
            "individual_id": dup_uuid,
            "name": "Charmander",
            "level": 15,
            "species_id": 4,
        },  # Duplicate individual_id!
    ]
    mypokemon_path = tmp_path / "mypokemon.json"
    mypokemon_path.write_text(json.dumps(mypokemon_data))

    # 2. Create team JSON referencing the duplicate ID
    team_data = [
        {"individual_id": dup_uuid, "name": "Charmander", "level": 15, "species_id": 4}
    ]
    team_path = tmp_path / "team.json"
    team_path.write_text(json.dumps(team_data))

    from Ankimon.pyobj.database_manager import AnkimonDB

    db = AnkimonDB(db_path=tmp_path / "ankimon.db")

    # Initialize the dialog
    dialog = MigrationDialog(
        db,
        mypokemon_path=mypokemon_path,
        mainpokemon_path=tmp_path / "mainpokemon.json",
        items_path=tmp_path / "items.json",
        badges_path=tmp_path / "badges.json",
        team_path=team_path,
        history_path=tmp_path / "history.json",
        data_path=tmp_path / "data.json",
        rate_path=tmp_path / "rate.json",
    )

    # Run migration step
    with patch("PyQt6.QtWidgets.QApplication.processEvents"):
        dialog._run_migration()

    assert dialog.migration_successful, dialog.log_area.toPlainText()
    saved_pokemon = db.get_all_pokemon()
    assert len(saved_pokemon) == 2
    ids_by_name = {p["name"]: p["individual_id"] for p in saved_pokemon}
    assert ids_by_name["Pikachu"] == dup_uuid
    assert ids_by_name["Charmander"] != dup_uuid
    assert db.get_team() == [{"individual_id": ids_by_name["Charmander"]}]
    db.close()
