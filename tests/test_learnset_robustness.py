import json
import importlib
import sys
import types
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Find the actual learnsets.json file in the user_files directory
_src = Path(__file__).parent.parent / "src"
actual_learnset_path = _src / "Ankimon" / "user_files" / "data_files" / "learnsets.json"

# Load the data once for the test
with open(actual_learnset_path, "r", encoding="utf-8") as file:
    LEARNSETS_DATA = json.load(file)

# Mock modules
mock_aqt = MagicMock()
sys.modules["aqt"] = mock_aqt
sys.modules["aqt.utils"] = mock_aqt.utils
sys.modules["Anki"] = MagicMock()
sys.modules["aqt.qt"] = MagicMock()


class MockResources:
    learnset_path = str(actual_learnset_path)

    def __getattr__(self, name):
        return "dummy"


sys.modules["Ankimon.resources"] = MockResources()

# Also mock singletons if needed
sys.modules["Ankimon.singletons"] = MagicMock()
sys.modules["Ankimon.utils"] = MagicMock()
mock_pyobj = MagicMock()
sys.modules["Ankimon.pyobj"] = mock_pyobj
sys.modules["Ankimon.pyobj.error_handler"] = mock_pyobj.error_handler
sys.modules["Ankimon.pyobj.QProgressIndicator"] = MagicMock()

# Now load pokedex_functions from its file
_spec = importlib.util.spec_from_file_location(
    "Ankimon.functions.pokedex_functions",
    _src / "Ankimon" / "functions" / "pokedex_functions.py",
)
_pf = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _pf
_spec.loader.exec_module(_pf)

from Ankimon.functions.pokedex_functions import get_all_pokemon_moves


def test_all_pokemon_learnsets_are_valid():
    """
    Test that retrieving learnsets for all Pokemon in learnsets.json
    does not throw any ValueError due to malformed level strings.
    """
    pokemon_names = list(LEARNSETS_DATA.keys())

    with patch.object(_pf.json, "load", return_value=LEARNSETS_DATA):
        with patch("builtins.open"):
            for pokemon_name in pokemon_names:
                try:
                    # Level 100 ensures we try to parse as many valid levels as possible
                    get_all_pokemon_moves(pokemon_name, 100)
                except ValueError as e:
                    pytest.fail(f"ValueError raised for pokemon {pokemon_name}: {e}")
                except Exception as e:
                    pytest.fail(f"Unexpected exception for pokemon {pokemon_name}: {e}")
