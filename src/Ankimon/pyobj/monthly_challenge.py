"""Monthly challenge delivery, saved decisions, and profile-session handling.

Only the fetch runs in the background. Collection writes and dialogs run on the
GUI thread, with session checks around modal event loops.
"""

from datetime import datetime
from html import escape

import requests
from aqt import mw

from ..events import events
from ..services import services
from .error_handler import show_warning_with_traceback
from .monthly_challenge_dialogs import (
    show_monthly_acceptance_dialog,
    show_monthly_challenge_dialog,
    show_monthly_rejection_dialog,
)


def create_monthly_challenge_pokemon(pokemon_data, make_shiny=False):
    """Creates a Pokémon dictionary from monthly challenge data."""
    base_stats = pokemon_data.get("stats", {})
    return {
        "name": pokemon_data["name"],
        "nickname": pokemon_data.get("nickname", ""),
        "id": pokemon_data["id"],
        "level": pokemon_data.get("level", 1),
        "ability": pokemon_data.get("ability", "No Ability"),
        "type": pokemon_data.get("type", ["Normal"]),
        "stats": base_stats,
        "ev": pokemon_data.get(
            "ev", {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0}
        ),
        "iv": pokemon_data.get(
            "iv", {"hp": 15, "atk": 15, "def": 15, "spa": 15, "spd": 15, "spe": 15}
        ),
        "attacks": pokemon_data.get("attacks", ["Tackle"]),
        "growth_rate": pokemon_data.get("growth_rate", "medium"),
        "base_experience": pokemon_data.get("base_experience", 64),
        "gender": pokemon_data.get("gender", "N"),
        "shiny": pokemon_data.get("shiny", False) or make_shiny,
        "xp": pokemon_data.get("xp", 0),
        "current_hp": pokemon_data.get("current_hp", base_stats.get("hp")),
        "friendship": pokemon_data.get("friendship", 0),
        "pokemon_defeated": pokemon_data.get("pokemon_defeated", 0),
        "everstone": pokemon_data.get("everstone", False),
        "mega": pokemon_data.get("mega", False),
        "special_form": pokemon_data.get("special_form", None),
        "tier": pokemon_data.get("tier", "Normal"),
        "captured_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "individual_id": pokemon_data["individual_id"],
        "is_favorite": pokemon_data.get("is_favorite", False),
        "held_item": pokemon_data.get("held_item", None),
    }


def _refresh_collection(refresh_callback=None, parent_window=None):
    """Refresh presentation independently of the already committed award."""
    try:
        if refresh_callback:
            refresh_callback()
        from ..utils import is_alive

        if is_alive(services.pokemon_pc):
            services.pokemon_pc.refresh_pokemon_grid()
    except Exception as e:
        show_warning_with_traceback(
            parent=parent_window,
            exception=e,
            message="Error refreshing Pokemon collection",
        )


def add_pokemon_to_collection(
    new_pokemon,
    refresh_callback=None,
    parent_window=None,
    *,
    refresh=True,
    accept_monthly_challenge=False,
):
    """Return whether persistence succeeded; presentation cannot undo a save.

    Monthly awards save the Pokemon and accepted decision in one transaction.
    The failure dialog can run an event loop, so callers must recheck their
    session before doing any further database work.
    """
    try:
        if not services.db.save_pokemon(
            new_pokemon, accept_monthly_challenge=accept_monthly_challenge
        ):
            return False
    except Exception as e:
        show_warning_with_traceback(
            parent=parent_window,
            exception=e,
            message="Error adding Pokemon to collection",
        )
        return False
    if refresh:
        _refresh_collection(refresh_callback, parent_window)
    return True


def check_and_award_monthly_pokemon(logger, defer=True, *, reclaim=False):
    """Deliver this month's reward, respecting the user's saved decision.

    ``reclaim`` is the explicit menu action: offer rejected rewards or report
    owned progress. Coalesce requests for the same session through fetching and
    modal dialogs. Accepted but missing rewards are restored without a prompt.
    """

    def _fetch_monthly_data(current_month_str):
        """Fetch and validate this month's challenge. Returns dict or None.

        Runs on the background thread, so it must not touch the database: the
        active save can be switched while the request is in flight.
        """
        try:
            monthly_data_url = "https://raw.githubusercontent.com/h0tp-ftw/ankimon/refs/heads/main/assets/challenges/monthly_challenges.json"

            try:
                response = requests.get(monthly_data_url, timeout=2)
                response.raise_for_status()
                monthly_challenges = response.json()
            except requests.exceptions.RequestException as e:
                logger.log(
                    "error",
                    f"Could not fetch monthly challenges; likely no internet connection. Details: {e}",
                )
                return None

            if not isinstance(monthly_challenges, list):
                logger.log("warning", "Monthly challenge data is not a list.")
                return None

            current_challenge = next(
                (
                    c
                    for c in monthly_challenges
                    if isinstance(c, dict) and c.get("month") == current_month_str
                ),
                None,
            )

            if not current_challenge:
                logger.log(
                    "info", f"No monthly challenge found for {current_month_str}."
                )
                return None

            challenge_pokemon_data = current_challenge.get("pokemon")
            if not isinstance(challenge_pokemon_data, dict):
                logger.log(
                    "warning",
                    f"Monthly challenge for {current_month_str} is missing 'pokemon' data.",
                )
                return None

            raw_pokemon_id = challenge_pokemon_data.get("id")
            if isinstance(raw_pokemon_id, bool):
                raw_pokemon_id = None
            try:
                pokemon_id = int(raw_pokemon_id)
            except (TypeError, ValueError):
                pokemon_id = 0
            if pokemon_id <= 0:
                logger.log(
                    "warning",
                    f"Monthly challenge for {current_month_str} has an invalid Pokémon id.",
                )
                return None

            pokemon_name = challenge_pokemon_data.get("name")
            if not isinstance(pokemon_name, str) or not pokemon_name.strip():
                logger.log(
                    "warning",
                    f"Monthly challenge for {current_month_str} has an invalid Pokémon name.",
                )
                return None

            challenge_pokemon_data = dict(challenge_pokemon_data)
            challenge_pokemon_data["id"] = pokemon_id

            challenge_individual_id = challenge_pokemon_data.get("individual_id")
            if not challenge_individual_id:
                logger.log(
                    "warning",
                    f"Monthly challenge for {current_month_str} is missing 'individual_id' in 'pokemon' data.",
                )
                return None

            return {
                "current_challenge": current_challenge,
                "challenge_pokemon_data": challenge_pokemon_data,
            }

        except Exception as e:
            logger.log(
                "error",
                f"An unexpected error occurred while fetching monthly data: {e}",
            )
            return None

    def _session_unchanged(db, db_token, col):
        """Return True while the database and Anki profile are the ones seen at dispatch."""
        try:
            return (
                services.db is db and mw.col is col and db.identity_token() == db_token
            )
        except Exception:
            return False

    def _process_on_main_thread(
        result_data, db, db_token, col, current_month_str, reclaim
    ):
        """Apply the fetched challenge to the database on the main thread."""
        if not _session_unchanged(db, db_token, col):
            logger.log(
                "info",
                "Discarded the monthly challenge result: the Ankimon database or Anki profile changed while it was being fetched.",
            )
            return

        if result_data is None:
            if reclaim:
                services.ui.notify(
                    "warning",
                    "No monthly challenge could be loaded. Please try again later.",
                )
            return

        current_challenge = result_data["current_challenge"]
        challenge_pokemon_data = result_data["challenge_pokemon_data"]
        challenge_individual_id = challenge_pokemon_data["individual_id"]

        last_challenge_id = db.get_user_data("monthly_challenge_id")
        monthly_status = db.get_user_data("monthly_challenge", 0)
        try:
            monthly_status = int(monthly_status)
        except (TypeError, ValueError):
            monthly_status = 0

        # Edge case: Pokémon exists in collection but database tracking values are missing or stale
        pokemon_in_collection = db.get_pokemon(challenge_individual_id) is not None

        if reclaim and pokemon_in_collection:
            owned = db.get_pokemon(challenge_individual_id)
            services.ui.notify(
                "info",
                f"This month's Pokémon is already in your collection: {escape(str(owned.get('name', 'Pokémon')))}. "
                f"Level: {escape(str(owned.get('level', 1)))}. "
                f"Pokémon defeated: {escape(str(owned.get('pokemon_defeated', 0)))}.",
            )
            return

        # RECONCILE FIRST: If Pokémon exists in collection, sync tracking before any reset
        if pokemon_in_collection:
            needs_reconciliation = (
                last_challenge_id is None
                or str(last_challenge_id) != str(challenge_individual_id)
                or monthly_status == 0
            )
            if needs_reconciliation:
                db.set_monthly_challenge_state(challenge_individual_id, 1)
                logger.log(
                    "info",
                    f"Reconciled monthly challenge tracking: Pokémon {challenge_pokemon_data.get('name')} exists in collection, set monthly_challenge_id={challenge_individual_id}, monthly_challenge=1",
                )
                return

        if last_challenge_id is None or str(last_challenge_id) != str(
            challenge_individual_id
        ):
            db.set_monthly_challenge_state(challenge_individual_id, 0)
            monthly_status = 0

        if monthly_status == 2 and not reclaim:
            logger.log(
                "info", f"Monthly challenge for {current_month_str} was rejected."
            )
            return

        if monthly_status == 1 and pokemon_in_collection:
            logger.log(
                "info",
                f"User already has the Pokémon for {current_month_str} (ID: {challenge_individual_id}).",
            )
            return

        logger.log(
            "info",
            f"Awarding Pokémon for {current_month_str}: {challenge_pokemon_data.get('name')}",
        )
        make_shiny = False
        prev_id = current_challenge.get("previous_challenge_individual_id")
        threshold = current_challenge.get("defeat_threshold")

        if prev_id and threshold:
            logger.log(
                "info",
                f"Checking for shiny eligibility: prev_id={prev_id}, threshold={threshold}",
            )
            previous_challenge_pokemon = db.get_pokemon(prev_id)
            if previous_challenge_pokemon:
                try:
                    meets_threshold = int(
                        previous_challenge_pokemon.get("pokemon_defeated", 0)
                    ) >= int(threshold)
                except (ValueError, TypeError):
                    meets_threshold = False
                if meets_threshold:
                    logger.log(
                        "info",
                        f"Shiny criteria met for {challenge_pokemon_data.get('name')}.",
                    )
                    make_shiny = True

        new_pokemon = create_monthly_challenge_pokemon(
            challenge_pokemon_data, make_shiny=make_shiny
        )
        shiny_text = " (Shiny)" if new_pokemon["shiny"] else ""

        def award():
            # No refresh or informational dialog until both the Pokemon and
            # its accepted decision are committed. A failed save may show an
            # error dialog, but never triggers a "rollback" into another save.
            success = add_pokemon_to_collection(
                new_pokemon,
                parent_window=mw,
                refresh=False,
                accept_monthly_challenge=True,
            )
            if not _session_unchanged(db, db_token, col):
                return
            if not success:
                logger.log(
                    "error",
                    f"Failed to award {new_pokemon['name']}; keeping the previous challenge decision for a retry.",
                )
                return
            events.emit(
                "monthly_challenge",
                decision="accepted",
                individual_id=challenge_individual_id,
                restored=monthly_status == 1,
            )
            logger.log(
                "info", f"Successfully awarded {new_pokemon['name']}{shiny_text}."
            )
            _refresh_collection(parent_window=mw)
            if _session_unchanged(db, db_token, col):
                show_monthly_acceptance_dialog(
                    parent_window=mw, challenge_pokemon=new_pokemon
                )

        if monthly_status == 1:
            award()
            return

        # Other UI (or sync) may change the decision/collection while exec()
        # runs. Keep the exact offered state so a stale prompt cannot overwrite
        # another decision or replace an owned Pokemon's progress.
        offered_state = (
            db.get_user_data("monthly_challenge_id"),
            db.get_user_data("monthly_challenge", 0),
        )
        description = current_challenge.get("description", "")
        accepted = show_monthly_challenge_dialog(
            new_pokemon, description, parent_window=mw
        )
        if not _session_unchanged(db, db_token, col):
            logger.log(
                "warning",
                "Discarded the monthly challenge decision: the Ankimon database or Anki profile changed while the dialog was open.",
            )
            return
        if db.get_user_data("rate_this") not in (True, "true"):
            logger.log(
                "info",
                "Discarded the monthly challenge decision: rating eligibility changed while the dialog was open.",
            )
            return
        current_state = (
            db.get_user_data("monthly_challenge_id"),
            db.get_user_data("monthly_challenge", 0),
        )
        if (
            current_state != offered_state
            or db.get_pokemon(challenge_individual_id) is not None
        ):
            logger.log(
                "info",
                "Discarded a stale monthly challenge decision: the decision or collection changed while the dialog was open.",
            )
            return

        if accepted:
            award()
        else:
            db.set_monthly_challenge_state(challenge_individual_id, 2)
            events.emit(
                "monthly_challenge",
                decision="rejected",
                individual_id=challenge_individual_id,
            )
            show_monthly_rejection_dialog(
                parent_window=mw, challenge_pokemon=new_pokemon
            )
            logger.log("info", f"User rejected {new_pokemon['name']}{shiny_text}.")

    try:
        db = services.db
        if db.get_user_data("rate_this") not in (True, "true"):
            logger.log(
                "info", "Monthly Pokemon check skipped: user has not rated the addon."
            )
            if reclaim:
                services.ui.notify(
                    "info",
                    "Please rate the addon before claiming a monthly challenge Pokémon.",
                )
            return
        # Captured on the main thread before the worker starts, so the
        # callback can tell whether the database was switched or the profile
        # closed under it. Every Anki profile shares the Ankimon DB, so the
        # collection object is what marks a profile session.
        db_token = db.identity_token()
        col = mw.col
        if col is None:
            return
    except Exception as e:
        logger.log(
            "error",
            f"An unexpected error occurred while starting the monthly check: {e}",
        )
        return

    pending = getattr(services, "_monthly_challenge_request", None)
    if (
        pending is not None
        and pending["db"] is db
        and pending["token"] == db_token
        and pending["col"] is col
    ):
        # A menu click during the fetch upgrades the automatic check. Once
        # processing starts, nested requests share its existing dialog.
        if pending["fetching"] and reclaim:
            pending["reclaim"] = True
        return
    # Registry storage survives module reloads. Identity-based cleanup keeps
    # an old completion from releasing a newer session's pending request.
    request = {
        "db": db,
        "token": db_token,
        "col": col,
        "reclaim": reclaim,
        "fetching": True,
    }
    services._monthly_challenge_request = request

    def release():
        if getattr(services, "_monthly_challenge_request", None) is request:
            services._monthly_challenge_request = None

    def _complete(result_data):
        try:
            request["fetching"] = False
            _process_on_main_thread(
                result_data, db, db_token, col, current_month_str, request["reclaim"]
            )
        except Exception as e:
            logger.log("error", f"Error completing monthly check: {e}")
        finally:
            release()

    try:
        logger.log("info", "Checking for monthly challenge Pokemon award.")
        now = datetime.now()
        month_names = [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ]
        current_month_str = f"{month_names[now.month - 1]} {now.year}"
        if defer:

            def on_done(future):
                try:
                    result_data = future.result()
                except Exception as e:
                    release()
                    logger.log("error", f"Error completing monthly check: {e}")
                    return
                _complete(result_data)

            mw.taskman.run_in_background(
                lambda: _fetch_monthly_data(current_month_str), on_done
            )
        else:
            _complete(_fetch_monthly_data(current_month_str))
    except Exception as e:
        release()
        logger.log("error", f"Error starting monthly check: {e}")
