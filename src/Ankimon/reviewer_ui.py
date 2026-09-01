from anki.hooks import wrap
from aqt.reviewer import Reviewer
from aqt.utils import downArrow, tooltip, tr

from .services import services
from .singletons import (
    enemy_pokemon,
    main_pokemon,
    ankimon_tracker_obj,
    get_test_window,
    get_evo_window,
    logger,
    achievements,
    trainer_card,
    reviewer_obj,
)
from .functions.encounter_functions import (
    catch_pokemon,
    get_auto_battle_setting,
    kill_pokemon,
    new_pokemon,
    toggle_auto_battle_override,
)
from .texts import _bottomHTML_template, button_style

# Developer-mode gate (detects "dev_"/"_dev" in the profile or trainer name).
# Guards the dev-only test-encounter hotkey ('0'); default-off so it stays hidden.
from .utils import is_dev_mode


_collected_pokemon_ids = set()

# === TEAM CYCLING STATE ===
_team_cycle_index = 0
_team_cycle_pokemon_ids = []  # cache of the first N team member ids


def _team_cycle_count():
    """Rotation size from settings (default 3), via the service seam."""
    try:
        return int(services.settings.get("controls.team_cycle_count", 3))
    except Exception:
        return 3


def get_team_pokemon_list():
    """Load the first N team member individual_ids from the DB (seam: services.db)."""
    global _team_cycle_pokemon_ids
    try:
        db = services.db
        team_data = db.get_team()  # list of dicts with 'individual_id'
        cycle_count = _team_cycle_count()
        _team_cycle_pokemon_ids = [
            entry.get("individual_id")
            for entry in team_data[:cycle_count]
            if entry.get("individual_id")
        ]
        return _team_cycle_pokemon_ids
    except Exception as e:
        print(f"Error loading team pokemon from database: {e}")
        return []


def get_pokemon_from_collection(individual_id):
    """Load a full pokemon record from the DB by individual_id (seam: services.db)."""
    try:
        db = services.db
        return db.get_pokemon(individual_id)
    except Exception as e:
        print(f"Error loading pokemon {individual_id} from database: {e}")
        return None


def cycle_team_pokemon():
    """Cycle the active (main) pokemon to the next team slot and repaint the HUD."""
    global _team_cycle_index, _team_cycle_pokemon_ids

    try:
        from .functions.update_main_pokemon import save_main_pokemon

        cycle_count = _team_cycle_count()
        if cycle_count <= 1:
            tooltip("Team cycling is disabled (set rotation limit > 1 in Team Select)")
            return

        team_ids = get_team_pokemon_list()
        if not team_ids or len(team_ids) < 2:
            tooltip("Not enough team members to cycle (need at least 2)")
            return

        # Sync the cycle index to the active pokemon's slot so a swap made
        # outside this hotkey (Team Select / PC) still advances to the member
        # *after* the current one. Fall back to the running index (with a bounds
        # check) when the active pokemon is not among the cycled team slots.
        active_id = getattr(main_pokemon, "individual_id", None)
        try:
            _team_cycle_index = team_ids.index(active_id)
        except ValueError:
            if _team_cycle_index >= len(team_ids):
                _team_cycle_index = 0

        _team_cycle_index = (_team_cycle_index + 1) % len(team_ids)
        next_id = team_ids[_team_cycle_index]

        pokemon_data = get_pokemon_from_collection(next_id)
        if not pokemon_data or not pokemon_data.get("id"):
            tooltip("Invalid pokemon data")
            return

        try:
            from .functions.pokedex_functions import (
                search_pokedex_by_id,
                search_pokedex,
            )

            pokemon_name = pokemon_data.get("name")
            if not pokemon_name:
                pokemon_name = search_pokedex_by_id(pokemon_data.get("id", 1))
                pokemon_data["name"] = pokemon_name

            # search_pokedex returns [] (not None) when the name is unknown.
            # base_stats must be a dict with an "hp" key, or update_stats ->
            # calculate_max_hp would raise on base_stats["hp"] AFTER partially
            # mutating main_pokemon. Guard before touching main_pokemon.
            base_stats = search_pokedex(pokemon_name, "baseStats")
            if not isinstance(base_stats, dict) or "hp" not in base_stats:
                tooltip(f"Error: base stats not found for {pokemon_name}")
                return
            pokemon_data["base_stats"] = base_stats

            main_pokemon.update_stats(**pokemon_data)
            main_pokemon.max_hp = main_pokemon.calculate_max_hp()
            main_pokemon.hp = main_pokemon.max_hp
            main_pokemon.reset_bonuses()

            save_main_pokemon(main_pokemon)
        except Exception as e:
            print(f"Error updating pokemon: {e}")
            tooltip(f"Error switching pokemon: {e}")
            return

        # Repaint immediately through the HUD's single repaint entry point.
        try:
            reviewer_obj.refresh_hud()
        except Exception as e:
            print(f"Error updating HUD: {e}")

        pkmn_name = pokemon_data.get("nickname") or pokemon_data.get("name", "Unknown")
        pkmn_level = pokemon_data.get("level", "?")
        # main_pokemon now holds the switched-in Pokémon (update_stats above), so
        # its display_name gives the localized species / regional-form name and
        # honours a genuinely custom nickname.
        display_name = getattr(main_pokemon, "display_name", None) or pkmn_name
        try:
            switch_msg = services.translator.translate(
                "switched_pokemon", pokemon_name=display_name, level=pkmn_level
            )
        except Exception:
            switch_msg = f"Switched to {display_name}! Lv. {pkmn_level}"

        # The HUD refresh above only repaints the bottom-of-reviewer bars —
        # the separate Ankimon Window popup has its own sprite/name label and
        # message box, and was never told a swap happened, so it kept showing
        # the pokemon (and battle text) from before the cycle. Routes the
        # switch message into the window's own message box rather than
        # leaving it to the floating Anki tooltip below — with the window
        # open the two used to visually overlap/compete for the same
        # on-screen space.
        shown_in_window = False
        try:
            from .functions.drawing_utils import show_in_ankimon_window

            shown_in_window = show_in_ankimon_window(switch_msg)
        except Exception as e:
            print(f"Error updating Ankimon Window: {e}")

        # Only float the tooltip when the window did NOT take the message —
        # otherwise the same sentence appears twice at once, which is the
        # overlap show_in_ankimon_window() exists to remove.
        if not shown_in_window:
            tooltip(switch_msg)

    except Exception as e:
        print(f"Unexpected error: {e}")
        import traceback

        traceback.print_exc()
        tooltip(f"Error: {str(e)}")


def set_collected_ids(ids):
    global _collected_pokemon_ids
    _collected_pokemon_ids = ids


def catch_shortcut_function():
    if enemy_pokemon.hp < 1:
        catch_pokemon(
            enemy_pokemon,
            ankimon_tracker_obj,
            logger,
            "",
            _collected_pokemon_ids,
            achievements,
        )
        new_pokemon(
            enemy_pokemon,
            get_test_window(),
            ankimon_tracker_obj,
            reviewer_obj,
            update_hud=True,
        )
    else:
        if get_auto_battle_setting(services.settings) == 0:
            # Auto-battle disabled - show the original message
            tooltip("You only catch a pokemon once it's fainted!")
        else:
            # Auto-battle enabled - toggle override
            toggle_auto_battle_override("catch")


def defeat_shortcut_function():
    if enemy_pokemon.hp < 1:
        kill_pokemon(
            main_pokemon,
            enemy_pokemon,
            get_evo_window(),
            logger,
            achievements,
            trainer_card,
        )
        new_pokemon(
            enemy_pokemon,
            get_test_window(),
            ankimon_tracker_obj,
            reviewer_obj,
            update_hud=True,
        )
    else:
        if get_auto_battle_setting(services.settings) == 0:
            # Auto-battle disabled - show the original message
            tooltip("Wild pokemon has to be fainted to defeat it!")
        else:
            # Auto-battle enabled - toggle override
            toggle_auto_battle_override("defeat")


def team_cycle_shortcut_function():
    """Callback for the team-cycle hotkey."""
    cycle_team_pokemon()


def test_encounter_shortcut_function():
    """Dev-only hotkey (0): trigger a new pokemon encounter immediately."""
    if is_dev_mode():
        new_pokemon(
            enemy_pokemon,
            get_test_window(),
            ankimon_tracker_obj,
            reviewer_obj,
            update_hud=True,
        )
        tooltip("New encounter triggered (Test Hotkey 0)")


# Module-level key storage so the wrapped Reviewer methods read the current keys
# at call time; re-running setup_reviewer_ui() only refreshes these.
_current_keys = {"catch": "6", "defeat": "5", "team_cycle": "9"}
_original_shortcutkeys_wrapped = False
_ui_hooks_installed = False


def _resolve_team_cycle_key(team_cycle_shortcut):
    """4th-arg default: read controls.team_cycle_key via services.settings.

    Lets base's unmodified 3-arg setup_reviewer_ui() call keep working (the key
    comes from settings) while the settings-aware 4-arg call passes it explicitly.
    """
    if team_cycle_shortcut is not None:
        return str(team_cycle_shortcut).lower()
    try:
        return str(services.settings.get("controls.team_cycle_key", "9")).lower()
    except Exception:
        return "9"


def setup_reviewer_ui(
    catch_shortcut: str,
    defeat_shortcut: str,
    reviewer_buttons: bool,
    team_cycle_shortcut: str = None,
):
    """Install reviewer hotkeys / bottom-bar buttons.

    Idempotent: the Reviewer method wrapping happens once and stores the pristine
    methods as ``Reviewer._ankimon_orig_*`` (the reload-teardown contract shared
    with the reloader). Repeat calls (e.g. saving settings) only refresh
    ``_current_keys`` — the wrappers read those at call time, so there is no
    re-wrapping and no double-wrap of ``Reviewer._shortcutKeys``.
    """
    global _current_keys, _original_shortcutkeys_wrapped, _ui_hooks_installed

    _current_keys["catch"] = str(catch_shortcut).lower()
    _current_keys["defeat"] = str(defeat_shortcut).lower()
    _current_keys["team_cycle"] = _resolve_team_cycle_key(team_cycle_shortcut)

    if not _original_shortcutkeys_wrapped:
        # Reload-teardown contract: capture the pristine method once, and always
        # wrap the pristine version — never an already-wrapped copy. A module
        # re-exec (add-on reload) resets _original_shortcutkeys_wrapped while the
        # Reviewer class persists with the previous boot's wrapper installed, so
        # restore the pristine first; otherwise wrap() below would stack a second
        # layer and every Ankimon shortcut would fire twice.
        if hasattr(Reviewer, "_ankimon_orig_shortcutKeys"):
            Reviewer._shortcutKeys = Reviewer._ankimon_orig_shortcutKeys
        else:
            Reviewer._ankimon_orig_shortcutKeys = Reviewer._shortcutKeys

        def _shortcutKeys_wrap(self, _old):
            original = _old(self)
            # These lambdas read _current_keys at CALL time, not definition time.
            original.append((_current_keys["catch"], lambda: catch_shortcut_function()))
            original.append(
                (_current_keys["defeat"], lambda: defeat_shortcut_function())
            )
            original.append(
                (_current_keys["team_cycle"], lambda: team_cycle_shortcut_function())
            )
            if is_dev_mode():
                original.append(("0", lambda: test_encounter_shortcut_function()))
            return original

        Reviewer._shortcutKeys = wrap(
            Reviewer._shortcutKeys, _shortcutKeys_wrap, "around"
        )
        _original_shortcutkeys_wrapped = True

    if reviewer_buttons is True and not _ui_hooks_installed:

        def _linkHandler_wrap(self, url, _old):
            if url == "catch":
                catch_shortcut_function()
                return True
            elif url == "defeat":
                defeat_shortcut_function()
                return True
            elif url == "team_cycle":
                team_cycle_shortcut_function()
                return True
            else:
                return _old(self, url)

        def _bottomHTML_wrap(self, _old):
            return _bottomHTML_template % dict(
                edit=tr.studying_edit(),
                editkey=tr.actions_shortcut_key(val="E"),
                more=tr.studying_more(),
                morekey=tr.actions_shortcut_key(val="M"),
                downArrow=downArrow(),
                time=self.card.time_taken() // 1000,
                CatchKey=tr.actions_shortcut_key(val=f"{_current_keys['catch']}"),
                DefeatKey=tr.actions_shortcut_key(val=f"{_current_keys['defeat']}"),
                TeamCycleKey=tr.actions_shortcut_key(
                    val=f"{_current_keys['team_cycle']}"
                ),
            )

        # Reload-teardown contract (same as _shortcutKeys above): capture the
        # pristine methods once and always wrap the pristine version. On a module
        # re-exec the Reviewer class keeps the previous wrappers, so restore the
        # originals before re-wrapping to avoid stacking a second layer.
        if hasattr(Reviewer, "_ankimon_orig_linkHandler"):
            Reviewer._linkHandler = Reviewer._ankimon_orig_linkHandler
        else:
            Reviewer._ankimon_orig_linkHandler = Reviewer._linkHandler
        if hasattr(Reviewer, "_ankimon_orig_bottomHTML"):
            Reviewer._bottomHTML = Reviewer._ankimon_orig_bottomHTML
        else:
            Reviewer._ankimon_orig_bottomHTML = Reviewer._bottomHTML

        Reviewer._linkHandler = wrap(Reviewer._linkHandler, _linkHandler_wrap, "around")
        Reviewer._bottomHTML = wrap(Reviewer._bottomHTML, _bottomHTML_wrap, "around")
        _ui_hooks_installed = True
