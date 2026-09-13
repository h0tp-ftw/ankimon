import re

with open("src/Ankimon/functions/battle_functions.py", "r") as f:
    content = f.read()

# restore the item consumption
import_str = """
def _display_item_name(item_id, holder):
    \"\"\"Return a display name for an item id.\"\"\"
    try:
        from ..pyobj.item_window import get_friendly_item_name
        return get_friendly_item_name(item_id)
    except Exception:
        pass
    if holder and getattr(holder, "held_item", None) == item_id:
        return item_id.title().replace("-", " ")
    return item_id.title()
"""

if "_display_item_name" not in content:
    content = content.replace("def _process_battle_effects(", import_str + "\ndef _process_battle_effects(")

item_section = """            # Handle stat boost changes
            elif any("""

item_replacement = """            # Handle a held item being spent
            elif key.endswith(".item"):
                # Only consumption. An item ARRIVING (Thief, Trick, a switch-in)
                # is a different event and reads wrong in this wording.
                if before and after is None:
                    target = "user" if key.startswith("user.") else "opponent"
                    holder = main_pokemon if target == "user" else enemy_pokemon
                    message = safe_translate(
                        "effect_item_consumed",
                        pokemon_name=get_pokemon_name(target),
                        item=_display_item_name(before, holder),
                    )
                    effect_messages.append(message)

            # Handle stat boost changes
            elif any("""

if "# Handle a held item being spent" not in content:
    content = content.replace(item_section, item_replacement)

with open("src/Ankimon/functions/battle_functions.py", "w") as f:
    f.write(content)
