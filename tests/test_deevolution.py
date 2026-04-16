import pytest
import json
import uuid
import sys
from pathlib import Path
import importlib.util

import unittest.mock as mock
from unittest.mock import patch, MagicMock

_src = Path(__file__).parent.parent / "src"

def setup_mock_modules():
    mocks = {}
    modules_to_mock = [
        "aqt", "aqt.qt", "aqt.utils",
        "Ankimon.pyobj",
        "Ankimon.pyobj.settings",
        "Ankimon.pyobj.pokemon_obj",
        "Ankimon.pyobj.reviewer_obj",
        "Ankimon.pyobj.test_window",
        "Ankimon.pyobj.trainer_card",
        "Ankimon.pyobj.InfoLogger",
        "Ankimon.pyobj.evolution_window",
        "Ankimon.pyobj.attack_dialog",
        "Ankimon.pyobj.translator",
        "Ankimon.pyobj.error_handler",
        "Ankimon.functions.pokemon_functions",
        "Ankimon.functions.pokedex_functions",
        "Ankimon.functions.trainer_functions",
        "Ankimon.functions.badges_functions",
        "Ankimon.functions.drawing_utils",
        "Ankimon.functions.migration",
        "Ankimon.utils",
        "Ankimon.business",
        "Ankimon.const",
        "Ankimon.singletons",
        "Ankimon.resources",
        "Ankimon.gui_classes.pokemon_details",
        "Ankimon.gui_entities"
    ]
    for module in modules_to_mock:
        if module not in sys.modules:
            mocks[module] = mock.MagicMock()
            sys.modules[module] = mocks[module]

    class MockQDialog:
        pass

    class MockQtWidgets(mock.MagicMock):
        QDialog = MockQDialog
        QWidget = MockQDialog
        QMainWindow = MockQDialog

    mock_qtwidgets = MockQtWidgets()
    mock_qtwidgets.QDialog = MockQDialog
    mock_qtwidgets.QWidget = MockQDialog
    mock_qtwidgets.QMainWindow = MockQDialog

    class MockPackage(mock.MagicMock):
        __path__ = []

    sys.modules['PyQt6'] = MockPackage()
    sys.modules['PyQt6'].__spec__ = mock.MagicMock()
    sys.modules['PyQt6.QtWidgets'] = mock_qtwidgets
    sys.modules['PyQt6.QtCore'] = mock.MagicMock()
    sys.modules['PyQt6.QtGui'] = mock.MagicMock()
    sys.modules['PyQt6.QtMultimedia'] = mock.MagicMock()
    sys.modules['PyQt6.QtWebEngineCore'] = mock.MagicMock()
    sys.modules['PyQt6.QtWebEngineWidgets'] = mock.MagicMock()
    sys.modules['PyQt6.QtWebChannel'] = mock.MagicMock()
    sys.modules['PyQt6.QtNetwork'] = mock.MagicMock()

    import PyQt6
    import PyQt6.QtWidgets
    PyQt6.QtWidgets.QDialog = MockQDialog
    PyQt6.QtWidgets.QWidget = MockQDialog
    PyQt6.QtWidgets.QMainWindow = MockQDialog

    for module in [
        "requests",
        "Ankimon.poke_engine",
        "Ankimon.poke_engine.objects",
        "Ankimon.poke_engine.helpers"
    ]:
        if module not in sys.modules:
            mocks[module] = mock.MagicMock()
            sys.modules[module] = mocks[module]

    return mocks

def teardown_mock_modules(mocks):
    for module in mocks:
        if module in sys.modules:
            del sys.modules[module]

@pytest.fixture(autouse=True)
def patch_sys_modules():
    mocks = setup_mock_modules()
    yield
    teardown_mock_modules(mocks)

def load_pokemon_obj():
    spec_pokemon_obj = importlib.util.spec_from_file_location(
        "Ankimon.pyobj.pokemon_obj",
        _src / "Ankimon" / "pyobj" / "pokemon_obj.py",
    )
    pokemon_obj = importlib.util.module_from_spec(spec_pokemon_obj)
    sys.modules["Ankimon.pyobj.pokemon_obj"] = pokemon_obj
    spec_pokemon_obj.loader.exec_module(pokemon_obj)
    return pokemon_obj.PokemonObject


@pytest.fixture
def mock_paths(tmp_path):
    main_path = tmp_path / "mainpokemon.json"
    my_path = tmp_path / "mypokemon.json"

    with patch('Ankimon.pyobj.collection_dialog.mainpokemon_path', main_path), \
         patch('Ankimon.pyobj.collection_dialog.mypokemon_path', my_path), \
         patch('Ankimon.pyobj.pokemon_obj.mypokemon_path', my_path), \
         patch('Ankimon.pyobj.pokemon_obj.mainpokemon_path', main_path), \
         patch('Ankimon.resources.mypokemon_path', my_path), \
         patch('Ankimon.resources.mainpokemon_path', main_path):
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

        PokemonObject = load_pokemon_obj()
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

    # Load collection_dialog isolated FIRST before patching it
    spec_collection_dialog = importlib.util.spec_from_file_location(
        "Ankimon.pyobj.collection_dialog",
        _src / "Ankimon" / "pyobj" / "collection_dialog.py",
    )
    collection_dialog = importlib.util.module_from_spec(spec_collection_dialog)
    sys.modules["Ankimon.pyobj.collection_dialog"] = collection_dialog
    spec_collection_dialog.loader.exec_module(collection_dialog)

    # Patch external calls inside MainPokemon using patch.object to avoid JSON serialization issues with mock modules
    with patch.object(collection_dialog, 'search_pokedex_by_id', return_value="Bulbasaur"), \
         patch.object(collection_dialog, 'search_pokedex', return_value={"hp": 45, "atk": 49, "def": 49, "spa": 65, "spd": 65, "spe": 45}), \
         patch.object(collection_dialog.PokemonObject, 'calc_stat', return_value=20), \
         patch.object(collection_dialog.uuid, 'uuid4', return_value=uuid.uuid4()), \
         patch('Ankimon.functions.migration.migrate_starter_individual_id'), \
             patch('Ankimon.singletons.pokemon_pc'), \
             patch.object(collection_dialog, 'mainpokemon_path', main_path), \
             patch.object(collection_dialog, 'mypokemon_path', my_path):

        # Execute the function
        collection_dialog.MainPokemon(
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
