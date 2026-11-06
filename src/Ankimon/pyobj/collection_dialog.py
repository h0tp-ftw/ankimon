import json
from collections import defaultdict
import uuid

from aqt.utils import showInfo, showWarning
from ..pyobj.error_handler import show_warning_with_traceback
from PyQt6.QtWidgets import *
from PyQt6.QtGui import *
from PyQt6.QtCore import *
from aqt import mw
import re

from ..pyobj.InfoLogger import ShowInfoLogger
from ..pyobj.pokemon_obj import PokemonObject
from ..pyobj.InfoLogger import ShowInfoLogger
from ..pyobj.translator import Translator
from ..pyobj.test_window import TestWindow
from ..pyobj.reviewer_obj import Reviewer_Manager
from ..functions.pokedex_functions import search_pokedex, search_pokedex_by_id
from ..pyobj.database_manager import canonicalize_pokemon_record


def _resolve_persisted_hp(pokemon_data):
    """Return the saved current HP, preserving zero and rejecting malformed data.

    Delegates to ``canonicalize_pokemon_record(..., allow_legacy_hp=True)`` so
    this switch path applies the SAME precedence the migration and the DB read
    path do: a valid ``current_hp`` wins, a valid legacy ``hp`` is the fallback,
    a malformed ``current_hp`` falls back to a valid ``hp`` (not to max HP), and
    a record carrying neither yields ``None`` -- which lets ``PokemonObject``
    fall back to ``max_hp`` only when there is genuinely no persisted value.

    Reading only ``current_hp`` here (the old behaviour) is what full-healed a
    wounded main on switch: a legacy row with ``hp: 7`` and no ``current_hp``
    returned ``None``, the constructor got no HP, and ``_normalize_hp(None,
    max_hp, max_hp)`` produced the max (26).
    """
    if not isinstance(pokemon_data, dict):
        return None
    resolved = canonicalize_pokemon_record(
        pokemon_data, allow_legacy_hp=True
    ).get("current_hp")
    if resolved is None or isinstance(resolved, bool):
        return None
    try:
        return int(resolved)
    except (TypeError, ValueError, OverflowError):
        return None


def MainPokemon(
    pokemon_data: dict,
    main_pokemon: PokemonObject,
    logger: ShowInfoLogger,
    translator: Translator,
    reviewer_obj: Reviewer_Manager,
    test_window: TestWindow,
):
    from ..functions.migration import migrate_starter_individual_id
    from ..battle_loop import (
        _cancel_main_faint_deferral,
        _main_faint_pending_for,
    )

    # Re-selecting the active companion while both Pokémon are fainted is not
    # a switch. Reloading its saved row can overwrite the live 0 HP, and
    # cancelling the deferral would strand the pending enemy choice without
    # the main's faint recovery. Keep this selection inert until that choice.
    if _main_faint_pending_for(main_pokemon, pokemon_data.get("individual_id")):
        return

    db = mw.ankimon_db

    # Save combat HP without replacing newer stored moves, rewards, or metadata.
    try:
        if (
            main_pokemon is not None
            and getattr(main_pokemon, "individual_id", None)
            and db.get_main_pokemon()
        ):
            if not db.update_pokemon_current_hp(
                str(main_pokemon.individual_id), main_pokemon.current_hp
            ):
                raise RuntimeError("The outgoing Pokémon was not saved")
    except Exception as exc:
        # Do not switch away from an active Pokémon whose latest state was not saved.
        # Two lines on purpose: the exception text is diagnostic, and dropping a
        # raw repr into a message box tells the player nothing they can act on.
        # Keep it in the log file and show a message that names the Pokemon and
        # says what to check.
        logger.log("error", f"Could not save outgoing main pokemon: {exc}")
        logger.log_and_showinfo(
            "error",
            f"Could not save {getattr(main_pokemon, 'name', 'your previous main')} "
            "before switching, so the switch was cancelled. Recent progress may "
            "be lost. See the Ankimon log for details.",
        )
        return

    # The PC read this snapshot before the outgoing save above. Re-selecting
    # the same individual must retain its live HP when a busy HUD save had
    # left the snapshot behind, while still refreshing its stored moves.
    if pokemon_data.get("individual_id") and str(pokemon_data["individual_id"]) == str(
        getattr(main_pokemon, "individual_id", None)
    ):
        pokemon_data = {**pokemon_data, "current_hp": main_pokemon.current_hp}

    # --- Now proceed to set the new mainpokemon as before ---
    pokemon_id = pokemon_data.get("id")
    pokemon_name = search_pokedex_by_id(pokemon_id)
    base_stats = search_pokedex(pokemon_name, "baseStats")
    # HP: hand the constructor the PERSISTED value and let PokemonObject
    # normalize it. Passing a freshly computed max-HP stat here (the old
    # behaviour) silently full-healed the incoming main and, via
    # db.save_main_pokemon(to_dict()) below, persisted that full HP.
    #
    # Legacy rows are normalized by AnkimonDB before reaching this switch path.
    persisted_hp = _resolve_persisted_hp(pokemon_data)

    # Create NEW PokemonObject instance using class constructor
    new_main_pokemon = PokemonObject(
        name=pokemon_name,
        level=pokemon_data.get("level", 5),
        ability=pokemon_data.get("ability", ["none"]),
        type=pokemon_data.get("type", ["Normal"]),
        base_stats=base_stats,
        ev=pokemon_data.get("ev", defaultdict(int)),
        iv=pokemon_data.get("iv", defaultdict(int)),
        attacks=pokemon_data.get("attacks", ["Struggle"]),
        base_experience=pokemon_data.get("base_experience", 0),
        growth_rate=pokemon_data.get("growth_rate", "medium"),
        # ``or`` not a get() default: a record holding an explicit null nature
        # would otherwise reach get_nature_stat_mult() and blow up on None.lower().
        nature=pokemon_data.get("nature") or "serious",
        # Keep runtime battle HP aligned with the persisted current_hp.
        hp=persisted_hp,
        current_hp=persisted_hp,
        gender=pokemon_data.get("gender", "N"),
        shiny=pokemon_data.get("shiny", False),
        individual_id=pokemon_data.get("individual_id", str(uuid.uuid4())),
        id=pokemon_data.get("id", 133),
        volatile_status=set(pokemon_data.get("volatile_status", [])),
        xp=pokemon_data.get("xp", 0),
        nickname=pokemon_data.get("nickname", ""),
        # Add common extra fields if constructor supports them
        friendship=pokemon_data.get("friendship", 0),
        pokemon_defeated=pokemon_data.get("pokemon_defeated", 0),
        everstone=pokemon_data.get("everstone", False),
        evolution_rejected=pokemon_data.get("evolution_rejected", False),
        mega=pokemon_data.get("mega", False),
        special_form=pokemon_data.get("special_form", None),
        tier=pokemon_data.get("tier", None),
        captured_date=pokemon_data.get("captured_date", None),
        is_favorite=pokemon_data.get("is_favorite", False),
        held_item=pokemon_data.get("held_item"),
    )
    # ``mega`` and ``special_form`` are the only fields the constructor does not
    # assign -- they fall into **kwargs and are dropped -- so they have to be set
    # here. Set them UNCONDITIONALLY: __dict__.update() below cannot clear an
    # attribute that new_main_pokemon does not carry, so skipping the assignment
    # for a record that lacks the key leaves the OUTGOING Pokemon's value on the
    # object and save_main_pokemon() then writes it onto the INCOMING Pokemon's
    # row. The six other fields this loop used to copy are all real constructor
    # parameters, already assigned above from the same pokemon_data.
    new_main_pokemon.mega = pokemon_data.get("mega", False)
    new_main_pokemon.special_form = pokemon_data.get("special_form", None)

    # Commit the incoming persisted record before mutating the shared singleton.
    try:
        if not db.save_main_pokemon(new_main_pokemon.to_dict()):
            raise RuntimeError("The incoming Pokémon was not saved as main")
    except Exception as exc:
        logger.log("error", f"Could not switch the main Pokémon: {exc}")
        logger.log_and_showinfo(
            "error", "Could not save the selected Pokémon as main; switch cancelled."
        )
        return

    _cancel_main_faint_deferral()
    main_pokemon.__dict__.update(new_main_pokemon.__dict__)

    logger.log_and_showinfo(
        "info",
        translator.translate(
            "picked_main_pokemon",
            main_pokemon_name=getattr(
                main_pokemon, "display_name", main_pokemon.name.capitalize()
            ),
        ),
    )

    # Update UI components. refresh_hud() is Reviewer_Manager's single entry
    # point for a repaint: it builds the reviewer shim itself and is guarded, so
    # no reviewer/webview is a silent no-op rather than an AttributeError on
    # mw.reviewer.web. No extra bookkeeping is needed here:
    # reviewer_obj.main_pokemon IS the object mutated in place above, and
    # db.save_main_pokemon() already invalidated the HUD cache via
    # _clear_reviewer_ownership_cache().
    reviewer_obj.refresh_hud()

    if test_window.isVisible():
        test_window.display_first_encounter()

    from ..singletons import pokemon_pc

    pokemon_pc.refresh_pokemon_grid()
