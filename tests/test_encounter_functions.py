import sys
import unittest.mock as mock
from pathlib import Path
import importlib.util

# Mock necessary modules
sys.modules["aqt"] = mock.MagicMock()
sys.modules["aqt.qt"] = mock.MagicMock()
sys.modules["aqt.utils"] = mock.MagicMock()

# Mock internal dependencies of encounter_functions
for module in [
    "Ankimon.pyobj.ankimon_tracker", "Ankimon.pyobj.pokemon_obj", 
    "Ankimon.pyobj.reviewer_obj", "Ankimon.pyobj.test_window", 
    "Ankimon.pyobj.trainer_card", "Ankimon.pyobj.InfoLogger", 
    "Ankimon.pyobj.evolution_window", "Ankimon.pyobj.attack_dialog",
    "Ankimon.pyobj.translator", "Ankimon.pyobj.error_handler",
    "Ankimon.functions.pokemon_functions", "Ankimon.functions.pokedex_functions",
    "Ankimon.functions.trainer_functions", "Ankimon.functions.badges_functions",
    "Ankimon.functions.drawing_utils", "Ankimon.utils", "Ankimon.business", 
    "Ankimon.const", "Ankimon.singletons", "Ankimon.resources"
]:
    sys.modules[module] = mock.MagicMock()

# Import the module under test
_src = Path(__file__).parent.parent / "src"
spec = importlib.util.spec_from_file_location(
    "Ankimon.functions.encounter_functions",
    _src / "Ankimon" / "functions" / "encounter_functions.py",
)
ef = importlib.util.module_from_spec(spec)
# Pre-patch singletons used in the module
ef.main_pokemon = mock.MagicMock()
ef.settings_obj = mock.MagicMock()
ef.ankimon_tracker_obj = mock.MagicMock()
ef.trainer_card = mock.MagicMock()

# Execute the module
spec.loader.exec_module(ef)

# exec_module runs `main_pokemon = None` / `settings_obj = None` at module top (the
# bind_runtime_globals targets), which OVERWRITES the pre-patch above. modify_percentages
# / get_tier read those bare globals, so (re)set the mocks AFTER exec.
ef.main_pokemon = mock.MagicMock()
ef.settings_obj = mock.MagicMock()
ef.ankimon_tracker_obj = mock.MagicMock()
ef.trainer_card = mock.MagicMock()

def test_modify_percentages_does_not_raise_nameerror():
    # Setup mocks
    ef.main_pokemon.level = 50
    
    # This should NOT raise NameError if fixed
    try:
        res = ef.modify_percentages(total_reviews=100, daily_average=50, trainer_level=20)
        assert isinstance(res, dict)
        assert sum(res.values()) > 99.9  # Normalized to 100
    except NameError as e:
        import pytest
        pytest.fail(f"NameError raised: {e}")

def test_get_tier_calls_modify_percentages_correctly():
    # Setup mocks
    ef.settings_obj.get.return_value = 100  # daily_average
    
    # This should NOT raise NameError if fixed
    try:
        tier = ef.get_tier(total_reviews=150, trainer_level=25)
        assert isinstance(tier, str)
    except NameError as e:
        import pytest
        pytest.fail(f"NameError raised in get_tier: {e}")
