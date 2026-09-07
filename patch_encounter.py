with open("src/Ankimon/functions/encounter_functions.py", "r") as f:
    content = f.read()

search_pattern = """    if not _in_bulk_resolve() and settings_obj.get("gui.pop_up_dialog_message_on_encounter") is True:
        if pokemon.shiny or pokemon.tier >= 4:
            # We construct a message appropriately
            if pokemon.shiny:
                msg = f"A Shiny wild {get_pretty_name_for_name(pokemon.name)} appeared!"
            else:
                msg = f"A rare wild {get_pretty_name_for_name(pokemon.name)} appeared!"

            try:
                if services.logger:
                    services.logger.log_and_showinfo("info", msg)
            except Exception as e:
                pass"""

replace_pattern = """    # Show a popup message for rare/shiny Pokemon if the setting is enabled
    if not _in_bulk_resolve() and settings_obj.get("gui.pop_up_dialog_message_on_encounter") is True:
        if pokemon.shiny or pokemon.tier >= 4:
            if pokemon.shiny:
                msg = f"A Shiny wild {get_pretty_name_for_name(pokemon.name)} appeared!"
            else:
                msg = f"A rare wild {get_pretty_name_for_name(pokemon.name)} appeared!"

            try:
                if services.logger:
                    services.logger.log_and_showinfo("info", msg)
            except Exception:
                pass"""

content = content.replace("    # Show a popup message for rare/shiny Pokemon if the setting is enabled\n" + search_pattern, replace_pattern)

with open("src/Ankimon/functions/encounter_functions.py", "w") as f:
    f.write(content)
