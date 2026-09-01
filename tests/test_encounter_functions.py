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
    "Ankimon.functions.trainer_functions",
    "Ankimon.functions.badges_functions",
    "Ankimon.functions.drawing_utils",
    "Ankimon.utils",
    "Ankimon.business",
    "Ankimon.const",
    "Ankimon.singletons",
    "Ankimon.resources",
]:
    sys.modules[module] = mock.MagicMock()

# Import the module under test
_src = Path(__file__).parent.parent / "src"


def force_load_module(name, filepath):
    spec = importlib.util.spec_from_file_location(name, filepath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Force load encounter_data and pokedex_functions so they are not MagicMocks
force_load_module(
    "Ankimon.functions.encounter_data",
    _src / "Ankimon" / "functions" / "encounter_data.py",
)
pdx = force_load_module(
    "Ankimon.functions.pokedex_functions",
    _src / "Ankimon" / "functions" / "pokedex_functions.py",
)
pdx.pokedex_path = _src / "Ankimon" / "data_files" / "pokedex.json"

spec = importlib.util.spec_from_file_location(
    "Ankimon.functions.encounter_functions",
    _src / "Ankimon" / "functions" / "encounter_functions.py",
)
ef = importlib.util.module_from_spec(spec)

# Execute the module
spec.loader.exec_module(ef)

# exec_module runs `main_pokemon = None` / `settings_obj = None` at module top (the
# bind_runtime_globals targets), which would overwrite any pre-patch. modify_percentages
# / get_tier / handle_enemy_faint read those bare globals, so set the mocks AFTER exec.
ef.main_pokemon = mock.MagicMock()
ef.settings_obj = mock.MagicMock()
ef.ankimon_tracker_obj = mock.MagicMock()
ef.trainer_card = mock.MagicMock()


def test_modify_percentages_does_not_raise_nameerror():
    # Setup mocks
    ef.main_pokemon.level = 50

    # This should NOT raise NameError if fixed
    try:
        res = ef.modify_percentages(
            total_reviews=100, daily_average=50, trainer_level=20
        )
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


def test_modify_percentages_low_reviews_keyerror():
    # Setup mocks
    ef.main_pokemon.level = 50
    ef.settings_obj.get.return_value = 100  # daily_average

    # This should NOT raise KeyError if fixed (total_reviews=10, daily_average=100 -> ratio=0.1 < 0.4, trainer_level=20 > 10)
    try:
        res = ef.modify_percentages(
            total_reviews=10, daily_average=100, trainer_level=20
        )
        assert isinstance(res, dict)
        assert sum(res.values()) > 99.9  # Normalized to 100
        # Ensure only active tiers are present/active
        assert res.get("Normal") > 99.9
        assert res.get("Legendary", 0) == 0
    except KeyError as e:
        import pytest

        pytest.fail(f"KeyError raised: {e}")


def test_handle_enemy_faint_auto_catch_regional_enabled():
    # Save original globals
    orig_settings = ef.settings_obj
    orig_data = ef.encounter_data
    orig_tracker = ef.ankimon_tracker_obj
    orig_catch = ef.catch_pokemon
    orig_new = ef.new_pokemon
    orig_kill = ef.kill_pokemon

    try:
        # Create mock objects
        mock_settings = mock.MagicMock()
        mock_data = mock.MagicMock()
        mock_tracker = mock.MagicMock()
        mock_catch = mock.MagicMock()
        mock_new = mock.MagicMock()
        mock_kill = mock.MagicMock()

        # Setup settings mock
        def mock_get(key, default=None):
            if key == "battle.automatic_battle":
                return 3
            if key == "battle.auto_catch_regional":
                return True
            if key.startswith("battle.auto_catch_"):
                return False
            return default

        mock_settings.get = mock_get

        # Setup tracker mock
        mock_tracker.faint_processed = False

        # Setup encounter_data mocks
        mock_data.MEGA = []
        mock_data.GMAX = []
        mock_data.REGIONAL_FORM_REGION = {10091: "alola"}

        # Assign mock objects to ef
        ef.settings_obj = mock_settings
        ef.encounter_data = mock_data
        ef.ankimon_tracker_obj = mock_tracker
        ef.catch_pokemon = mock_catch
        ef.new_pokemon = mock_new
        ef.kill_pokemon = mock_kill

        # Setup main / enemy mock pokemon
        main_pokemon = mock.MagicMock()
        enemy_pokemon = mock.MagicMock()
        enemy_pokemon.id = 10091
        enemy_pokemon.tier = "Normal"
        enemy_pokemon.name = "Rattata-Alola"
        enemy_pokemon.shiny = False

        collected_pokemon_ids = {10091}
        test_window = mock.MagicMock()
        evo_window = mock.MagicMock()
        reviewer_obj = mock.MagicMock()
        logger = mock.MagicMock()
        achievements = {}

        # Execute
        result = ef.handle_enemy_faint(
            main_pokemon,
            enemy_pokemon,
            collected_pokemon_ids,
            test_window,
            evo_window,
            reviewer_obj,
            logger,
            achievements,
        )

        # Verify
        mock_catch.assert_called_once()
        mock_new.assert_called_once()
        mock_kill.assert_not_called()
        assert mock_tracker.faint_processed is True
        # battle_loop.py skips its end-of-turn display_battle() based on this
        # return value — a fresh encounter was painted here via new_pokemon(),
        # so it must report True.
        assert result is True

    finally:
        # Restore original globals
        ef.settings_obj = orig_settings
        ef.encounter_data = orig_data
        ef.ankimon_tracker_obj = orig_tracker
        ef.catch_pokemon = orig_catch
        ef.new_pokemon = orig_new
        ef.kill_pokemon = orig_kill


def test_handle_enemy_faint_auto_catch_regional_disabled():
    # Save original globals
    orig_settings = ef.settings_obj
    orig_data = ef.encounter_data
    orig_tracker = ef.ankimon_tracker_obj
    orig_catch = ef.catch_pokemon
    orig_new = ef.new_pokemon
    orig_kill = ef.kill_pokemon

    try:
        # Create mock objects
        mock_settings = mock.MagicMock()
        mock_data = mock.MagicMock()
        mock_tracker = mock.MagicMock()
        mock_catch = mock.MagicMock()
        mock_new = mock.MagicMock()
        mock_kill = mock.MagicMock()

        # Setup settings mock
        def mock_get(key, default=None):
            if key == "battle.automatic_battle":
                return 3
            if key == "battle.auto_catch_regional":
                return False
            if key.startswith("battle.auto_catch_"):
                return False
            return default

        mock_settings.get = mock_get

        # Setup tracker mock
        mock_tracker.faint_processed = False

        # Setup encounter_data mocks
        mock_data.MEGA = []
        mock_data.GMAX = []
        mock_data.REGIONAL_FORM_REGION = {10091: "alola"}

        # Assign mock objects to ef
        ef.settings_obj = mock_settings
        ef.encounter_data = mock_data
        ef.ankimon_tracker_obj = mock_tracker
        ef.catch_pokemon = mock_catch
        ef.new_pokemon = mock_new
        ef.kill_pokemon = mock_kill

        # Setup main / enemy mock pokemon
        main_pokemon = mock.MagicMock()
        enemy_pokemon = mock.MagicMock()
        enemy_pokemon.id = 10091
        enemy_pokemon.tier = "Normal"
        enemy_pokemon.name = "Rattata-Alola"
        enemy_pokemon.shiny = False

        collected_pokemon_ids = {10091}
        test_window = mock.MagicMock()
        evo_window = mock.MagicMock()
        reviewer_obj = mock.MagicMock()
        logger = mock.MagicMock()
        achievements = {}

        # Execute
        ef.handle_enemy_faint(
            main_pokemon,
            enemy_pokemon,
            collected_pokemon_ids,
            test_window,
            evo_window,
            reviewer_obj,
            logger,
            achievements,
        )

        # Verify
        mock_kill.assert_called_once()
        mock_catch.assert_not_called()
        mock_new.assert_called_once()
        assert mock_tracker.faint_processed is True

    finally:
        # Restore original globals
        ef.settings_obj = orig_settings
        ef.encounter_data = orig_data
        ef.ankimon_tracker_obj = orig_tracker
        ef.catch_pokemon = orig_catch
        ef.new_pokemon = orig_new
        ef.kill_pokemon = orig_kill


def test_auto_battle_override_toggle_returns_none_when_cleared():
    """Toggling an active override off is a valid nullable state transition."""
    original_override = ef.get_auto_battle_override()
    try:
        ef.clear_auto_battle_override()

        assert ef.toggle_auto_battle_override("catch") == "catch"
        assert ef.get_auto_battle_override() == "catch"
        assert ef.toggle_auto_battle_override("catch") is None
        assert ef.get_auto_battle_override() is None
    finally:
        ef._auto_battle_override = original_override


def test_handle_enemy_faint_manual_mode_clears_stale_override():
    """Manual mode must not execute an override selected before auto-battle was off."""
    original_settings = ef.settings_obj
    original_tracker = ef.ankimon_tracker_obj
    original_catch = ef.catch_pokemon
    original_new = ef.new_pokemon
    original_kill = ef.kill_pokemon
    original_override = ef.get_auto_battle_override()

    try:
        settings = mock.MagicMock()
        settings.get = lambda key, default=None: (
            0 if key == "battle.automatic_battle" else default
        )
        tracker = mock.MagicMock()
        tracker.faint_processed = False
        catch = mock.MagicMock()
        new = mock.MagicMock()
        kill = mock.MagicMock()
        main_pokemon = mock.MagicMock()
        enemy_pokemon = mock.MagicMock(
            id=25, name="Pikachu", shiny=False, tier="Normal"
        )
        test_window = mock.MagicMock()

        ef.settings_obj = settings
        ef.ankimon_tracker_obj = tracker
        ef.catch_pokemon = catch
        ef.new_pokemon = new
        ef.kill_pokemon = kill
        ef._auto_battle_override = "catch"

        result = ef.handle_enemy_faint(
            main_pokemon,
            enemy_pokemon,
            set(),
            test_window,
            mock.MagicMock(),
            mock.MagicMock(),
            mock.MagicMock(),
            {},
        )

        test_window.display_pokemon_death.assert_called_once()
        catch.assert_not_called()
        kill.assert_not_called()
        new.assert_not_called()
        assert ef.get_auto_battle_override() is None
        # Manual mode shows the death/catch screen, not a fresh encounter —
        # battle_loop.py's enemy_pokemon.hp > 0 check already excludes this
        # case from the end-of-turn repaint, but the return value itself must
        # still honestly report "did not replace the encounter".
        assert result is False
    finally:
        ef.settings_obj = original_settings
        ef.ankimon_tracker_obj = original_tracker
        ef.catch_pokemon = original_catch
        ef.new_pokemon = original_new
        ef.kill_pokemon = original_kill
        ef._auto_battle_override = original_override


def test_meets_prerequisites_fusion_and_normal():
    # Ensure the real _player_owns_base_form logic is used (in case simulation tests mutated it)
    def real_owns_base_form(actual_id, collected_ids):
        name = ef.search_pokedex_by_id(actual_id)
        if not name or name == "Pokémon not found":
            return True
        species_id = ef.safe_int(ef.search_pokedex(name, "species_id"))
        if not species_id:
            return True
        return species_id in collected_ids

    ef._player_owns_base_form = real_owns_base_form

    # 1. Test normal pokemon prerequisite (e.g. Mewtwo (150) needs Mew (151))
    assert ef._meets_prerequisites(150, {151}) is True
    assert ef._meets_prerequisites(150, set()) is False

    # 2. Test fusion forms (specific actual_id prerequisite, e.g. Necrozma Dusk Mane (10155) needs Necrozma (800) and Solgaleo (791))
    # It should not require Lunala (792) even though base Necrozma (800) requires Solgaleo and Lunala.
    assert ef._meets_prerequisites(10155, {800, 791}) is True
    assert ef._meets_prerequisites(10155, {800}) is False
    assert ef._meets_prerequisites(10155, {791}) is False

    # 3. Test fallback for forms not explicitly in PREREQUISITES (e.g. Aerodactyl Mega (10038) has base species Aerodactyl (142))
    # Aerodactyl has no prerequisites, so Aerodactyl Mega should meet prerequisites unconditionally.
    assert ef._meets_prerequisites(10038, set()) is True

    # 4. Test stat-redistribution forms requiring their base forms
    # Dialga Origin (10245) requires Dialga (483)
    assert ef._meets_prerequisites(10245, {483}) is True
    assert ef._meets_prerequisites(10245, set()) is False

    # Meloetta Pirouette (10018) requires Meloetta (648)
    assert ef._meets_prerequisites(10018, {648}) is True
    assert ef._meets_prerequisites(10018, set()) is False

    # 5. Test ("OR", {...}) prerequisites: any single member suffices
    # Terapagos (1024) requires Koraidon (1007) OR Miraidon (1008)
    assert ef._meets_prerequisites(1024, {1007}) is True
    assert ef._meets_prerequisites(1024, {1008}) is True
    assert ef._meets_prerequisites(1024, {1007, 1008}) is True
    assert ef._meets_prerequisites(1024, set()) is False


def test_save_main_pokemon_progress_persists_when_evo_window_none():
    """A friendship-evo-ready main Pokemon defeated while the evo window is
    dead/None (F31 lazy singletons) must still persist its level/xp/EV/friendship.

    check_friendship_evolution_for_pokemon calls ``evo_window.ask_pokemon_evo(...)``
    unguarded; without the call-site guard, the resulting AttributeError aborts
    save_main_pokemon_progress before its final ankimon_db.save_main_pokemon(...),
    silently discarding the defeat's progress. The guard must skip the offer (not
    invoke the checker) when the window is None so the save always runs.
    """
    import types

    names = (
        "settings_obj",
        "translator",
        "services",
        "ankimon_db",
        "find_experience_for_level",
        "limit_ev_yield",
        "check_friendship_evolution_for_pokemon",
        "check_evolution_for_pokemon",
    )
    orig = {n: getattr(ef, n) for n in names}
    try:
        main_data = {
            "name": "Pikachu",
            "attacks": ["Tackle"],
            "ev": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
            "held_item": None,
            "pokemon_defeated": 0,
            "stats": {},
            "level": 50,
            "xp": 0,
            "friendship": 300,
        }

        settings = mock.MagicMock()
        settings.get = lambda k, d=None: {
            "misc.remove_level_cap": False,
            "gui.pop_up_dialog_message_on_defeat": False,
        }.get(k, d)
        ef.settings_obj = settings

        ef.translator = mock.MagicMock()
        ef.translator.translate = lambda *a, **k: "msg"

        ef.services = mock.MagicMock()
        ef.services.db.get_main_pokemon.return_value = main_data
        ef.ankimon_db = mock.MagicMock()

        # Never level up (keeps the friendship path the only evolution branch).
        ef.find_experience_for_level = lambda *a, **k: 10**9
        ef.limit_ev_yield = lambda have, add: {
            "hp": 0,
            "attack": 0,
            "defense": 0,
            "special-attack": 0,
            "special-defense": 0,
            "speed": 0,
        }
        # Simulate the real UNGUARDED crash: reaching this with a None window
        # raises exactly as evo_window.ask_pokemon_evo(...) would.
        ef.check_friendship_evolution_for_pokemon = mock.MagicMock(
            side_effect=AttributeError(
                "'NoneType' object has no attribute 'ask_pokemon_evo'"
            )
        )
        ef.check_evolution_for_pokemon = mock.MagicMock(return_value=None)

        main = types.SimpleNamespace(
            name="Pikachu",
            growth_rate="medium",
            level=50,
            xp=0,
            individual_id="iid",
            id=25,
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

        # evo_window=None must NOT abort the save.
        result = ef.save_main_pokemon_progress(
            main, enemy, 5, {}, mock.MagicMock(), None
        )

        ef.ankimon_db.save_main_pokemon.assert_called_once()  # persistence intact
        ef.check_friendship_evolution_for_pokemon.assert_not_called()  # offer skipped
        assert result == 50
    finally:
        for n, v in orig.items():
            setattr(ef, n, v)


def test_tier_fallback_degrades_straight_to_normal(monkeypatch):
    """A rolled rare tier that empties after its guards must fall back to the
    common Normal tier, NOT cascade sideways into the next rare tiers (which would
    over-represent rare/legendary encounters). We assert the exact tier-query order:
    the rolled tier, then Normal — never Legendary/Gmax/Ultra in between."""
    queried = []

    class _Stop(Exception):
        pass

    def fake_pool(tier):
        queried.append(tier)
        if tier == "Normal":
            # Stop before the heavy Rattata-fallback tuple build; we only care
            # about which tiers were consulted, and in what order.
            raise _Stop()
        return []  # the rolled rare tier is empty -> triggers the fallback

    monkeypatch.setattr(ef, "get_all_pokemon_in_tier", fake_pool)
    monkeypatch.setattr(ef, "get_tier", lambda *a, **k: "Mega")

    try:
        ef.generate_random_pokemon(50, mock.MagicMock(), collected_ids=set())
    except _Stop:
        pass

    assert queried == ["Mega", "Normal"]
    assert "Legendary" not in queried  # no sideways cascade into other rare tiers


def _run_victory_with_stored_row(stored_individual_id, main_individual_id):
    """Drive one no-level-up victory and report what the checkers were handed.

    Returns the ``attacks=`` kwarg ``check_friendship_evolution_for_pokemon``
    received. ``None`` means the seed was declined and the checker falls back to
    its own ``get_pokemon(individual_id)`` lookup.
    """
    import types

    names = (
        "settings_obj",
        "translator",
        "services",
        "ankimon_db",
        "find_experience_for_level",
        "limit_ev_yield",
        "check_friendship_evolution_for_pokemon",
        "check_evolution_for_pokemon",
    )
    orig = {n: getattr(ef, n) for n in names}
    try:
        main_data = {
            "individual_id": stored_individual_id,
            "name": "Pikachu",
            "attacks": ["Tackle", "Charm"],
            "ev": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
            "held_item": None,
            "pokemon_defeated": 0,
            "stats": {},
            "level": 50,
            "xp": 0,
            "friendship": 300,
        }
        settings = mock.MagicMock()
        settings.get = lambda k, d=None: {
            "misc.remove_level_cap": False,
            "gui.pop_up_dialog_message_on_defeat": False,
        }.get(k, d)
        ef.settings_obj = settings
        ef.translator = mock.MagicMock()
        ef.translator.translate = lambda *a, **k: "msg"
        ef.services = mock.MagicMock()
        ef.services.db.get_main_pokemon.return_value = main_data
        ef.ankimon_db = mock.MagicMock()
        # Never level up: this is the no-level-up victory the seed exists for.
        ef.find_experience_for_level = lambda *a, **k: 10**9
        ef.limit_ev_yield = lambda have, add: {
            "hp": 0,
            "attack": 0,
            "defense": 0,
            "special-attack": 0,
            "special-defense": 0,
            "speed": 0,
        }
        ef.check_friendship_evolution_for_pokemon = mock.MagicMock(return_value=None)
        ef.check_evolution_for_pokemon = mock.MagicMock(return_value=None)

        main = types.SimpleNamespace(
            name="Pikachu",
            growth_rate="medium",
            level=50,
            xp=0,
            individual_id=main_individual_id,
            id=25,
            everstone=False,
            friendship=300,
            gender="F",
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

        ef.save_main_pokemon_progress(
            main, enemy, 5, {}, mock.MagicMock(), mock.MagicMock()
        )

        ef.check_friendship_evolution_for_pokemon.assert_called_once()
        return ef.check_friendship_evolution_for_pokemon.call_args.kwargs.get("attacks")
    finally:
        for n, v in orig.items():
            setattr(ef, n, v)


def test_victory_seeds_the_moveset_when_the_stored_row_is_this_pokemon():
    # The happy path the seed exists for: one fewer DB query per no-level-up
    # victory, with the exact list the checker's own fallback would have read.
    assert _run_victory_with_stored_row("iid", "iid") == ["Tackle", "Charm"]


def test_victory_declines_the_seed_when_the_stored_row_is_another_pokemon():
    """A mismatched is_main row must not feed its moveset to the checkers.

    ``is_main`` has no uniqueness constraint and ``get_main_pokemon()``
    ``fetchone()``s whatever it finds, so the stored row and the in-memory
    ``main_pokemon`` can name different Pokemon — the level-up merge below the
    seed guards itself against exactly that before touching the moveset. Seeding
    past the same mismatch evaluates a ``levelMove`` / ``known_move_type`` gate
    against ANOTHER Pokemon's moves (a Charm here would offer Sylveon to an Eevee
    that knows no Fairy move). ``None`` restores the checker's own
    ``get_pokemon(individual_id)`` lookup, which is keyed correctly.
    """
    assert _run_victory_with_stored_row("someone-else", "iid") is None


def test_victory_declines_the_seed_when_the_stored_row_has_no_id():
    # A row that cannot prove its identity is treated as a mismatch, not a match.
    assert _run_victory_with_stored_row(None, "iid") is None


def test_victory_declines_the_seed_when_neither_side_has_an_id():
    # Two missing ids must not compare equal into a match: there is nothing to
    # verify, which is precisely the case the check exists to refuse.
    assert _run_victory_with_stored_row(None, None) is None


def test_victory_seed_tolerates_int_vs_str_id_drift():
    # individual_id is TEXT in the schema but callers have passed ints; the
    # guard must not decline a genuine match over the type alone.
    assert _run_victory_with_stored_row(7, "7") == ["Tackle", "Charm"]


def test_soothe_bell_boosts_friendship_gain_by_half(monkeypatch):
    """Soothe Bell (real-game effect: 1.5x friendship gained per step/event)
    held by the main Pokémon must scale save_main_pokemon_progress's
    friendship roll the same way Lucky Egg already scales XP."""
    import types

    names = (
        "settings_obj", "translator", "services", "ankimon_db",
        "find_experience_for_level", "limit_ev_yield",
        "check_friendship_evolution_for_pokemon", "check_evolution_for_pokemon",
    )
    orig = {n: getattr(ef, n) for n in names}
    monkeypatch.setattr(ef.random, "randint", lambda a, b: 6)
    try:
        settings = mock.MagicMock()
        settings.get = lambda k, d=None: {
            "misc.remove_level_cap": False,
            "gui.pop_up_dialog_message_on_defeat": False,
        }.get(k, d)
        ef.settings_obj = settings
        ef.translator = mock.MagicMock()
        ef.translator.translate = lambda *a, **k: "msg"
        ef.services = mock.MagicMock()
        ef.ankimon_db = mock.MagicMock()
        ef.find_experience_for_level = lambda *a, **k: 10**9  # never level up
        ef.limit_ev_yield = lambda have, add: {
            "hp": 0, "attack": 0, "defense": 0,
            "special-attack": 0, "special-defense": 0, "speed": 0,
        }
        ef.check_friendship_evolution_for_pokemon = mock.MagicMock(return_value=None)
        ef.check_evolution_for_pokemon = mock.MagicMock(return_value=None)

        def _make_main(held_item):
            return types.SimpleNamespace(
                name="Pikachu", growth_rate="medium", level=50, xp=0,
                individual_id="iid", id=25, everstone=False, friendship=300,
                stats={}, ev={"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
                hp=100, held_item=held_item, pokemon_defeated=0, tier="Normal",
                is_favorite=False, evolution_rejected=False,
                invalidate_cp_cache=lambda: None,
            )

        enemy = types.SimpleNamespace(ev_yield={})

        main_no_item = _make_main(None)
        ef.services.db.get_main_pokemon.return_value = {
                "attacks": ["Tackle"],
                "ev": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
            }
        ef.save_main_pokemon_progress(main_no_item, enemy, 5, {}, mock.MagicMock(), None)

        main_soothe = _make_main("soothe-bell")
        ef.save_main_pokemon_progress(main_soothe, enemy, 5, {}, mock.MagicMock(), None)

        assert main_no_item.friendship == 300 + 6
        assert main_soothe.friendship == 300 + int(6 * 1.5)
    finally:
        for n, v in orig.items():
            setattr(ef, n, v)
