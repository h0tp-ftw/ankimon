import json
import uuid
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
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
        {"individual_id": dup_uuid, "name": "Charmander", "level": 15, "species_id": 4},  # Duplicate individual_id!
    ]
    mypokemon_path = tmp_path / "mypokemon.json"
    mypokemon_path.write_text(json.dumps(mypokemon_data))

    # 2. Create team JSON referencing the duplicate ID
    team_data = [
        {"individual_id": dup_uuid, "name": "Charmander", "level": 15, "species_id": 4}
    ]
    team_path = tmp_path / "team.json"
    team_path.write_text(json.dumps(team_data))

    # Mock database
    mock_db = MagicMock()
    mock_db.is_migrated_phase1.return_value = False
    mock_db.save_pokemon.return_value = True
    
    # When save_team is called, we capture the list saved to the DB
    saved_team = []
    def mock_save_team(team_list):
        saved_team.extend(team_list)
        return True
    mock_db.save_team.side_effect = mock_save_team

    # We mock get_all_pokemon to return what was supposedly saved in phase 1
    # since Step 5 does: all_captured = self.db.get_all_pokemon()
    saved_pokemon = []
    def mock_save_pokemon(pokemon):
        saved_pokemon.append(pokemon)
        return True
    mock_db.save_pokemon.side_effect = mock_save_pokemon
    mock_db.get_all_pokemon.side_effect = lambda: saved_pokemon

    # Initialize the dialog
    dialog = MigrationDialog(
        mock_db,
        mypokemon_path=mypokemon_path,
        mainpokemon_path=tmp_path / "mainpokemon.json",
        items_path=tmp_path / "items.json",
        badges_path=tmp_path / "badges.json",
        team_path=team_path,
        history_path=tmp_path / "history.json",
        data_path=tmp_path / "data.json",
        rate_path=tmp_path / "rate.json"
    )

    # Run migration step
    with patch("PyQt6.QtWidgets.QApplication.processEvents"):
        dialog._run_migration()

    # Verify that the two migrated pokemon have distinct IDs
    if len(saved_pokemon) != 2:
        print("MIGRATION LOGS:")
        print(dialog.log_area.toPlainText())
    assert len(saved_pokemon) == 2
    id1 = saved_pokemon[0]["individual_id"]
    id2 = saved_pokemon[1]["individual_id"]
    assert id1 != id2
    assert id1 == dup_uuid or id2 == dup_uuid
    new_uuid = id1 if id2 == dup_uuid else id2

    # Verify that the team member's ID was updated to the new UUID
    assert len(saved_team) == 1
    assert saved_team[0]["individual_id"] == new_uuid
