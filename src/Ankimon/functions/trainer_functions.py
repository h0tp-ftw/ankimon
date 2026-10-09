import json
import random
from .badges_functions import get_achieved_badges
from .pokemon_functions import find_experience_for_level
from .pokedex_functions import check_evolution_for_pokemon, return_name_for_id
from .friendship_evolution import check_friendship_evolution_for_pokemon
from .learnset_retrieval import get_levelup_move_for_pokemon
from .drawing_utils import tooltipWithColour
from ..move_names import format_move_name
from ..services import services

MAX_MOVES = 4
LEVEL_UP_COLOR = "#6A4DAC"


def _in_bulk_resolve() -> bool:
    """Report whether the mobile-sync bulk resolver is replaying reviews.

    Mirrors ``encounter_functions._in_bulk_resolve``; no dialog may open
    while a backlog is being replayed.

    Returns
    -------
    bool
        True while ``utils.in_bulk_resolve`` is set, False otherwise
        (including when ``utils`` cannot be imported headless).
    """
    try:
        from .. import utils
    except Exception:
        return False

    return bool(getattr(utils, "in_bulk_resolve", False))


def _learn_levelup_moves(logger, pokemon, start_level, end_level):
    """Teach an XP Share recipient the moves of every level it just gained.

    Mirrors the active Pokemon's level-up in ``encounter_functions``, with one
    difference: a recipient can jump several levels off a single win, so all
    new moves are collected first. Free slots are filled silently; if moves
    remain and the set is full, the user gets ONE ``choose_moveset`` prompt
    for this Pokemon rather than one per move. Declined moves stay available
    through Remember Attacks.

    Parameters
    ----------
    logger : ShowInfoLogger
        Logger used for the learn / discard messages.
    pokemon : dict
        Stored Pokemon record; ``pokemon["attacks"]`` is updated in place.
    start_level : int
        Level before the XP was applied (exclusive).
    end_level : int
        Level after the XP was applied (inclusive).

    Returns
    -------
    None
    """
    attacks = pokemon.get("attacks") or []
    if isinstance(attacks, str):
        try:
            attacks = json.loads(attacks)
        except Exception:
            attacks = []
    attacks = list(attacks)
    display_name = str(pokemon.get("name", "")).capitalize()

    # A learnset failure must never block the XP reward saved after this.
    try:
        new_attacks = _collect_new_levelup_moves(
            str(pokemon.get("name", "")).lower(), attacks, start_level, end_level
        )
    except Exception as error:
        logger.log(
            "error", f"Could not look up level-up moves for {display_name}: {error}"
        )
        return
    if not new_attacks:
        return

    learned = []
    while new_attacks and len(attacks) < MAX_MOVES:
        move = new_attacks.pop(0)
        attacks.append(move)
        learned.append(move)
    if learned:
        msg = _translate(
            "mainpokemon_learned_new_attack",
            "Your {main_pokemon_name} learned {new_attack_name} !",
            main_pokemon_name=display_name,
            new_attack_name=", ".join(format_move_name(m) for m in learned),
        )
        logger.log("info", msg)
        if not _in_bulk_resolve():
            tooltipWithColour(msg, LEVEL_UP_COLOR)

    if new_attacks:
        if _in_bulk_resolve():
            # Never pop a dialog while replaying a bulk backlog; the moves are
            # still reachable via Remember Attacks.
            logger.log(
                "info",
                f"[Bulk Resolve] Discarded learning new moves {new_attacks} on {display_name}.",
            )
        else:
            attacks = _choose_moveset_or_keep(logger, display_name, attacks, new_attacks)

    pokemon["attacks"] = attacks


def _normalize_move_id(move):
    """Normalise a move id the way the move-evolution gate does.

    Parameters
    ----------
    move : str
        Raw move id or display name.

    Returns
    -------
    str
        Lower-cased id with spaces and hyphens removed.
    """
    return str(move).lower().replace(" ", "").replace("-", "")


def _collect_new_levelup_moves(name, attacks, start_level, end_level):
    """Gather the moves learned on each gained level, in level order.

    Parameters
    ----------
    name : str
        Lower-cased species name used for the learnset lookup.
    attacks : list of str
        Moves the Pokemon already knows; these are never offered again.
    start_level : int
        Level before the XP was applied (exclusive).
    end_level : int
        Level after the XP was applied (inclusive).

    Returns
    -------
    list of str
        Raw move ids not yet known, first occurrence kept, ascending by level.
    """
    known = {_normalize_move_id(m) for m in attacks}
    new_attacks = []
    for level in range(start_level + 1, end_level + 1):
        for move in get_levelup_move_for_pokemon(name, level):
            key = _normalize_move_id(move)
            if key not in known:
                known.add(key)
                new_attacks.append(move)
    return new_attacks


def _translate(key, fallback, **kwargs):
    """Translate ``key`` through ``services.translator`` when one is wired.

    Parameters
    ----------
    key : str
        Translation key from the ``lang/*_text.json`` files.
    fallback : str
        English template used headless or when the translator fails.
    **kwargs
        Placeholders substituted into the template.

    Returns
    -------
    str
    """
    translator = getattr(services, "translator", None)
    if translator is not None:
        try:
            return translator.translate(key, **kwargs)
        except Exception:
            pass
    return fallback.format(**kwargs)


def _apply_moveset_choice(attacks, new_attacks, chosen):
    """Validate a ``choose_moveset`` answer and lay it out slot by slot.

    Parameters
    ----------
    attacks : list of str
        The current (full) moveset that was offered.
    new_attacks : list of str
        The new candidates that were offered, in level order.
    chosen : object
        Whatever the presenter returned.

    Returns
    -------
    list of str or None
        The new moveset, with retained moves keeping their slots and the
        selected new moves filling the vacated slots in candidate order;
        None if ``chosen`` is not exactly ``MAX_MOVES`` unique ids from the
        offered pool.
    """
    if not isinstance(chosen, (list, tuple)):
        return None
    chosen = list(chosen)
    pool = set(attacks) | set(new_attacks)
    if len(chosen) != MAX_MOVES or len(set(chosen)) != MAX_MOVES:
        return None
    if not set(chosen) <= pool:
        return None
    incoming = [m for m in new_attacks if m in chosen]
    result = []
    for move in attacks:
        if move in chosen:
            result.append(move)
        elif incoming:
            result.append(incoming.pop(0))
    result.extend(incoming)
    return result[:MAX_MOVES]


def _choose_moveset_or_keep(logger, display_name, attacks, new_attacks):
    """Ask the presenter for a moveset; on cancel, error or bad answer keep ``attacks``.

    Parameters
    ----------
    logger : ShowInfoLogger
        Logger for the outcome message.
    display_name : str
        Capitalised Pokemon name for messages and the dialog.
    attacks : list of str
        The current (full) moveset.
    new_attacks : list of str
        New candidates that found no free slot.

    Returns
    -------
    list of str
        The moveset to store.
    """
    try:
        chosen = services.ui.choose_moveset(display_name, attacks, new_attacks)
    except Exception as error:
        # The reward is still saved by the caller; only the prompt is lost.
        logger.log(
            "error",
            f"Moveset prompt failed for {display_name}: {error}; keeping current moves.",
        )
        return attacks
    if chosen is None:
        logger.log(
            "info",
            f"{display_name} did not learn {new_attacks}; use Remember Attacks to teach them later.",
        )
        return attacks
    applied = _apply_moveset_choice(attacks, new_attacks, chosen)
    if applied is None:
        logger.log(
            "warning",
            f"Ignoring invalid moveset choice {chosen!r} for {display_name}; keeping current moves.",
        )
        return attacks
    logger.log("info", f"{display_name}'s moves are now {applied}")
    return applied


def find_trainer_rank(highest_level, trainer_level):
    """
    Determines the Pokémon rank based on the player's achievements like Pokémon caught (from Pokedex),
    highest level Pokémon, trainer XP, trainer level, shiny Pokémon count, and badges.

    Args:
    highest_level (int): The highest level Pokémon the player owns.
    trainer_level (int): The level of the trainer.

    Returns:
    str: The Pokémon rank (Grand Champion, Champion, Elite, Veteran, Rookie, etc.).
    """
    try:
        # Count the amount of Pokémon caught based on the Pokedex
        caught_pokemon = services.db.execute(
            "SELECT COUNT(DISTINCT pokedex_id) FROM captured_pokemon"
        ).fetchone()[0]

        # Count the number of shiny Pokémon
        shiny_pokemon_count = services.db.get_shiny_count()

        # Count badges
        badge_count = len(get_achieved_badges())

        # Determine rank based on achievements
        if (
            caught_pokemon >= 900
            and highest_level >= 99
            and trainer_level >= 100
            and shiny_pokemon_count >= 50
        ):
            rank = "Legendary Trainer"
        elif (
            caught_pokemon >= 800
            and highest_level >= 95
            and trainer_level >= 80
            and shiny_pokemon_count >= 25
        ):
            rank = "Grand Champion"
        elif (
            caught_pokemon >= 700
            and highest_level >= 90
            and trainer_level >= 70
            and shiny_pokemon_count >= 20
        ):
            rank = "Champion"
        elif (
            caught_pokemon >= 600
            and highest_level >= 80
            and trainer_level >= 60
            and shiny_pokemon_count >= 10
            and badge_count >= 8
        ):
            rank = "Master Trainer"
        elif (
            caught_pokemon >= 500
            and highest_level >= 75
            and trainer_level >= 50
            and shiny_pokemon_count >= 5
            and badge_count > 6
        ):
            rank = "Elite"
        elif (
            caught_pokemon >= 400
            and highest_level >= 70
            and trainer_level >= 45
            and shiny_pokemon_count >= 3
            and badge_count > 5
        ):
            rank = "Elite Trainer"
        elif (
            caught_pokemon >= 350
            and highest_level >= 60
            and trainer_level >= 40
            and shiny_pokemon_count >= 2
            and badge_count > 4
        ):
            rank = "Advanced Trainer"
        elif (
            caught_pokemon >= 300
            and highest_level >= 50
            and trainer_level >= 30
            and shiny_pokemon_count > 0
            and badge_count > 3
        ):
            rank = "Veteran"
        elif (
            caught_pokemon >= 250
            and highest_level >= 40
            and trainer_level >= 20
            and shiny_pokemon_count > 0
        ):
            rank = "Skilled Trainer"
        elif caught_pokemon >= 150 and highest_level >= 30 and trainer_level >= 10:
            rank = "Rookie"
        else:
            rank = "Novice Trainer"  # Default rank for beginners

        return rank

    except FileNotFoundError:
        print("Error: One of the files (Pokedex or MyPokemon) could not be found.")
        return "Unknown Rank"


def _grant_xp_to_pokemon(logger, settings_obj, evo_window, individual_id, exp):
    """Apply XP and uncapped friendship to one stored Pokémon by individual_id.

    Save level-ups and rewards before evolution prompts. Shared by both
    XP Share modes below (classic grants this to one chosen holder; ORAS
    grants it to every other team member). Returns False if the Pokémon
    no longer exists (released/traded since it was selected/added to the
    team), True otherwise."""
    db = services.db
    remove_level_cap = settings_obj.get("misc.remove_level_cap")

    msg = ""
    evolution_triggered = False

    pokemon = db.get_pokemon(individual_id)
    if pokemon is None:
        return False
    # Classic XP Share can round a one-point reward down to zero. A recipient
    # that earns no XP must not gain friendship or trigger an evolution.
    if exp <= 0:
        return True

    current_level = int(pokemon["level"])  # MODIFIED: Use local variable for level
    if pokemon.get("held_item") == "lucky-egg":
        exp = int(exp * 1.5)  # Multiply by 1.5 if pokemon holds lucky egg
        msg += f"{pokemon['name']}'s Lucky Egg boosts its XP gained!\n"
    current_xp = pokemon.get("xp") or pokemon.get("stats", {}).get("xp", 0)
    growth_rate = pokemon["growth_rate"]  # MODIFIED: Use local variable for growth rate
    experience_needed = int(
        find_experience_for_level(growth_rate, current_level, remove_level_cap)
    )  # MODIFIED: Pre-calculate needed XP
    evo_id = None  # Initialize variable

    levels_gained = 0
    logger.log("info", "Running XP share function")
    if experience_needed > exp + current_xp:
        pokemon["xp"] = current_xp + exp
    else:
        while exp + current_xp > experience_needed:
            if remove_level_cap or current_level < 100:
                if levels_gained >= 10:
                    logger.log(
                        "error",
                        f"XP Share level-up loop exceeded safety cap of 10 for {pokemon['name']}",
                    )
                    exp = max(0, experience_needed - 1)
                    current_xp = 0
                    break
                levels_gained += 1
                current_level += 1
                exp = exp + current_xp - experience_needed
                current_xp = 0
                experience_needed = int(
                    find_experience_for_level(
                        growth_rate, current_level, remove_level_cap
                    )
                )  # MODIFIED: Recalculate needed XP
                msg += f"XP increased for {pokemon['name']} with level {current_level} and XP {exp}\n"
            else:
                break
        pokemon["level"] = current_level
        pokemon["xp"] = 0 if exp < 0 else exp
        # Learn moves before the save and the evolution check below, so the
        # new moves persist with the level-up and a move-based evolution sees
        # them on this very level (same ordering as the active Pokemon).
        if levels_gained > 0:
            _learn_levelup_moves(
                logger, pokemon, current_level - levels_gained, current_level
            )

    # Passive XP Share earns less friendship than battling; neither is capped.
    friendship_gain = random.randint(1, 2)
    if pokemon.get("held_item") == "soothe-bell":
        friendship_gain = int(friendship_gain * 1.5)
    pokemon["friendship"] = pokemon.get("friendship", 0) + friendship_gain

    # Prompts read the stored record and may synchronously evolve it. Commit
    # progress first, so a failed prompt cannot lose the reward and a completed
    # evolution cannot be overwritten with this pre-evolution snapshot.
    db.save_pokemon(pokemon)

    # Check for evolution
    evo_id = check_evolution_for_pokemon(
        pokemon["individual_id"],
        pokemon["id"],
        pokemon["level"],
        evo_window,
        pokemon.get("everstone", False),
        pokemon.get("evolution_rejected", False),
        current_attacks=pokemon.get("attacks"),
        gender=pokemon.get("gender"),
    )

    if evo_id is not None:
        evo_disp_name = return_name_for_id(evo_id)
        evo_disp_name = evo_disp_name.capitalize() if evo_disp_name else str(evo_id)
        msg += f"{pokemon['name']} is about to evolve to {evo_disp_name} at level {pokemon['level']}"
        evolution_triggered = True

    # Secondary friendship/time-of-day evolution check. Only fires if the level
    # check above did not already prompt an evolution (avoids double-prompting).
    if not evolution_triggered and evo_window is not None:
        try:
            friendship_evo_id = check_friendship_evolution_for_pokemon(
                pokemon["individual_id"],
                pokemon["id"],
                evo_window,
                pokemon.get("everstone", False),
                pokemon.get("friendship", 0),
                pokemon.get("evolution_rejected", False),
                # Both values are already loaded in `pokemon`; passing them
                # avoids a synchronous DB read on the XP-share review path.
                attacks=pokemon.get("attacks"),
                pokemon_defeated=pokemon.get("pokemon_defeated", 0),
            )
        except RuntimeError as error:
            # A deleted Qt window cannot prompt, but its recipient's reward is
            # already committed. Continue distributing ORAS rewards to the
            # rest of the team; a later victory can offer this evolution again.
            logger.log(
                "error",
                f"XP Share evolution prompt failed for {individual_id}: {error}",
            )
            return True
        if friendship_evo_id is not None:
            friendship_evo_name = return_name_for_id(friendship_evo_id)
            friendship_evo_name = (
                friendship_evo_name.capitalize()
                if friendship_evo_name
                else str(friendship_evo_id)
            )
            msg += evo_window.translator.translate(
                "pokemon_about_to_evolve_friendship",
                main_pokemon_name=pokemon["name"],
                evo_pokemon_name=friendship_evo_name,
            )
            evolution_triggered = True

    logger.log("info", f"{msg}")
    return True


def xp_share_gain_exp(logger, settings_obj, evo_window, main_pokemon_id, exp, xp_share_individual_id):
    """Grant XP Share's cut of ``exp``. Two modes, picked by
    ``trainer.xp_share_mode`` (default "classic" — never silently changes
    behavior for existing saves):

    * "classic" (pre-Gen-6 item behavior): one chosen holder Pokémon
      (``xp_share_individual_id``) splits ``exp`` 50/50 with the active
      Pokémon — both are reduced.
    * "oras" (Gen 6+ Key Item behavior): the active Pokémon keeps its FULL,
      un-reduced experience, and EVERY other Pokémon on the active team also
      earns that same full amount, as if each had battled too — no holder to
      choose, XP Share is just "on" for the whole team.

    Returns the amount the ACTIVE Pokémon should be credited.
    """
    mode = settings_obj.get("trainer.xp_share_mode", "classic")

    if mode == "oras":
        try:
            team_rows = services.db.get_team() or []
        except Exception:
            team_rows = []
        for row in team_rows:
            ind_id = row.get("individual_id")
            if not ind_id or ind_id == main_pokemon_id:
                continue
            _grant_xp_to_pokemon(logger, settings_obj, evo_window, ind_id, exp)
        return exp  # the active Pokémon's own, full, unreduced share

    # --- classic mode ---
    if not xp_share_individual_id or xp_share_individual_id == main_pokemon_id:
        return exp

    original_exp = int(exp * 0.5)
    half_exp = int(exp * 0.5)

    if services.db.get_pokemon(xp_share_individual_id) is None:
        # The XP-Share target may have been released or traded away since it
        # was selected, leaving a dangling individual_id in settings. Clear
        # the stale setting and return the already-computed half-exp so the
        # main Pokémon still gets its share and the review continues normally.
        settings_obj.set("trainer.xp_share", None)
        logger.log("info", "XP Share target no longer exists; cleared the setting.")
        return original_exp

    _grant_xp_to_pokemon(logger, settings_obj, evo_window, xp_share_individual_id, half_exp)
    return original_exp
