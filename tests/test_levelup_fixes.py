import sys
import types
from pathlib import Path
import unittest.mock as mock
import pytest

_src = Path(__file__).parent.parent / "src"

# Re-establish correct package stubs for all Ankimon packages to undo any path-less stubs from other tests during collection
for _pkg in (
    "Ankimon",
    "Ankimon.functions",
    "Ankimon.pyobj",
    "Ankimon.ankimon_items_web",
):
    _mod = types.ModuleType(_pkg)
    _mod.__path__ = [str(_src / _pkg.replace(".", "/"))]
    _mod.__package__ = _pkg
    sys.modules[_pkg] = _mod

# Link sub-packages to parent packages so attribute access works
if "Ankimon" in sys.modules:
    for attr in ("functions", "pyobj", "ankimon_items_web"):
        subpkg = f"Ankimon.{attr}"
        if subpkg in sys.modules:
            setattr(sys.modules["Ankimon"], attr, sys.modules[subpkg])

# Stub standard anki / aqt modules before imports if not already done
sys.modules["aqt"] = mock.MagicMock()
sys.modules["aqt.qt"] = mock.MagicMock()
sys.modules["aqt.utils"] = mock.MagicMock()

@pytest.fixture(autouse=True)
def setup_real_modules():
    # Save the old sys.modules state for only the mocked modules to prevent split-brain side effects in other tests
    keys_to_isolate = [
        "Ankimon.functions.pokemon_functions",
        "Ankimon.functions.trainer_functions"
    ]
    old_modules = {k: sys.modules.get(k) for k in keys_to_isolate}
    
    # Remove any MagicMock stubs during execution to force-import real modules
    for key in keys_to_isolate:
        sys.modules.pop(key, None)
        
    import importlib
    pkm_func = importlib.import_module("Ankimon.functions.pokemon_functions")
    find_experience_for_level = pkm_func.find_experience_for_level
    
    trainer_func = importlib.import_module("Ankimon.functions.trainer_functions")
    
    # Force override find_experience_for_level on all target modules (encounter_functions and mobile_sync are not popped)
    pkm_func.find_experience_for_level = find_experience_for_level
    trainer_func.find_experience_for_level = find_experience_for_level
    
    from Ankimon.functions import encounter_functions as ef
    ef.find_experience_for_level = find_experience_for_level
    
    from Ankimon.functions import mobile_sync as ms
    ms.find_experience_for_level = find_experience_for_level

    yield

    # Restore old sys.modules state so subsequent tests don't suffer from split-brain module references
    for key, old_val in old_modules.items():
        if old_val is not None:
            sys.modules[key] = old_val
        else:
            sys.modules.pop(key, None)

def test_erratic_curve_above_142():
    from Ankimon.functions.pokemon_functions import find_experience_for_level
    # Erratic curve shouldn't go negative or 0 for levels > 142
    for lvl in range(140, 250):
        xp_req = find_experience_for_level("erratic", lvl)
        assert not isinstance(xp_req, mock.MagicMock)
        assert xp_req > 0
        # Check erratic is at least 1 at any level
        assert xp_req >= 1

def test_save_main_pokemon_progress_caps_at_10_levelups():
    from Ankimon.functions.pokemon_functions import find_experience_for_level
    from Ankimon.functions.encounter_functions import save_main_pokemon_progress
    
    # Test that save_main_pokemon_progress caps level ups to 10 and recalculates cost dynamically
    # Mock settings
    settings = mock.MagicMock()
    settings.get = lambda k, d=None: {
        "misc.remove_level_cap": True,
        "gui.pop_up_dialog_message_on_defeat": False,
        "gui.hud_styling": False,
        "gui.reviewer_text_message_box": False,
        "gui.reviewer_text_message_box_time": 4,
    }.get(k, d)
    
    # Mock translator, logger, db, services
    from Ankimon.functions import encounter_functions as ef
    ef.settings_obj = settings
    ef.translator = mock.MagicMock()
    ef.translator.translate = mock.MagicMock(return_value="msg")
    ef.ankimon_db = mock.MagicMock()
    
    from Ankimon.services import services
    services.settings = settings
    services.db = mock.MagicMock()
    services.db.get_main_pokemon.return_value = {
        "name": "Feebas",
        "attacks": ["Tackle"],
        "ev": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
        "held_item": None,
        "pokemon_defeated": 0,
        "stats": {},
        "level": 52,
        "xp": 0,
        "friendship": 300,
    }
    services.logger = mock.MagicMock()
    
    # Let's mock checks and other functions that are not related to level up math
    ef.check_evolution_for_pokemon = mock.MagicMock(return_value=None)
    ef.check_friendship_evolution_for_pokemon = mock.MagicMock(return_value=None)
    ef.get_levelup_move_for_pokemon = mock.MagicMock(return_value=[])
    
    main = types.SimpleNamespace(
        name="Feebas",
        growth_rate="erratic",
        level=52,
        xp=0,
        individual_id="iid",
        id=349,
        everstone=False,
        friendship=300,
        stats={},
        ev={"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
        hp=100,
        held_item=None,
        pokemon_defeated=0,
        tier="Normal",
        is_favorite=False,
        evolution_rejected=False,
        invalidate_cp_cache=lambda: None,
    )
    enemy = types.SimpleNamespace(ev_yield={})
    
    # Give it massive XP (e.g. 1,000,000 XP) which would normally level it up more than 10 times
    # At level 52, erratic curve needs ~2000-3000 XP per level. 1M XP would jump to level 100+.
    # With the cap of 10, it should stop at level 62.
    result_lvl = save_main_pokemon_progress(
        main, enemy, 1000000, {}, mock.MagicMock(), None
    )
    
    assert main.level == 62
    # Verify that remaining XP is capped to next level's threshold - 1
    next_level_cost = find_experience_for_level("erratic", 62)
    assert main.xp == next_level_cost - 1

    gained_xp_call = next(
        call
        for call in ef.translator.translate.call_args_list
        if call.args and call.args[0] == "mainpokemon_gained_xp"
    )
    assert gained_xp_call.kwargs["experience_till_next_level"] == next_level_cost

def test_xp_share_gain_exp_caps_at_10_levelups():
    from Ankimon.functions.pokemon_functions import find_experience_for_level
    from Ankimon.functions.trainer_functions import xp_share_gain_exp
    
    # Test that xp_share_gain_exp caps level ups to 10
    settings = mock.MagicMock()
    settings.get = lambda k, d=None: {
        "misc.remove_level_cap": True,
        "trainer.xp_share": "some_id",
        "evolution.friendship_time_enabled": False,
    }.get(k, d)
    
    pokemon_data = {
        "individual_id": "some_id",
        "id": 349,
        "name": "Feebas",
        "growth_rate": "erratic",
        "level": 52,
        "xp": 0,
        "held_item": None,
    }
    
    from Ankimon.services import services
    services.settings = settings
    services.db = mock.MagicMock()
    services.db.get_pokemon.return_value = pokemon_data
    services.logger = mock.MagicMock()
    
    # We mock check_evolution_for_pokemon
    from Ankimon.functions import trainer_functions as tf
    orig_check = tf.check_evolution_for_pokemon
    tf.check_evolution_for_pokemon = mock.MagicMock(return_value=None)
    
    try:
        # Give massive XP (e.g. 2,000,000 XP, which is multiplied by 0.5 to 1,000,000 XP for XP share)
        xp_share_gain_exp(
            mock.MagicMock(), settings, None, "other_id", 2000000, "some_id"
        )
        
        assert pokemon_data["level"] == 62
        next_level_cost = find_experience_for_level("erratic", 62)
        assert pokemon_data["xp"] == next_level_cost - 1
    finally:
        tf.check_evolution_for_pokemon = orig_check

def test_mobile_sync_gain_exp_caps_at_10_levelups():
    from Ankimon.functions.pokemon_functions import find_experience_for_level
    from Ankimon.functions.mobile_sync import _attribute_xp_and_evs_to_companion
    
    # Test that _attribute_xp_and_evs_to_companion in mobile_sync caps level ups to 10
    settings = mock.MagicMock()
    settings.get = lambda k, d=None: {
        "misc.remove_level_cap": True,
        "gui.pop_up_dialog_message_on_defeat": False,
    }.get(k, d)
    
    pkmndata = {
        "individual_id": "some_id",
        "id": 349,
        "name": "Feebas",
        "growth_rate": "erratic",
        "level": 52,
        "xp": 0,
        "attacks": ["Tackle"],
        "ev": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
        "iv": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
        "base_stats": {"hp": 20, "atk": 15, "def": 20, "spa": 10, "spd": 55, "spe": 80},
        "nature": "serious",
    }
    
    from Ankimon.services import services
    services.settings = settings
    services.db = mock.MagicMock()
    services.db.get_pokemon.return_value = pkmndata
    services.logger = mock.MagicMock()
    
    from Ankimon.functions import pokemon_functions as pf
    orig_move = getattr(pf, "get_levelup_move_for_pokemon", None)
    pf.get_levelup_move_for_pokemon = mock.MagicMock(return_value=[])
    
    try:
        _attribute_xp_and_evs_to_companion("some_id", 1000000, {}, settings_obj=settings, battles_fought=0)
        assert pkmndata["level"] == 62
        next_level_cost = find_experience_for_level("erratic", 62)
        assert pkmndata["xp"] == next_level_cost - 1
    finally:
        if orig_move is not None:
            pf.get_levelup_move_for_pokemon = orig_move
        else:
            delattr(pf, "get_levelup_move_for_pokemon")
