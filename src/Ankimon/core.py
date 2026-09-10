"""
core.py — the aqt-free composition root for Ankimon's game state.

Builds the addon's core objects — logger, database, settings, translator, the
main and (placeholder) enemy Pokemon, trainer card, tracker, achievements — and
registers them in the service registry (:mod:`Ankimon.services`). It imports
NOTHING from aqt/PyQt6, so the exact same construction runs in two places:

* **Production** — ``singletons.py`` calls :func:`build_core` and then builds the
  Qt windows on top, wiring the real :class:`QtPresenter` into ``services.ui``.
* **Headless** — the agent harness calls :func:`build_core` and wires recording
  fakes + the default :class:`HeadlessPresenter` instead.

Sharing this code (rather than duplicating it in the harness) is what keeps the
two roots from drifting, and it means the headless tests exercise the very same
construction production uses.

Ordering note: ``services.db`` and ``services.logger`` are registered BEFORE
``Settings()`` is constructed, because ``Settings.load_config`` reads
``services.db`` on the first call. Registering them late would make the very
first config load fall through to defaults.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from .services import services
from .pyobj.InfoLogger import ShowInfoLogger
from .pyobj.database_manager import get_db
from .pyobj.settings import Settings
from .pyobj.translator import Translator
from .pyobj.pokemon_obj import PokemonObject
from .pyobj.trainer_card import TrainerCard
from .pyobj.ankimon_tracker import AnkimonTracker
from .functions.update_main_pokemon import update_main_pokemon
from .functions.badges_functions import populate_achievements_from_badges


def _build_placeholder_enemy() -> PokemonObject:
    """The initial enemy Pokemon shown before the first real encounter.

    Mirrors the historical default from singletons.py verbatim. It is a
    placeholder: ``new_pokemon()`` overwrites it (via ``update_stats``) the first
    time an encounter is generated.
    """
    return PokemonObject(
        name="Rattata",
        shiny=False,
        id=19,
        level=5,
        ability="Run Away",
        type=["Normal"],
        stats={
            "hp": 39, "atk": 52, "def": 43, "spa": 60, "spd": 50, "spe": 65, "xp": 101,
        },
        attacks=["Quick Attack", "Tackle", "Tail Whip"],
        base_experience=58,
        growth_rate="medium-slow",
        hp=30,
        ev={"hp": 3, "atk": 5, "def": 4, "spa": 1, "spd": 2, "spe": 3},
        iv={"hp": 27, "atk": 24, "def": 3, "spa": 24, "spd": 16, "spe": 21},
        gender="M",
        battle_status="Fighting",
        xp=0,
        position=(5, 5),
        tier="Normal",
        captured_date=None,
        individual_id=str(uuid.uuid4()),
    )


def build_core() -> SimpleNamespace:
    """Construct the core game objects and register them in ``services``.

    Returns a ``SimpleNamespace`` of the constructed objects so a caller
    (singletons / the harness) can also expose them as module globals for
    back-compat.
    """
    # Logger + DB first, and registered immediately: Settings() reads services.db.
    logger = ShowInfoLogger()
    services.populate(logger=logger)

    ankimon_db = get_db(logger)
    services.populate(db=ankimon_db)

    settings_obj = Settings()
    services.populate(settings=settings_obj)

    # Run before TrainerCard construction, whose initializer can sync stats.
    try:
        from .pyobj.ankimon_leaderboard import migrate_credentials_from_db

        migrate_credentials_from_db()
    except Exception as e:
        print(f"Ankimon: Error during leaderboard credentials migration: {e}")

    translator = Translator(language=int(settings_obj.get("misc.language")))
    services.populate(translator=translator)

    # Game state.
    main_pokemon, mainpokemon_empty = update_main_pokemon()
    enemy_pokemon = _build_placeholder_enemy()

    trainer_card = TrainerCard(
        logger,
        main_pokemon,
        settings_obj,
        trainer_name=settings_obj.get("trainer.name"),
        trainer_id="".join(filter(str.isdigit, str(uuid.uuid4()).replace("-", ""))),
        team="Pikachu (Level 25), Charizard (Level 50), Bulbasaur (Level 15)",
        league="Unranked",
    )

    ankimon_tracker_obj = AnkimonTracker(trainer_card=trainer_card)
    ankimon_tracker_obj.set_main_pokemon(main_pokemon)
    ankimon_tracker_obj.set_enemy_pokemon(enemy_pokemon)

    achievements = populate_achievements_from_badges(
        {str(i): False for i in range(1, 69)}
    )

    services.populate(
        tracker=ankimon_tracker_obj,
        main_pokemon=main_pokemon,
        enemy_pokemon=enemy_pokemon,
        trainer_card=trainer_card,
        achievements=achievements,
    )

    return SimpleNamespace(
        logger=logger,
        ankimon_db=ankimon_db,
        settings_obj=settings_obj,
        translator=translator,
        main_pokemon=main_pokemon,
        mainpokemon_empty=mainpokemon_empty,
        enemy_pokemon=enemy_pokemon,
        trainer_card=trainer_card,
        ankimon_tracker_obj=ankimon_tracker_obj,
        achievements=achievements,
    )


# --- Runtime global binding -------------------------------------------------
#
# The core logic modules (battle_loop / encounter_functions / the poke-engine
# bridge) refer to shared singletons by bare name (``main_pokemon``,
# ``settings_obj`` …) — exactly as they did when they imported those names from
# ``singletons``. Python resolves such bare names against the *module's own
# globals* at call time; a module-level ``__getattr__`` does NOT intercept them
# (it only fires for ``module.attr`` access from outside). So we bind them as
# real module globals here, pointing at the live registry objects. This both
# preserves the original behaviour (those imports were snapshots of stable
# objects) and keeps function parameters of the same name shadowing correctly.
#
# Each entry: module path -> {bare global name: services attribute name}.
_RUNTIME_GLOBALS = {
    "Ankimon.functions.encounter_functions": {
        "main_pokemon": "main_pokemon",
        "ankimon_tracker_obj": "tracker",
        "trainer_card": "trainer_card",
        "settings_obj": "settings",
        "translator": "translator",
        "ankimon_db": "db",
        "pokemon_pc": "pokemon_pc",
    },
    "Ankimon.battle_loop": {
        "main_pokemon": "main_pokemon",
        "enemy_pokemon": "enemy_pokemon",
        "settings_obj": "settings",
        "reviewer_obj": "reviewer",
        "ankimon_tracker_obj": "tracker",
        "test_window": "test_window",
        "evo_window": "evo_window",
        "logger": "logger",
        "achievements": "achievements",
        "trainer_card": "trainer_card",
        "translator": "translator",
    },
    "Ankimon.functions.ankimon_hooks_to_poke_engine": {
        "ankimon_tracker_obj": "tracker",
        "settings_obj": "settings",
    },
}


def bind_runtime_globals() -> None:
    """Point the core logic modules' bare globals at the live registry objects.

    Call this from the composition root AFTER every service (core *and* the GUI
    windows / fakes) has been registered, since some bound names (test_window,
    evo_window, pokemon_pc, reviewer) are populated only after build_core().
    """
    import importlib

    root_pkg = __package__ or "Ankimon"
    for module_path, mapping in _RUNTIME_GLOBALS.items():
        real_module_path = module_path
        if module_path.startswith("Ankimon."):
            real_module_path = root_pkg + module_path[len("Ankimon"):]
        module = importlib.import_module(real_module_path)
        for global_name, attr in mapping.items():
            setattr(module, global_name, getattr(services, attr))
