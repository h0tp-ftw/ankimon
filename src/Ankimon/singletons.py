"""
singletons.py — the production composition root (Anki / Qt).

Originally this module both *built* every Ankimon object and *held* it as a
module global. It has since been split:

* The aqt-free core (logger, DB, settings, translator, the Pokemon, trainer
  card, tracker, achievements) is built by :func:`Ankimon.core.build_core`.
* This module builds the Qt **windows** on top of that core and registers
  everything — core objects, GUI windows, and the Qt UI presenter — in the
  service registry (:mod:`Ankimon.services`).

It also keeps the historical module-level names (``settings_obj``, ``logger``,
``test_window``, …) and the ``mw.<service>`` shims so the not-yet-migrated
importers and ``__init__.py`` keep working unchanged.

The agent harness is the *other* root: it calls the same ``build_core()`` but
wires recording fakes + the headless presenter instead of the Qt windows below.

Author: Axil (original); split into core/gui 2026-06.
"""

from aqt import mw

# GUI window/widget classes (all import Qt).
from .pyobj.settings_window import SettingsWindow
from .pyobj.test_window import TestWindow
from .pyobj.achievement_window import AchievementWindow
from .pyobj.ankimon_tracker_window import AnkimonTrackerWindow
from .pyobj.ankimon_shop import PokemonShopManager
from .ankidex.ankidex_obj import Ankidex
from .pyobj.reviewer_obj import Reviewer_Manager
from .pyobj.evolution_window import EvoWindow
from .pyobj.starter_window import StarterWindow
from .pyobj.item_window import ItemWindow
from .pyobj.pc_box import PokemonPC
from .gui_entities import (
    License,
    Credits,
    TableWidget,
    IDTableWidget,
    NatureTableWidget,
    Version_Dialog,
)
from .resources import addon_dir
from .services import services
from .core import build_core, bind_runtime_globals
from .gui_presenter import QtPresenter
from .utils import is_alive

# --- Core (aqt-free) composition. Populates services.{db,logger,settings,
#     translator,tracker,main_pokemon,enemy_pokemon,trainer_card,achievements}. ---
_core = build_core()
logger = _core.logger
ankimon_db = _core.ankimon_db
settings_obj = _core.settings_obj
translator = _core.translator
main_pokemon = _core.main_pokemon
mainpokemon_empty = _core.mainpokemon_empty
enemy_pokemon = _core.enemy_pokemon
trainer_card = _core.trainer_card
ankimon_tracker_obj = _core.ankimon_tracker_obj
achievements = _core.achievements
mw.ankimon_db = ankimon_db
mw.logger = logger
mw.translator = translator
mw.settings_obj = settings_obj

# --- GUI windows (Qt), built on top of the core objects above. ---
settings_window = SettingsWindow(
    config=settings_obj.config,  # Use settings_obj.config instead of settings_obj.settings.config
    set_config_callback=settings_obj.set,
    save_config_callback=settings_obj.save_config,
    load_config_callback=settings_obj.load_config,
)
mw.settings_ankimon = settings_window

# Create an instance of the MainWindow
test_window = TestWindow(
    main_pokemon=main_pokemon,
    enemy_pokemon=enemy_pokemon,
    settings_obj=settings_obj,
    ankimon_tracker_obj=ankimon_tracker_obj,
    translator=translator,
    parent=mw,
    logger=logger,
)
mw.test_window = test_window

achievement_bag = AchievementWindow()
mw.achievement_bag = achievement_bag

# Initialize the Pokémon Shop Manager
shop_manager = PokemonShopManager(
    logger=logger,
    settings_obj=settings_obj,
    set_callback=settings_obj.set,
    get_callback=settings_obj.get,
)
mw.shop_manager = shop_manager

ankimon_tracker_window = AnkimonTrackerWindow(tracker=ankimon_tracker_obj)
mw.ankimon_tracker_window = ankimon_tracker_window

# Ankidex V2
ankidex_window = None
def get_ankidex_window():
    global ankidex_window
    if not is_alive(ankidex_window):
        ankidex_window = Ankidex(addon_dir, ankimon_tracker=ankimon_tracker_obj)
        mw.ankidex_window = ankidex_window
    return ankidex_window
get_ankidex_window()

reviewer_obj = Reviewer_Manager(
    settings_obj=settings_obj,
    main_pokemon=main_pokemon,
    enemy_pokemon=enemy_pokemon,
    ankimon_tracker=ankimon_tracker_obj,
)
mw.reviewer_obj = reviewer_obj

item_window = ItemWindow(
    logger=logger,
    settings_obj=settings_obj,
    main_pokemon=main_pokemon,
    enemy_pokemon=enemy_pokemon,
    achievements=achievements,
    starter_window=starter_window,
    evo_window=evo_window,
)
mw.item_window = item_window

pokemon_pc = PokemonPC(
    logger=logger,
    translator=translator,
    reviewer_obj=reviewer_obj,
    test_window=test_window,
    settings=settings_obj,
    main_pokemon=main_pokemon,
)
mw.pokemon_pc = pokemon_pc

# UI Utilities
eff_chart = TableWidget()
gen_id_chart = IDTableWidget()
nature_chart = NatureTableWidget()
license = License()
credits = Credits()
version_dialog = Version_Dialog()

def swap_ankimon_account():
    """Toggles between ankimon.db and ankimonDEV.db and refreshes the game state."""
    from aqt.utils import tooltip
    from .functions.update_main_pokemon import update_main_pokemon
    from .functions.encounter_functions import new_pokemon, clear_encounter_cache

    current_name = mw.ankimon_db.db_path.name
    new_name = "ankimonDEV.db" if current_name == "ankimon.db" else "ankimon.db"

    try:
        # Switch DB connection
        mw.ankimon_db.switch_database(new_name)

        # Reload configuration (in-place)
        mw.settings_obj.load_config()

        # Update main pokemon in-place
        update_main_pokemon(mw.main_pokemon)

        # Refresh trainer card data
        mw.trainer_card.refresh()

        # Reset battle and capture state so no stale data can bleed through
        mw.ankimon_tracker_obj.caught = 0
        mw.ankimon_tracker_obj.general_card_count_for_battle = 0
        
        # Sync collected IDs to current account
        from .reviewer_ui import set_collected_ids
        new_ids = mw.ankimon_db.get_all_pokemon_ids()
        set_collected_ids(new_ids)

        # Clear encounter percentages cache (uses new trainer level/stats)
        clear_encounter_cache()

        # Generate a fresh encounter for the new account
        new_pokemon(mw.enemy_pokemon, mw.test_window, mw.ankimon_tracker_obj, mw.reviewer_obj)

        # Refresh windows if they are open
        if hasattr(mw, "pokemon_pc") and is_alive(mw.pokemon_pc):
            # Reset selection because IDs change between databases
            mw.pokemon_pc._selected_individual_id = None
            mw.pokemon_pc.pokemon_details_layout = None
            mw.pokemon_pc.refresh_gui()
        
        if hasattr(mw, "item_window") and is_alive(mw.item_window):
            mw.item_window.renewWidgets()

        if hasattr(mw, "ankidex_window") and is_alive(mw.ankidex_window):
            mw.ankidex_window.update_ui_data()

        # If in reviewer, force HUD update
        if hasattr(mw, "reviewer") and mw.reviewer and hasattr(mw, "reviewer_obj"):
            mw.reviewer_obj.update_life_bar(mw.reviewer, None, 0)

        tooltip(f"Switched to {new_name}")
    except Exception as e:
        tooltip(f"Failed to switch account: {e}")
        import traceback
        traceback.print_exc()

evo_window = EvoWindow(
    logger,
    settings_obj,
    main_pokemon,
    translator,
    reviewer_obj,
    test_window,
    achievements,
)
starter_window = StarterWindow(logger, settings_obj)
item_window = ItemWindow(  # Create an instance of the MainWindow
    logger=logger,
    settings_obj=settings_obj,
    main_pokemon=main_pokemon,
    enemy_pokemon=enemy_pokemon,
    achievements=achievements,
    starter_window=starter_window,
    evo_window=evo_window,
)

pokemon_pc = PokemonPC(
    logger=logger,
    translator=translator,
    reviewer_obj=reviewer_obj,
    test_window=test_window,
    settings=settings_obj,
    main_pokemon=main_pokemon,
)

# --- Register the GUI windows + the Qt UI presenter in the registry, so the
#     core logic (battle_loop / encounter_functions) reaches them via services. ---
services.populate(
    ui=QtPresenter(),
    test_window=test_window,
    evo_window=evo_window,
    pokemon_pc=pokemon_pc,
    reviewer=reviewer_obj,
)

# Bind the core logic modules' bare globals (main_pokemon, settings_obj,
# test_window, …) to the now-fully-populated registry. Must run after the
# services.populate above so the GUI window bindings are non-None.
bind_runtime_globals()
