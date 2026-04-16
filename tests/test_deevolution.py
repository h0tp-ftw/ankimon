import pytest
import json
import uuid
import os
import sys

from unittest.mock import patch, MagicMock

# Create a mock for Anki/AQT to avoid complex imports
class MockPackage(MagicMock):
    __path__ = []

mock_aqt = MockPackage()
mock_aqt.__spec__ = MagicMock()
mock_anki = MockPackage()
mock_anki.__spec__ = MagicMock()
sys.modules['aqt'] = mock_aqt
sys.modules['aqt.operations'] = MagicMock()
sys.modules['aqt.operations.QueryOp'] = MagicMock()
sys.modules['aqt.reviewer'] = MagicMock()
sys.modules['aqt.main'] = MagicMock()
sys.modules['aqt.theme'] = MagicMock()
sys.modules['aqt.webview'] = MagicMock()
sys.modules['aqt.editor'] = MagicMock()
sys.modules['aqt.gui_hooks'] = MagicMock()
sys.modules['anki'] = mock_anki
sys.modules['anki.hooks'] = MagicMock()
sys.modules['anki.cards'] = MagicMock()
sys.modules['anki.collection'] = MagicMock()
sys.modules['anki.utils'] = MagicMock()
sys.modules['anki.decks'] = MagicMock()
sys.modules['anki.buildinfo'] = MagicMock()
sys.modules['anki.buildinfo.version'] = "24.04"
sys.modules['aqt.qt'] = MagicMock()
sys.modules['aqt.utils'] = MagicMock()
sys.modules['PyQt6'] = MockPackage()
sys.modules['PyQt6'].__spec__ = MagicMock()
sys.modules['PyQt6.QtCore'] = MagicMock()
class MockQDialog:
    pass

class MockQtWidgets(MagicMock):
    QDialog = MockQDialog
    QWidget = MockQDialog
    QMainWindow = MockQDialog

# To make 'from PyQt6.QtWidgets import QDialog' work
mock_qtwidgets = MockQtWidgets()
mock_qtwidgets.QDialog = MockQDialog
sys.modules['PyQt6.QtWidgets'] = mock_qtwidgets
sys.modules['PyQt6.QtGui'] = MagicMock()
sys.modules['PyQt6.QtMultimedia'] = MagicMock()
sys.modules['PyQt6.QtWebEngineCore'] = MagicMock()
sys.modules['PyQt6.QtWebEngineWidgets'] = MagicMock()
sys.modules['PyQt6.QtWebChannel'] = MagicMock()
sys.modules['PyQt6.QtNetwork'] = MagicMock()
sys.modules['markdown'] = MagicMock()
sys.modules['requests'] = MagicMock()

# Mock out complex singletons before they load
sys.modules['src.Ankimon.singletons'] = MagicMock()

# Now we can safely import Ankimon objects
import importlib.util

# Stop triggering complex __init__.py stuff from src/Ankimon/__init__.py
# We can bypass __init__.py by directly importing from the file.
import importlib.util

# Use normal import, but mock the __init__ correctly.
import sys
mock_pkg = MockPackage()
mock_pkg.__path__ = ['src/Ankimon']
mock_pkg.__spec__ = MagicMock()
sys.modules['src.Ankimon'] = mock_pkg

import src.Ankimon
from src.Ankimon.pyobj.pokemon_obj import PokemonObject


@pytest.fixture
def mock_paths(tmp_path):
    main_path = tmp_path / "mainpokemon.json"
    my_path = tmp_path / "mypokemon.json"

    with patch('src.Ankimon.pyobj.collection_dialog.mainpokemon_path', main_path), \
         patch('src.Ankimon.pyobj.collection_dialog.mypokemon_path', my_path), \
         patch('src.Ankimon.pyobj.pokemon_obj.mypokemon_path', my_path), \
         patch('src.Ankimon.pyobj.pokemon_obj.mainpokemon_path', main_path), \
         patch('src.Ankimon.resources.mypokemon_path', my_path), \
         patch('src.Ankimon.resources.mainpokemon_path', main_path):
        yield main_path, my_path

def test_pick_main_pokemon_does_not_deevolve(mock_paths):
    main_path, my_path = mock_paths

    # 1. Setup the scenario
    # An evolved Pokemon (Golduck) is in mypokemon.json
    # But the stale active data in mainpokemon.json (and in-memory main_pokemon) is still Psyduck.

    ind_id_1 = str(uuid.uuid4())
    ind_id_2 = str(uuid.uuid4())

    # mypokemon.json has the correct evolved data
    mypokemon_data = [
        {
            "id": 55,
            "name": "Golduck",
            "level": 34,
            "individual_id": ind_id_1,
            "attacks": ["Water Gun", "Confusion"],
            "base_stats": {"hp": 80, "atk": 82, "def": 78, "spa": 95, "spd": 80, "spe": 85},
            "xp": 1000,
            "ev": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
            "iv": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0}
        },
        {
            "id": 1,
            "name": "Bulbasaur",
            "level": 5,
            "individual_id": ind_id_2,
            "attacks": ["Tackle"],
            "base_stats": {"hp": 45, "atk": 49, "def": 49, "spa": 65, "spd": 65, "spe": 45},
            "ev": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
            "iv": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0}
        }
    ]

    with open(my_path, "w") as f:
        json.dump(mypokemon_data, f)

    # stale main_pokemon in memory (still Psyduck)
    stale_main_pokemon = PokemonObject(
        id=54,
        name="Psyduck",
        level=34,
        ability=["Damp"],
        type=["Water"],
        base_stats={"hp": 50, "atk": 52, "def": 48, "spa": 65, "spd": 50, "spe": 55},
        individual_id=ind_id_1,
        attacks=["Water Gun", "Confusion"],
        xp=1050,  # Note: XP has increased slightly since evolution
        hp=50,
        gender="M",
        shiny=False,
        tier="Normal",
        growth_rate="medium",
        captured_date="2024-01-01"
    )

    # We want to pick Bulbasaur as the new main Pokemon
    new_pokemon_data = mypokemon_data[1]

    # Mock the required dependencies for MainPokemon
    logger = MagicMock()
    translator = MagicMock()
    reviewer_obj = MagicMock()
    test_window = MagicMock()

    # Patch external calls inside MainPokemon
    with patch('src.Ankimon.pyobj.collection_dialog.search_pokedex_by_id', return_value="Bulbasaur"), \
         patch('src.Ankimon.pyobj.collection_dialog.search_pokedex', return_value={"hp": 45, "atk": 49, "def": 49, "spa": 65, "spd": 65, "spe": 45}), \
         patch('src.Ankimon.pyobj.collection_dialog.PokemonObject.calc_stat', return_value=20), \
         patch('src.Ankimon.pyobj.collection_dialog.uuid.uuid4', return_value=uuid.uuid4()), \
         patch('src.Ankimon.functions.migration.migrate_starter_individual_id'), \
         patch('src.Ankimon.singletons.pokemon_pc'):

        from src.Ankimon.pyobj.collection_dialog import MainPokemon

        # Execute the function
        MainPokemon(
            pokemon_data=new_pokemon_data,
            main_pokemon=stale_main_pokemon,
            logger=logger,
            translator=translator,
            reviewer_obj=reviewer_obj,
            test_window=test_window
        )

    # Verify the results
    with open(my_path, "r") as f:
        updated_mypokemon = json.load(f)

    # Find our first Pokemon (which was Golduck)
    first_pokemon = next((p for p in updated_mypokemon if p["individual_id"] == ind_id_1), None)

    assert first_pokemon is not None
    # Crucial assertion: The Pokemon should still be Golduck, NOT Psyduck
    assert first_pokemon["name"] == "Golduck"
    assert first_pokemon["id"] == 55
    # The progress (XP) should have been updated from the stale memory object
    assert first_pokemon["xp"] == 1050
