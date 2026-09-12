import re

with open("src/Ankimon/functions/battle_functions.py", "r") as f:
    content = f.read()

import_str = "from .battle_text_functions import effectiveness_text\n"
if "from .battle_text_functions import effectiveness_text" not in content:
    content = content.replace("from ..move_names import format_move_name", "from ..move_names import format_move_name\n" + import_str)


opponent_attack_section = """
        # 2. Enemy attack section
        if enemy_attack and enemy_attack != constants.DO_NOTHING_MOVE:
            # --- NEW: Format enemy move name ---
            formatted_enemy_attack = format_move_name(enemy_attack)

            enemy_attack_msg = translator.translate(
                "enemy_attack_announcement",
                pokemon_name=enemy_pokemon.display_name,
                attack_name=formatted_enemy_attack,  # Use the formatted name
            )
            message_parts.append(enemy_attack_msg)
"""

opponent_attack_replacement = """
        # 2. Enemy attack section
        if enemy_attack and enemy_attack != constants.DO_NOTHING_MOVE:
            if battle_info.get("opponent_move_blocked_by_status"):
                status_block = battle_info.get("opponent_move_blocked_by_status")
                status_key = "pokemon_is_paralyzed" if status_block == "par" else "pokemon_is_sleeping"
                message_parts.append(translator.translate(status_key, pokemon_name=enemy_pokemon.display_name))
            elif battle_info.get("opponent_move_missed"):
                missed_text = translator.translate("move_has_missed").lower()
                missed_text = missed_text.replace("move ", "attack ")
                message_parts.append(f"{enemy_pokemon.display_name}'s " + missed_text)
            else:
                # --- NEW: Format enemy move name ---
                formatted_enemy_attack = format_move_name(enemy_attack)

                enemy_attack_msg = translator.translate(
                    "enemy_attack_announcement",
                    pokemon_name=enemy_pokemon.display_name,
                    attack_name=formatted_enemy_attack,  # Use the formatted name
                )

                if battle_info.get("opponent_effectiveness") is not None:
                    eff_val = battle_info.get("opponent_effectiveness")
                    if eff_val != 1.0:
                        # translate effective text using a new helper function
                        enemy_attack_msg += " " + _get_effectiveness_text(eff_val, translator)
                message_parts.append(enemy_attack_msg)
"""
content = content.replace(opponent_attack_section, opponent_attack_replacement)

user_attack_section = """
        # 3. User attack section
        if user_attack and user_attack != constants.DO_NOTHING_MOVE:
            # Handle special battle statuses first
            if battle_status and battle_status != "fighting":
                status_msg = _handle_special_battle_status(
                    main_pokemon, battle_status, translator
                )
                if status_msg:
                    message_parts.append(status_msg)
            else:
                # --- NEW: Format user move name ---
                formatted_user_attack = format_move_name(user_attack)

                # Normal attack resolution
                user_attack_msg = translator.translate(
                    "player_attack_announcement",
                    pokemon_name=main_pokemon.display_name,
                    attack_name=formatted_user_attack,  # Use the formatted name
                )
                message_parts.append(user_attack_msg)
"""

user_attack_replacement = """
        # 3. User attack section
        if user_attack and user_attack != constants.DO_NOTHING_MOVE:
            # Handle special battle statuses first
            if battle_status and battle_status != "fighting":
                status_msg = _handle_special_battle_status(
                    main_pokemon, battle_status, translator
                )
                if status_msg:
                    message_parts.append(status_msg)

            if battle_info.get("user_move_blocked_by_status"):
                status_block = battle_info.get("user_move_blocked_by_status")
                status_key = "pokemon_is_paralyzed" if status_block == "par" else "pokemon_is_sleeping"
                message_parts.append(translator.translate(status_key, pokemon_name=main_pokemon.display_name))
            elif battle_info.get("user_move_missed"):
                missed_text = translator.translate("move_has_missed").lower()
                missed_text = missed_text.replace("move ", "attack ")
                message_parts.append(f"{main_pokemon.display_name}'s " + missed_text)
            elif not battle_status or battle_status == "fighting":
                # --- NEW: Format user move name ---
                formatted_user_attack = format_move_name(user_attack)

                # Normal attack resolution
                user_attack_msg = translator.translate(
                    "player_attack_announcement",
                    pokemon_name=main_pokemon.display_name,
                    attack_name=formatted_user_attack,  # Use the formatted name
                )

                if battle_info.get("user_effectiveness") is not None:
                    eff_val = battle_info.get("user_effectiveness")
                    if eff_val != 1.0:
                        user_attack_msg += " " + _get_effectiveness_text(eff_val, translator)
                message_parts.append(user_attack_msg)
"""

content = content.replace(user_attack_section, user_attack_replacement)

# Add _get_effectiveness_text
new_func = """
def _get_effectiveness_text(effect_value, translator):
    if effect_value == 0:
        return translator.translate("effectiveness_missed")
    elif effect_value <= 0.5:
        return translator.translate("effectiveness_not_very")
    elif effect_value <= 1.5:
        return translator.translate("effectiveness_very")
    elif effect_value <= 2:
        return translator.translate("effectiveness_super")
    else:
        return translator.translate("effectiveness_normal")
"""
content += new_func

with open("src/Ankimon/functions/battle_functions.py", "w") as f:
    f.write(content)
