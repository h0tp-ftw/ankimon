"""Manual double faint through the real reviewer and popup dispatch functions."""

import importlib.util
import sys
import types
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1] / "src" / "Ankimon"


def _load(monkeypatch, name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


class Pokemon:
    def __init__(self, individual_id, name, hp):
        self.individual_id = individual_id
        self.name = name
        self.id = 25 if name == "pikachu" else 19
        self.hp = hp
        self.current_hp = hp
        self.max_hp = 100
        self.reset_count = 0

    def update_stats(self, **data):
        for key, value in data.items():
            setattr(self, key, value)

    def calculate_max_hp(self):
        return 100

    def reset_bonuses(self):
        self.reset_count += 1


@pytest.fixture
def game(monkeypatch):
    # Only Anki/Qt and persistence are stubbed. The three modules under test
    # come from their actual source files and call each other.
    monkeypatch.syspath_prepend(str(ROOT.parent))
    main = Pokemon("main-1", "pikachu", 0)
    enemy = Pokemon(None, "rattata", 0)
    reviewer = types.SimpleNamespace(refresh_hud=lambda: None)
    window = types.SimpleNamespace(current_view="battle", force_display_battle=lambda: None)
    singletons = types.ModuleType("Ankimon.singletons")
    for key, value in {
        "main_pokemon": main,
        "enemy_pokemon": enemy,
        "ankimon_tracker_obj": types.SimpleNamespace(),
        "get_test_window": lambda: window,
        "get_evo_window": lambda: None,
        "logger": None,
        "achievements": {},
        "trainer_card": None,
        "reviewer_obj": reviewer,
    }.items():
        setattr(singletons, key, value)
    monkeypatch.setitem(sys.modules, "Ankimon.singletons", singletons)

    anki = types.ModuleType("anki")
    anki.__path__ = []
    hooks = types.ModuleType("anki.hooks")
    hooks.wrap = lambda old, new, position: old
    monkeypatch.setitem(sys.modules, "anki", anki)
    monkeypatch.setitem(sys.modules, "anki.hooks", hooks)
    aqt = types.ModuleType("aqt")
    aqt.__path__ = []
    aqt_reviewer = types.ModuleType("aqt.reviewer")
    aqt_reviewer.Reviewer = type("Reviewer", (), {})
    aqt_utils = types.ModuleType("aqt.utils")
    aqt_utils.downArrow = lambda: ""
    aqt_utils.tooltip = lambda message: None
    aqt_utils.tr = types.SimpleNamespace()
    monkeypatch.setitem(sys.modules, "aqt", aqt)
    monkeypatch.setitem(sys.modules, "aqt.reviewer", aqt_reviewer)
    monkeypatch.setitem(sys.modules, "aqt.utils", aqt_utils)

    battle = _load(monkeypatch, "Ankimon.battle_loop", "battle_loop.py")
    registry = _load(monkeypatch, "Ankimon.hook_registry", "hook_registry.py")
    ui = _load(monkeypatch, "Ankimon.reviewer_ui", "reviewer_ui.py")
    monkeypatch.setattr(battle, "services", types.SimpleNamespace(test_window=window))
    monkeypatch.setattr(battle, "is_alive", lambda obj: obj is not None)

    fainted = []

    def handle_faint(pokemon, opponent, *args, **kwargs):
        fainted.append((pokemon.individual_id, opponent.id, kwargs))
        pokemon.hp = pokemon.current_hp = pokemon.max_hp
        pokemon.reset_bonuses()

    monkeypatch.setattr(battle, "handle_main_pokemon_faint", handle_faint)
    monkeypatch.setattr(registry, "catch_pokemon", lambda *args: None)
    monkeypatch.setattr(registry, "kill_pokemon", lambda *args: None)
    spawned = []

    def new_pokemon(pokemon, *args, **kwargs):
        spawned.append(kwargs)
        battle._cancel_main_faint_deferral()
        pokemon._ankimon_encounter_token = object()
        pokemon.id = 20
        pokemon.hp = 100

    monkeypatch.setattr(registry, "new_pokemon", new_pokemon)
    return types.SimpleNamespace(
        main=main, enemy=enemy, battle=battle, registry=registry,
        ui=ui, reviewer=reviewer, fainted=fainted, spawned=spawned,
    )


@pytest.mark.parametrize("choice", ["catch", "defeat"])
@pytest.mark.parametrize("entry", ["reviewer", "popup"])
def test_choice_settles_double_faint_once(game, choice, entry):
    game.battle._defer_main_faint_until_enemy_resolved(
        game.main, game.enemy, game.reviewer, translator=None
    )
    if entry == "reviewer":
        getattr(game.ui, f"{choice}_shortcut_function")()
    elif choice == "catch":
        # TestWindow._trigger_catch_pokemon delegates to this dispatcher.
        game.registry.CatchPokemonHook(game.ui._collected_pokemon_ids)
    else:
        game.registry.DefeatPokemonHook()

    assert game.fainted == [("main-1", 19, {"spawn_replacement": False})]
    assert game.main.hp == 100
    assert game.main.reset_count == 1
    assert game.enemy.id == 20
    assert len(game.spawned) == 1
    assert game.battle._main_faint_deferred is False


def test_cycling_companion_cancels_pending_faint(game, monkeypatch):
    game.battle._defer_main_faint_until_enemy_resolved(
        game.main, game.enemy, game.reviewer, translator=None
    )
    game.ui._team_cycle_count = lambda: 2
    game.ui.get_team_pokemon_list = lambda: ["main-1", "main-2"]
    game.ui.get_pokemon_from_collection = lambda individual_id: {
        "individual_id": individual_id, "id": 1, "name": "bulbasaur", "level": 5,
    }
    save_module = types.ModuleType("Ankimon.functions.update_main_pokemon")
    save_module.save_main_pokemon = lambda pokemon: None
    def apply_loaded_hp(pokemon, data):
        pokemon.max_hp = pokemon.calculate_max_hp()
        pokemon.hp = pokemon.current_hp = data.get("current_hp", pokemon.max_hp)

    save_module._apply_loaded_hp = apply_loaded_hp
    monkeypatch.setitem(sys.modules, save_module.__name__, save_module)
    pokedex = types.ModuleType("Ankimon.functions.pokedex_functions")
    pokedex.search_pokedex_by_id = lambda pokemon_id: "bulbasaur"
    pokedex.search_pokedex = lambda name, field: {"hp": 45}
    monkeypatch.setitem(sys.modules, pokedex.__name__, pokedex)
    game.ui.services = types.SimpleNamespace(
        settings=types.SimpleNamespace(get=lambda *args: 2),
        translator=types.SimpleNamespace(translate=lambda *args, **kwargs: "Switched"),
    )
    game.ui.cycle_team_pokemon()
    assert game.main.individual_id == "main-2"
    assert game.main.hp == 100
    assert game.battle._main_faint_deferred is False
    game.registry.CatchPokemonHook(set())
    assert game.fainted == []
    assert game.main.reset_count == 1  # only the intentional switch reset


def test_reselecting_active_companion_keeps_reviewer_catch_recovery(game, monkeypatch):
    """The PC may select its current row while the enemy choice is pending."""
    game.battle._defer_main_faint_until_enemy_resolved(
        game.main, game.enemy, game.reviewer, translator=None
    )
    sys.modules["aqt"].mw = types.SimpleNamespace()
    sys.modules["aqt.utils"].showInfo = lambda *args: None
    sys.modules["aqt.utils"].showWarning = lambda *args: None
    pyqt = types.ModuleType("PyQt6")
    pyqt.__path__ = []
    monkeypatch.setitem(sys.modules, "PyQt6", pyqt)
    for leaf in ("QtWidgets", "QtGui", "QtCore"):
        module = types.ModuleType(f"PyQt6.{leaf}")
        module.__all__ = []
        monkeypatch.setitem(sys.modules, module.__name__, module)
    for leaf, symbol in (
        ("InfoLogger", "ShowInfoLogger"),
        ("pokemon_obj", "PokemonObject"),
        ("translator", "Translator"),
        ("test_window", "TestWindow"),
        ("reviewer_obj", "Reviewer_Manager"),
    ):
        module = types.ModuleType(f"Ankimon.pyobj.{leaf}")
        setattr(module, symbol, type(symbol, (), {}))
        monkeypatch.setitem(sys.modules, module.__name__, module)
    migration = types.ModuleType("Ankimon.functions.migration")
    migration.migrate_starter_individual_id = lambda *args: None
    monkeypatch.setitem(sys.modules, migration.__name__, migration)

    collection = _load(
        monkeypatch, "Ankimon.pyobj.collection_dialog", "pyobj/collection_dialog.py"
    )
    collection.MainPokemon(
        {"individual_id": "main-1", "id": game.main.id, "current_hp": 0},
        game.main, None, None, game.reviewer, None,
    )

    assert game.main.hp == 0
    assert game.battle._main_faint_deferred is True
    game.ui.catch_shortcut_function()
    assert game.main.hp == game.main.max_hp
    assert game.fainted == [("main-1", 19, {"spawn_replacement": False})]
    assert len(game.spawned) == 1


def test_mutated_main_identity_cannot_receive_original_faint(game):
    game.battle._defer_main_faint_until_enemy_resolved(
        game.main, game.enemy, game.reviewer, translator=None
    )
    game.main.individual_id = "main-2"
    game.main.hp = 100
    game.registry.DefeatPokemonHook()
    assert game.fainted == []
    assert game.main.hp == 100
    assert game.battle._main_faint_deferred is False


def test_different_encounter_cannot_resolve_pending_faint(game):
    game.battle._defer_main_faint_until_enemy_resolved(
        game.main, game.enemy, game.reviewer, translator=None
    )
    game.enemy._ankimon_encounter_token = object()
    game.registry.CatchPokemonHook(set())
    assert game.fainted == []
    assert game.battle._main_faint_deferred is False


def test_new_encounter_cancels_abandoned_faint(game, monkeypatch):
    from Ankimon.functions import encounter_functions as encounters

    game.battle._defer_main_faint_until_enemy_resolved(
        game.main, game.enemy, game.reviewer, translator=None
    )
    old_token = game.enemy._ankimon_encounter_token
    game.main.level = 5
    monkeypatch.setattr(encounters, "main_pokemon", game.main)
    monkeypatch.setattr(encounters, "ankimon_tracker_obj", types.SimpleNamespace())
    monkeypatch.setattr(encounters, "clear_auto_battle_override", lambda: None)
    monkeypatch.setattr(
        encounters,
        "generate_random_pokemon",
        lambda *args: (
            "caterpie", 10, 5, None, ["Bug"], {"hp": 45}, ["tackle"],
            39, "medium", {}, {}, "M", "fighting", {}, "Normal", {},
            False, "Hardy",
        ),
    )

    class StopAfterReplacement(Exception):
        pass

    def stop():
        raise StopAfterReplacement()

    tracker = types.SimpleNamespace(randomize_battle_scene=stop)
    with pytest.raises(StopAfterReplacement):
        encounters.new_pokemon(game.enemy, None, tracker, None)

    assert game.enemy._ankimon_encounter_token is not old_token
    assert game.battle._main_faint_deferred is False
    game.main.hp = 100
    game.enemy.hp = 0
    game.registry.CatchPokemonHook(set())
    assert game.fainted == []
