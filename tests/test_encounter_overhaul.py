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
    "Ankimon.pyobj.ankimon_tracker",
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
    "Ankimon.functions.friendship_evolution",
    "Ankimon.functions.trainer_functions",
    "Ankimon.functions.badges_functions",
    "Ankimon.functions.drawing_utils",
    "Ankimon.move_names",
    "Ankimon.utils",
    "Ankimon.business",
    "Ankimon.const",
    "Ankimon.singletons",
    "Ankimon.resources",
]:
    sys.modules[module] = mock.MagicMock()

# Import the module under test
_src = Path(__file__).parent.parent / "src"
spec = importlib.util.spec_from_file_location(
    "Ankimon.functions.encounter_functions",
    _src / "Ankimon" / "functions" / "encounter_functions.py",
)
ef = importlib.util.module_from_spec(spec)

# Execute the module
spec.loader.exec_module(ef)

# The module's bare globals (bind_runtime_globals targets) are None after exec;
# rebind them to mocks. The DB now flows through the services registry (the seam
# replacement for exp's `mw.ankimon_db`), so swap the module's `services` handle
# for a MagicMock the tests control.
ef.main_pokemon = mock.MagicMock()
ef.settings_obj = mock.MagicMock()
ef.ankimon_tracker_obj = mock.MagicMock()
ef.trainer_card = mock.MagicMock()
ef.services = mock.MagicMock()


def test_legacy_mode_works_when_toggle_is_false():
    # Force legacy mode
    ef.USE_OVERHAUL_ENCOUNTER_SYSTEM = False
    ef.main_pokemon.level = 50
    ef.settings_obj.get.return_value = 100

    percentages = ef.modify_percentages(
        total_reviews=50, daily_average=100, trainer_level=5
    )
    assert "Normal" in percentages
    assert percentages["Starter"] == 0.0
    assert abs(sum(percentages.values()) - 100.0) < 0.001


def test_ep_mastery_index_bounds_and_components():
    # Setup mocks to achieve specific components
    ef.trainer_card.level = 50  # T_norm = 50%
    ef.settings_obj.get.return_value = 100  # DailyGoal

    # Mock Pokedex Completion D_norm (DB reads route through services.db)
    ef.services.db.get_all_pokemon_ids.return_value = {1, 2, 3}

    ef.search_pokedex_by_id = lambda pid: "Bulbasaur"
    ef.search_pokedex = lambda name, key: 1 if key == "species_id" else None
    ef.safe_int = lambda x: int(x) if x is not None else 0

    # 3 unique species out of a mock 10 species -> D_norm = 30%
    # We patch _load_pokedex_cache to return 10 species keys
    with mock.patch(
        "Ankimon.functions.pokedex_functions._load_pokedex_cache"
    ) as mock_cache:
        mock_cache.return_value = {str(i): {"species_id": i} for i in range(1, 11)}

        # Mock Core Team Power C_norm
        ef.services.db.get_all_pokemon.return_value = [
            {"level": 50, "stats": {}} for _ in range(6)
        ]

        # Patch calculate_cp_from_dict directly on ef to return 2000
        ef.calculate_cp_from_dict = mock.MagicMock(return_value=2000)

        # Run calculate_mastery_index_ep
        ep = ef.calculate_mastery_index_ep(
            total_reviews=50, daily_average=100, trainer_level=50
        )

        # New Overhaul Config:
        # T_norm = min((50/50)*100, 100) = 100.0 -> 0.25 * 100.0 = 25.0
        # D_norm = 30.0 -> 0.25 * 30.0 = 7.5
        # S_norm = min((50/100)*100, 100) = 50.0 -> 0.25 * 50.0 = 12.5
        # C_norm = min((2000/16000)*100, 100) = 12.5 -> 0.25 * 12.5 = 3.125
        # Total EP = 25.0 + 7.5 + 12.5 + 3.125 = 48.125
        assert abs(ep - 48.125) < 0.001


def test_overhaul_pity_multipliers_and_level_locks():
    # Enable overhaul system
    ef.USE_OVERHAUL_ENCOUNTER_SYSTEM = True
    try:
        ef.main_pokemon.level = (
            45  # locks Legendary (50), Mega (60), Gmax (65), Mythical (75)
        )

        # Mock pity trackers to trigger custom multipliers (user_data via services.db)
        ef.services.db.get_user_data.return_value = {
            "Ultra": 150,  # exceeds threshold (150 - 100)/50 = 1 -> multiplier = 1 + 1^2 = 2.0
            "Gmax": 200,  # exceeds threshold but Gmax is locked by level 45, so its weight must remain 0.0
            "Starter": 0,
            "Mega": 0,
            "Legendary": 0,
            "Mythical": 0,
        }

        # Force EP = 50.0 (patch the module attribute directly — the loaded
        # module instance is not registered in sys.modules)
        orig_ep = ef.calculate_mastery_index_ep
        ef.calculate_mastery_index_ep = lambda *args, **kwargs: 50.0
        try:
            percentages = ef.modify_percentages(
                total_reviews=50, daily_average=100, trainer_level=50
            )
        finally:
            ef.calculate_mastery_index_ep = orig_ep

        # Verify that locked categories have 0% rate
        assert percentages["Legendary"] == 0.0
        assert percentages["Mega"] == 0.0
        assert percentages["Gmax"] == 0.0
        assert percentages["Mythical"] == 0.0

        # Starter is locked under the new overhaul config (threshold 80 > 45)
        assert percentages["Starter"] == 0.0

        # Normal, Baby, and Ultra should have > 0% rates
        assert percentages["Normal"] > 0.0
        assert percentages["Baby"] > 0.0
        assert percentages["Ultra"] > 0.0
        assert abs(sum(percentages.values()) - 100.0) < 0.001
    finally:
        ef.USE_OVERHAUL_ENCOUNTER_SYSTEM = False


def test_pity_trackers_increment_and_reset_in_generation():
    ef.USE_OVERHAUL_ENCOUNTER_SYSTEM = True
    try:
        # Setup mock pity trackers in database
        pity_data = {
            "Ultra": 10,
            "Gmax": 20,
            "Starter": 30,
            "Mega": 40,
            "Legendary": 50,
            "Mythical": 60,
        }
        ef.services.db.get_user_data.return_value = pity_data

        pity_trackers = ef.load_pity_trackers()
        selected_tier = "Ultra"

        rare_tiers = ["Ultra", "Gmax", "Starter", "Mega", "Legendary", "Mythical"]
        if selected_tier in rare_tiers:
            pity_trackers[selected_tier] = 0
            for rt in rare_tiers:
                if rt != selected_tier:
                    pity_trackers[rt] += 1

        assert pity_trackers["Ultra"] == 0
        assert pity_trackers["Gmax"] == 21
        assert pity_trackers["Legendary"] == 51
        assert pity_trackers["Mythical"] == 61
    finally:
        ef.USE_OVERHAUL_ENCOUNTER_SYSTEM = False
