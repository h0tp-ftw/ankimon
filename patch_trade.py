import re

with open("src/Ankimon/pyobj/pokemon_trade.py", "r") as f:
    content = f.read()

create_str = """def create_monthly_challenge_pokemon(pokemon_data, make_shiny=False):"""
new_create_str = """def create_monthly_challenge_pokemon(pokemon_data, month_name=None, make_shiny=False):"""
content = content.replace(create_str, new_create_str)

is_monthly_str = """        "is_favorite": pokemon_data.get("is_favorite", False),
        "held_item": pokemon_data.get("held_item", None),
    }"""
new_is_monthly_str = """        "is_favorite": pokemon_data.get("is_favorite", False),
        "held_item": pokemon_data.get("held_item", None),
        "is_monthly": True,
        "monthly_month": month_name,
    }"""
content = content.replace(is_monthly_str, new_is_monthly_str)

call_str = """        new_pokemon = create_monthly_challenge_pokemon(
            challenge_pokemon_data, make_shiny=make_shiny
        )"""
new_call_str = """        new_pokemon = create_monthly_challenge_pokemon(
            challenge_pokemon_data, month_name=current_challenge.get("month"), make_shiny=make_shiny
        )"""
content = content.replace(call_str, new_call_str)

process_func_str = """    def _process_on_main_thread(
        result_data, db, db_token, col, current_month_str, reclaim
    ):
        \"\"\"Apply the fetched challenge to the database on the main thread.\"\"\"
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
            return"""

new_process_func_str = """    def _process_on_main_thread(
        result_data, db, db_token, col, current_month_str, reclaim
    ):
        \"\"\"Apply the fetched challenge to the database on the main thread.\"\"\"
        if not _session_unchanged(db, db_token, col):
            logger.log(
                "info",
                "Discarded the monthly challenge result: the Ankimon database or Anki profile changed while it was being fetched.",
            )
            return

        # Reconcile all challenges first
        if result_data and "all_challenges" in result_data:
            try:
                challenge_ids = []
                month_names = {}
                for c in result_data["all_challenges"]:
                    ind_id = c.get("pokemon", {}).get("individual_id")
                    month = c.get("month")
                    if ind_id and month:
                        challenge_ids.append(ind_id)
                        month_names[ind_id] = month
                if challenge_ids:
                    db.reconcile_monthly_pokemon(challenge_ids, month_names)
            except Exception as e:
                logger.log("error", f"Reconciliation error: {e}")

        if not result_data or not result_data.get("current_challenge"):
            if reclaim:
                services.ui.notify(
                    "warning",
                    "No monthly challenge could be loaded. Please try again later.",
                )
            return"""

content = content.replace(process_func_str, new_process_func_str)

fetch_func_str = """            if not current_challenge:
                logger.log(
                    "info", f"No monthly challenge found for {current_month_str}."
                )
                return None

            challenge_pokemon_data = current_challenge.get("pokemon")
            if not isinstance(challenge_pokemon_data, dict):
                logger.log(
                    "error",
                    f"Monthly challenge for {current_month_str} is missing 'pokemon' data.",
                )
                return None"""
new_fetch_func_str = """            if not current_challenge:
                logger.log(
                    "info", f"No monthly challenge found for {current_month_str}."
                )
                return {
                    "current_challenge": None,
                    "challenge_pokemon_data": None,
                    "all_challenges": monthly_challenges,
                }

            challenge_pokemon_data = current_challenge.get("pokemon")
            if not isinstance(challenge_pokemon_data, dict):
                logger.log(
                    "error",
                    f"Monthly challenge for {current_month_str} is missing 'pokemon' data.",
                )
                return {
                    "current_challenge": None,
                    "challenge_pokemon_data": None,
                    "all_challenges": monthly_challenges,
                }"""
content = content.replace(fetch_func_str, new_fetch_func_str)

fetch_return_str = """            return {
                "current_challenge": current_challenge,
                "challenge_pokemon_data": challenge_pokemon_data,
            }"""
new_fetch_return_str = """            return {
                "current_challenge": current_challenge,
                "challenge_pokemon_data": challenge_pokemon_data,
                "all_challenges": monthly_challenges,
            }"""
content = content.replace(fetch_return_str, new_fetch_return_str)

with open("src/Ankimon/pyobj/pokemon_trade.py", "w") as f:
    f.write(content)
