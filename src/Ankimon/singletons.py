"""
singletons.py — the production composition root (Anki / Qt), reload-safe.

Originally this module both *built* every Ankimon object and *held* it as a
module global. It has since been split:

* The aqt-free core (logger, DB, settings, translator, the Pokemon, trainer
  card, tracker, achievements) is built by :func:`Ankimon.core.build_core`.
* This module builds the Qt **windows** on top of that core and registers
  everything — core objects, GUI windows, and the Qt UI presenter — in the
  service registry (:mod:`Ankimon.services`).

Reload safety (F31): construction is idempotent and lazy.

* The **core** is get-or-create through the services registry: when a previous
  composition root already populated ``services`` (an add-on reload, a double
  boot, or the harness), the live registry objects are reused instead of being
  rebuilt — so a second boot cannot duplicate the DB connection, the tracker,
  or the ``Reviewer_Manager`` (whose constructor registers ``gui_hooks``).
* The **windows** are no longer constructed at import time. Each window is
  built on first access — via its ``get_*_window()`` factory or via plain
  ``from .singletons import <name>`` (which lands in the module-level
  ``__getattr__`` below) — and cached, with :func:`Ankimon.utils.is_alive`
  liveness checks so a window whose underlying C++ object was deleted is
  transparently re-created instead of handed out dead.

The historical module-level names (``settings_obj``, ``logger``,
``test_window``, …) and the ``mw.<service>`` shims are kept, so the
not-yet-migrated importers and ``__init__.py`` keep working unchanged: the
window names now resolve through ``__getattr__`` to the same live instances.

The agent harness is the *other* root: it calls the same ``build_core()`` but
wires recording fakes + the headless presenter instead of the Qt windows below.

Author: Axil (original); split into core/gui 2026-06; reload-safe 2026-07.
"""

from types import SimpleNamespace

from aqt import mw

from .pyobj.ankimon_shop import PokemonShopManager
from .pyobj.reviewer_obj import Reviewer_Manager
from .resources import addon_dir
from .services import services
from .core import build_core, bind_runtime_globals
from .gui_presenter import QtPresenter
from .utils import is_alive

# --- Core (aqt-free) composition: get-or-create (reload-safe). ---------------
# First boot: build_core() constructs everything and populates services.
# Reload / double boot (the services module survived): reuse the live registry
# objects instead of constructing duplicates.


def _core_is_populated() -> bool:
    """True when a previous composition root already built the core."""
    return (
        services.db is not None
        and services.logger is not None
        and services.settings is not None
    )


def _core_from_registry() -> SimpleNamespace:
    """Mirror the live registry objects into the shape build_core() returns."""
    return SimpleNamespace(
        logger=services.logger,
        ankimon_db=services.db,
        settings_obj=services.settings,
        translator=services.translator,
        main_pokemon=services.main_pokemon,
        # Only known at first construction (update_main_pokemon() reports it);
        # nothing imports it, so a reused root does not recompute it.
        mainpokemon_empty=None,
        enemy_pokemon=services.enemy_pokemon,
        trainer_card=services.trainer_card,
        ankimon_tracker_obj=services.tracker,
        achievements=services.achievements,
    )


_core = _core_from_registry() if _core_is_populated() else build_core()
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

# Back-compat shims: modules not yet migrated still read mw.<service>. These
# mirror the registry and are removed file-by-file as call sites move to
# `services`. (NOTE: menu_buttons.py re-creates mw.translator at its own import
# time; __init__.py re-points mw.translator back afterwards. Both go away when
# menu_buttons is migrated.)
mw.ankimon_db = ankimon_db
mw.logger = logger
mw.translator = translator
mw.settings_obj = settings_obj

# --- Window-less managers (eager, get-or-create where the registry knows them). ---

# The Pokémon Shop Manager. Not registry-backed (yet): cheap, side-effect-free
# construction, so rebuilding it on a reload is harmless.
shop_manager = PokemonShopManager(
    logger=logger,
    settings_obj=settings_obj,
    set_callback=settings_obj.set,
    get_callback=settings_obj.get,
)

# Reviewer manager. Get-or-create: its constructor appends to gui_hooks, so a
# reload that re-ran it unconditionally would double-register those hooks.
reviewer_obj = services.reviewer
if reviewer_obj is None:
    reviewer_obj = Reviewer_Manager(
        settings_obj=settings_obj,
        main_pokemon=main_pokemon,
        enemy_pokemon=enemy_pokemon,
        ankimon_tracker=ankimon_tracker_obj,
    )
    services.populate(reviewer=reviewer_obj)

# --- GUI windows: lazy, idempotent factories (F31). ---------------------------
#
# No window is constructed at import time. The seam-registered windows
# (test_window / evo_window / pokemon_pc) are cached in the services registry
# itself; every other window lives in _WINDOW_CACHE. After registering a seam
# window its factory re-runs core.bind_runtime_globals() so the core logic
# modules' bare globals (battle_loop.test_window, …) pick up the live instance.

_WINDOW_CACHE = {}


def _cached(name, factory):
    """Get-or-create a non-registry window: reuse while alive, else rebuild."""
    win = _WINDOW_CACHE.get(name)
    if is_alive(win):
        return win
    win = factory()
    _WINDOW_CACHE[name] = win
    return win


def get_settings_window():
    def _build():
        from .pyobj.settings_window import SettingsWindow

        win = SettingsWindow(
            config=dict(settings_obj.config),  # detached copy to avoid live mutation aliasing
            set_config_callback=settings_obj.set,
            save_config_callback=settings_obj.save_config,
            load_config_callback=settings_obj.load_config,
        )
        # Back-compat shim (pre-F31 this was written at import time).
        mw.settings_ankimon = win
        return win

    return _cached("settings_window", _build)


def get_test_window():
    win = services.test_window
    if is_alive(win):
        return win
    from .pyobj.test_window import TestWindow

    win = TestWindow(
        main_pokemon=main_pokemon,
        enemy_pokemon=enemy_pokemon,
        settings_obj=settings_obj,
        ankimon_tracker_obj=ankimon_tracker_obj,
        translator=translator,
        parent=mw,
        logger=logger,
    )
    services.populate(test_window=win)
    bind_runtime_globals()
    return win


def get_achievement_bag():
    def _build():
        from .pyobj.achievement_window import AchievementWindow

        return AchievementWindow()

    return _cached("achievement_bag", _build)


def get_ankimon_tracker_window():
    def _build():
        from .pyobj.ankimon_tracker_window import AnkimonTrackerWindow

        return AnkimonTrackerWindow(tracker=ankimon_tracker_obj)

    return _cached("ankimon_tracker_window", _build)


def get_pokedex_window():
    def _build():
        from .pokedex.pokedex_obj import Pokedex

        return Pokedex(addon_dir, ankimon_tracker=ankimon_tracker_obj)

    return _cached("pokedex_window", _build)


def get_eff_chart():
    def _build():
        from .gui_entities import TableWidget

        return TableWidget()

    return _cached("eff_chart", _build)


def get_pokedex_widget():
    def _build():
        from .gui_entities import Pokedex_Widget

        return Pokedex_Widget()

    return _cached("pokedex", _build)


def get_gen_id_chart():
    def _build():
        from .gui_entities import IDTableWidget

        return IDTableWidget()

    return _cached("gen_id_chart", _build)


def get_nature_chart():
    def _build():
        from .gui_entities import NatureTableWidget

        return NatureTableWidget()

    return _cached("nature_chart", _build)


def get_license():
    def _build():
        from .gui_entities import License

        return License()

    return _cached("license", _build)


def get_credits():
    def _build():
        from .gui_entities import Credits

        return Credits()

    return _cached("credits", _build)


def get_version_dialog():
    def _build():
        from .gui_entities import Version_Dialog

        return Version_Dialog()

    return _cached("version_dialog", _build)


def get_evo_window():
    win = services.evo_window
    if is_alive(win):
        return win
    from .pyobj.evolution_window import EvoWindow

    win = EvoWindow(
        logger,
        settings_obj,
        main_pokemon,
        translator,
        reviewer_obj,
        get_test_window(),
        achievements,
    )
    services.populate(evo_window=win)
    bind_runtime_globals()
    return win


def get_starter_window():
    def _build():
        from .pyobj.starter_window import StarterWindow

        return StarterWindow(logger, settings_obj)

    return _cached("starter_window", _build)


def get_item_window():
    def _build():
        from .pyobj.item_window import ItemWindow

        return ItemWindow(  # Create an instance of the MainWindow
            logger=logger,
            settings_obj=settings_obj,
            main_pokemon=main_pokemon,
            enemy_pokemon=enemy_pokemon,
            achievements=achievements,
            starter_window=get_starter_window(),
            evo_window=get_evo_window(),
        )

    return _cached("item_window", _build)


def get_pokemon_pc():
    win = services.pokemon_pc
    if is_alive(win):
        return win
    from .pyobj.pc_box import PokemonPC

    win = PokemonPC(
        logger=logger,
        translator=translator,
        reviewer_obj=reviewer_obj,
        test_window=get_test_window(),
        settings=settings_obj,
        main_pokemon=main_pokemon,
    )
    services.populate(pokemon_pc=win)
    bind_runtime_globals()
    return win


# The unified Ankimon web shell (F11/F13/F18): Items/Shop, Settings, Profile and
# Team all live in this one QDialog (one window, one dropdown navigator). Lazy +
# reload-safe like the other window factories, but not registry-backed — it is a
# pure GUI window, so it lives in its own module-level cache with an is_alive()
# liveness check (a shell whose C++ object was deleted is rebuilt, not handed out
# dead). Seam-correct: constructed from the services-resolved core objects above,
# never from mw.* (F31 keeps mw coupling out of this module, NR-04).
_items_web_window = None


def get_items_window():
    global _items_web_window
    if is_alive(_items_web_window):
        return _items_web_window
    from .ankimon_items_web.shop_obj import AnkimonItemsWeb

    _items_web_window = AnkimonItemsWeb(
        addon_dir,
        shop_manager=shop_manager,
        item_window=get_item_window(),
        ankimon_tracker=ankimon_tracker_obj,
        trainer_card=trainer_card,
        settings_obj=settings_obj,
        logger=logger,
    )
    mw.items_web_window = _items_web_window
    return _items_web_window


def notify_stats_changed():
    """Tell the open Ankimon shell that gameplay stats changed (a catch, XP
    gain, cash reward, level-up, ...) so it can live-refresh whichever screen is
    showing — no manual reload. Screen-agnostic: the shell decides what (if
    anything) to refresh based on its current screen (see
    ``AnkimonItemsWeb.refresh_live_screen`` and ``LIVE_UPDATES.md``).

    Also refreshes the player's public leaderboard entry, since the same set of
    call sites is exactly "the player's stats just changed". That push runs
    whether or not a shell window is open, so it happens before the live-screen
    early-return below.

    Pure best-effort and cheap: never creates the window, no-ops when no live
    screen is visible, no-ops when the leaderboard is switched off (the
    default), and swallows any error so a UI hiccup can't interfere with
    gameplay. Main-thread only — background callers (mobile sync worker
    threads) get neither refresh, and pick both up on their next main-thread
    notification. Call it from gameplay write chokepoints via a deferred
    ``from .singletons import notify_stats_changed`` wrapped in try/except."""
    from .utils import is_main_thread
    if not is_main_thread():
        return

    # Leaderboard push. TrainerCard.sync_leaderboard() reads the misc.leaderboard
    # opt-in before touching the database and rate limits itself, so this costs
    # a getattr for the users who never enable it, and the HTTP request itself
    # is already handed to a daemon thread by ankimon_leaderboard.
    try:
        from .services import services

        trainer_card = getattr(services, "trainer_card", None)
        if trainer_card is not None:
            trainer_card.sync_leaderboard()
    except Exception as e:
        print(f"[Ankimon] leaderboard sync from notify_stats_changed failed: {e}")

    global _items_web_window
    if not is_alive(_items_web_window):
        return
    try:
        _items_web_window.refresh_live_screen()
    except Exception as e:
        print(f"[Ankimon] notify_stats_changed failed: {e}")



# The standalone Ankidex (Pokédex V2) SPA window (F16): its own QDialog +
# QWebEngineView, opened via this factory (and reused by the web shell's inline
# Ankidex screen through get_ankidex_window().get_ankidex_data()). Lazy +
# reload-safe like the other window factories, and — like the web shell above —
# not registry-backed (a pure GUI window), so it lives in its own module-level
# cache with an is_alive() liveness check (a dialog whose C++ object was deleted
# is rebuilt, not handed out dead). Seam-correct: constructed from the
# services-resolved tracker, never from mw.* (its data getter reads
# services.db / services.settings), keeping mw coupling out of this module (NR-04).
_ankidex_window = None


def get_ankidex_window():
    global _ankidex_window
    if is_alive(_ankidex_window):
        return _ankidex_window
    from .ankidex.ankidex_obj import Ankidex

    _ankidex_window = Ankidex(addon_dir, ankimon_tracker=ankimon_tracker_obj)
    return _ankidex_window


def swap_ankimon_account():
    """Toggle between ankimon.db and ankimonDEV.db and refresh the game state.

    Developer-only (wired behind ``is_dev_mode()`` in ``menu_buttons``). Seam-refit
    from the exp original: every ``mw.*`` access routes through the service
    registry — ``services.db`` / ``services.settings`` / ``services.main_pokemon`` /
    ``services.tracker`` / ``services.trainer_card`` / ``services.reviewer`` — and
    the GUI windows through this module's own lazy caches, keeping mw coupling out
    of singletons (NR-04). ``mw.reviewer`` is a genuine Anki API, so it stays direct.
    """
    from aqt.utils import tooltip
    from .functions.update_main_pokemon import update_main_pokemon
    from .functions.encounter_functions import new_pokemon, clear_encounter_cache
    from .functions.mobile_sync import _mobile_sync_lock

    mobile_lock_acquired = False
    try:
        mobile_lock_acquired = _mobile_sync_lock.acquire(blocking=False)
        if not mobile_lock_acquired:
            tooltip("Cannot switch accounts while mobile battles are resolving.")
            return
        # services.db (or its db_path) can be None during init / in headless
        # environments; read the active name inside the try so a missing DB
        # fails gracefully into the tooltip rather than raising an uncaught
        # AttributeError before the handler below can report it.
        if services.db is None or services.db.db_path is None:
            tooltip("Ankimon database is not initialized.")
            return

        current_name = services.db.db_path.name
        new_name = "ankimonDEV.db" if current_name == "ankimon.db" else "ankimon.db"

        # Switch the DB connection.
        services.db.switch_database(new_name)

        # Reload configuration in place.
        services.settings.load_config()

        # Update the main Pokémon. update_main_pokemon() mutates the passed-in
        # object in place ONLY when the now-active DB already has a saved main;
        # for a DB with none yet (e.g. the first switch to ankimonDEV.db) it
        # returns a fresh, unrelated PokemonObject. Discarding that would leave
        # the live singleton — shared by test_window / battle_loop / pokemon_pc —
        # still showing the previous account's Pokemon, so apply it back.
        new_main, _ = update_main_pokemon(services.main_pokemon)
        if new_main is not None and new_main is not services.main_pokemon:
            services.main_pokemon.update_stats(**new_main.to_dict())

        # Refresh trainer-card data from the now-active account.
        services.trainer_card.refresh()

        # Reset battle/capture counters so no stale data can bleed through.
        services.tracker.caught = 0
        services.tracker.general_card_count_for_battle = 0

        # Sync collected IDs to the newly-active account.
        from .reviewer_ui import set_collected_ids
        from .battle_loop import init_battle_state

        new_ids = services.db.get_all_pokemon_ids()
        set_collected_ids(new_ids)
        init_battle_state(new_ids)

        # Update the mobile-reviews badge with the new database's pending count.
        try:
            from .menu_buttons import update_mobile_badge

            update_mobile_badge(services.db.get_pending_mobile_count())
        except Exception:
            pass

        # Clear the encounter-percentage cache (depends on trainer level/stats).
        clear_encounter_cache()

        # Generate a fresh encounter for the new account.
        new_pokemon(
            services.enemy_pokemon,
            services.test_window,
            services.tracker,
            services.reviewer,
        )

        # Refresh any open windows so they reflect the new account. Peek the lazy
        # caches — never construct a window just to refresh it.
        pc = services.pokemon_pc
        if is_alive(pc):
            # IDs differ between databases, so drop the stale selection.
            pc._selected_individual_id = None
            pc.pokemon_details_layout = None
            pc.refresh_gui()

        item_win = _WINDOW_CACHE.get("item_window")
        if is_alive(item_win):
            item_win.renewWidgets()

        if is_alive(_ankidex_window):
            _ankidex_window.update_ui_data()

        if is_alive(_items_web_window) and _items_web_window.isVisible():
            _items_web_window.update_ui_data()

        # If a review is in progress, force a HUD refresh (mw.reviewer = Anki).
        reviewer = getattr(mw, "reviewer", None)
        if reviewer is not None and services.reviewer is not None:
            services.reviewer.update_life_bar(reviewer, None, 0)

        tooltip(f"Switched to {new_name}")
    except Exception as e:
        tooltip(f"Failed to switch account: {e}")
        import traceback

        traceback.print_exc()
    finally:
        if mobile_lock_acquired:
            _mobile_sync_lock.release()


# DEFERRED seam points (do NOT add here in F31):
# * get_nature_chart() -> gui_entities.NatureTableWidget lands with F36 (the
#   widget does not exist on this base yet).
# * notify_stats_changed() (QWebChannel live-update push) belongs to the
#   webshell host / F10+F49, not to this module (NR-04).

# Per-name lazy proxies: `from .singletons import test_window` (and plain
# module attribute access) constructs ONLY the requested window, on first
# access. None of these names is a real module global, so this __getattr__ is
# their only resolution path.
_LAZY_WINDOWS = {
    "settings_window": get_settings_window,
    "test_window": get_test_window,
    "achievement_bag": get_achievement_bag,
    "ankimon_tracker_window": get_ankimon_tracker_window,
    "pokedex_window": get_pokedex_window,
    "eff_chart": get_eff_chart,
    "pokedex": get_pokedex_widget,
    "gen_id_chart": get_gen_id_chart,
    "nature_chart": get_nature_chart,
    "license": get_license,
    "credits": get_credits,
    "version_dialog": get_version_dialog,
    "evo_window": get_evo_window,
    "starter_window": get_starter_window,
    "item_window": get_item_window,
    "pokemon_pc": get_pokemon_pc,
}


def __getattr__(name):
    factory = _LAZY_WINDOWS.get(name)
    if factory is not None:
        return factory()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# --- Qt UI presenter + runtime-global binding. --------------------------------
# Idempotent: only swap in a QtPresenter when the registry does not already
# hold one, so a reload keeps the existing presenter instance.
if not isinstance(services.ui, QtPresenter):
    services.populate(ui=QtPresenter())

# Bind the core logic modules' bare globals (main_pokemon, settings_obj,
# test_window, …) to the now-populated registry. The window bindings
# (test_window / evo_window / pokemon_pc) may still be None here — each lazy
# factory re-runs bind_runtime_globals() after registering its window, so the
# bindings are live before any gameplay code can run.
bind_runtime_globals()
