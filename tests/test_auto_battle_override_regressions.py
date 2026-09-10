"""Regression tests for auto-battle override resolution."""

import runpy
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


@lru_cache(maxsize=None)
def _load_ef():
    """Load ``encounter_functions`` via the sibling module's harness.

    ``test_encounter_functions.py`` installs MagicMock stubs for Ankimon.utils,
    Ankimon.resources, Ankimon.const and friends as an import-time side effect.
    Doing that at *collection* time would leak: this module sorts before
    test_cp_formula.py, so every module collected after it would bind mocks
    instead of the real helpers.  Loading lazily keeps collection clean — by the
    time a test here runs, the other modules have bound their real symbols and
    conftest's autouse ``restore_package_stubs`` repairs the stubs afterwards.
    """
    return runpy.run_path(
        str(Path(__file__).with_name("test_encounter_functions.py"))
    )["ef"]


def _run_override_scenario(
    auto_battle_setting: int,
    override: str,
    *,
    wishlist_ids=(),
    collected_ids=(),
    enemy_tier="Normal",
    auto_catch_overrides=None,
):
    ef = _load_ef()
    original_names = (
        "settings_obj",
        "encounter_data",
        "ankimon_tracker_obj",
        "catch_pokemon",
        "kill_pokemon",
        "new_pokemon",
        "_auto_battle_override",
    )
    originals = {name: getattr(ef, name) for name in original_names}

    settings = mock.MagicMock()
    wishlist = list(wishlist_ids)
    auto_catch_overrides = auto_catch_overrides or {}

    def get_setting(key, default=None):
        if key == "battle.automatic_battle":
            return auto_battle_setting
        if key == "battle.auto_catch_wishlist":
            return wishlist
        if key.startswith("battle.auto_catch_"):
            return auto_catch_overrides.get(key, False)
        return default

    settings.get = get_setting
    tracker = mock.MagicMock()
    tracker.faint_processed = False
    catch = mock.MagicMock()
    defeat = mock.MagicMock()
    new = mock.MagicMock()
    main_pokemon = mock.MagicMock()
    enemy_pokemon = mock.MagicMock(
        id=25,
        name="Pikachu",
        shiny=False,
        tier=enemy_tier,
    )
    test_window = mock.MagicMock()
    evo_window = mock.MagicMock()
    reviewer_obj = mock.MagicMock()
    logger = mock.MagicMock()
    achievements = {}

    try:
        ef.settings_obj = settings
        ef.encounter_data = SimpleNamespace(
            MEGA=set(),
            GMAX=set(),
            REGIONAL_FORM_REGION={},
        )
        ef.ankimon_tracker_obj = tracker
        ef.catch_pokemon = catch
        ef.kill_pokemon = defeat
        ef.new_pokemon = new
        ef._auto_battle_override = override

        ef.handle_enemy_faint(
            main_pokemon,
            enemy_pokemon,
            set(collected_ids),
            test_window,
            evo_window,
            reviewer_obj,
            logger,
            achievements,
        )

        return SimpleNamespace(
            tracker=tracker,
            catch=catch,
            defeat=defeat,
            new=new,
            main_pokemon=main_pokemon,
            enemy_pokemon=enemy_pokemon,
            test_window=test_window,
            reviewer_obj=reviewer_obj,
            override_after=ef.get_auto_battle_override(),
        )
    finally:
        for name, value in originals.items():
            setattr(ef, name, value)


def _assert_completed_override(result):
    result.new.assert_called_once_with(
        result.enemy_pokemon,
        result.test_window,
        result.tracker,
        result.reviewer_obj,
    )
    result.main_pokemon.reset_bonuses.assert_called_once_with()
    assert result.tracker.faint_processed is True
    assert result.tracker.general_card_count_for_battle == 0
    assert result.override_after is None


def test_catch_override_supersedes_auto_defeat():
    result = _run_override_scenario(2, "catch")

    result.catch.assert_called_once()
    result.defeat.assert_not_called()
    _assert_completed_override(result)


def test_defeat_override_supersedes_auto_catch():
    result = _run_override_scenario(1, "defeat")

    result.defeat.assert_called_once()
    result.catch.assert_not_called()
    _assert_completed_override(result)


def test_defeat_override_precedes_wishlist_and_catch_if_new():
    result = _run_override_scenario(
        3,
        "defeat",
        wishlist_ids=(25,),
        collected_ids=(),
    )

    result.defeat.assert_called_once()
    result.catch.assert_not_called()
    _assert_completed_override(result)


def test_defeat_override_yields_to_legendary_auto_catch_safety_net():
    result = _run_override_scenario(
        1,
        "defeat",
        enemy_tier="Legendary",
        auto_catch_overrides={"battle.auto_catch_legendary": True},
    )

    result.catch.assert_called_once()
    result.defeat.assert_not_called()
    _assert_completed_override(result)


def test_defeat_override_yields_to_mythical_auto_catch_safety_net():
    result = _run_override_scenario(
        1,
        "defeat",
        enemy_tier="Mythical",
        auto_catch_overrides={"battle.auto_catch_mythical": True},
    )

    result.catch.assert_called_once()
    result.defeat.assert_not_called()
    _assert_completed_override(result)


def test_defeat_override_kills_legendary_when_safety_net_disabled():
    result = _run_override_scenario(
        1,
        "defeat",
        enemy_tier="Legendary",
        auto_catch_overrides={"battle.auto_catch_legendary": False},
    )

    result.defeat.assert_called_once()
    result.catch.assert_not_called()
    _assert_completed_override(result)
