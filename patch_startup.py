import re

with open("src/Ankimon/startup.py", "r") as f:
    content = f.read()

# Add a check for the item wishlist in the startup checks and notifications
replacement = """    if database_complete:
        enemy_info = _generate_first_enemy_background()

        if ankimon_db.get_pokemon_count() == 0:
            needs_starter = True

        badge_list = get_achieved_badges()
        if len(badge_list) > 1 and ankimon_db.get_user_data("rate_this") is not True:
            needs_rating = True

        # Check item wishlist against daily shop items
        wishlist_items_found = []
        try:
            from .pyobj.ankimon_shop import PokemonShopManager
            from .services import services
            sm = PokemonShopManager(logger, settings_obj, None, None)
            todays_items = sm.get_daily_items() or []
            todays_tms = sm.get_daily_tms() or []
            wishlist = settings_obj.get("gui.item_wishlist", [])
            for item in todays_items:
                if item["name"] in wishlist:
                    wishlist_items_found.append(item["name"])
            for tm in todays_tms:
                if tm["name"] in wishlist:
                    wishlist_items_found.append(tm["name"])
        except Exception as e:
            logger.log("error", f"Error checking wishlist items: {e}")"""

search = """    if database_complete:
        enemy_info = _generate_first_enemy_background()

        if ankimon_db.get_pokemon_count() == 0:
            needs_starter = True

        badge_list = get_achieved_badges()
        if len(badge_list) > 1 and ankimon_db.get_user_data("rate_this") is not True:
            needs_rating = True"""

if search in content:
    content = content.replace(search, replacement)
else:
    print("Could not find the block in startup.py (1)")


replacement_2 = """        "collected_pokemon_ids": collected_pokemon_ids,
        "database_complete": database_complete,
        "enemy_info": enemy_info,
        "needs_starter": needs_starter,
        "needs_rating": needs_rating,
        "wishlist_items_found": wishlist_items_found,
    }"""

search_2 = """        "collected_pokemon_ids": collected_pokemon_ids,
        "database_complete": database_complete,
        "enemy_info": enemy_info,
        "needs_starter": needs_starter,
        "needs_rating": needs_rating,
    }"""

if search_2 in content:
    content = content.replace(search_2, replacement_2)
else:
    print("Could not find the block in startup.py (2)")


replacement_3 = """    # Starter selection for a blank profile.
    if results["database_complete"] and results["needs_starter"]:
        from .singletons import get_starter_window

        get_starter_window().display_starter_pokemon()

    # Rate-this-addon prompt.
    if results["database_complete"] and results["needs_rating"]:
        rate_this_addon()

    # Item wishlist notification
    if results.get("wishlist_items_found"):
        from .services import services
        items_str = ", ".join(item.replace('-', ' ').title() for item in results["wishlist_items_found"])
        services.ui.notify("info", f"Wishlist item(s) available in today's shop: {items_str}!")"""

search_3 = """    # Starter selection for a blank profile.
    if results["database_complete"] and results["needs_starter"]:
        from .singletons import get_starter_window

        get_starter_window().display_starter_pokemon()

    # Rate-this-addon prompt.
    if results["database_complete"] and results["needs_rating"]:
        rate_this_addon()"""

if search_3 in content:
    content = content.replace(search_3, replacement_3)
else:
    print("Could not find the block in startup.py (3)")


with open("src/Ankimon/startup.py", "w") as f:
    f.write(content)
