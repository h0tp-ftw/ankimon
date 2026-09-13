import re

with open("src/Ankimon/functions/battle_functions.py", "r") as f:
    content = f.read()

safe_translate_section = """        if "pokemon_name" in kwargs and "status_name" in kwargs:
            if "apply" in key or "still" in key:
                return (
                    f"{kwargs['pokemon_name']} is affected by {kwargs['status_name']}!"
                )
            elif "remove" in key:
                return (
                    f"{kwargs['pokemon_name']} recovers from {kwargs['status_name']}!"
                )
        return f"Battle effect: {key}\""""

safe_translate_replacement = """        if "pokemon_name" in kwargs and "item" in kwargs:
            return f"{kwargs['pokemon_name']} used up its {kwargs['item']}!"
        if "pokemon_name" in kwargs and "status_name" in kwargs:
            if "apply" in key or "still" in key:
                return (
                    f"{kwargs['pokemon_name']} is affected by {kwargs['status_name']}!"
                )
            elif "remove" in key:
                return (
                    f"{kwargs['pokemon_name']} recovers from {kwargs['status_name']}!"
                )
        return f"Battle effect: {key}\""""
content = content.replace(safe_translate_section, safe_translate_replacement)


with open("src/Ankimon/functions/battle_functions.py", "w") as f:
    f.write(content)
