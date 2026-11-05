import re

file_path_encounter_data = "src/Ankimon/functions/encounter_data.py"

with open(file_path_encounter_data, "r") as f:
    content = f.read()

# Add Rotom and Wormadam forms to NORMAL
insert_str = """    10004,
    10005,  # wormadam forms
    10008,
    10009,
    10010,
    10011,
    10012,  # rotom forms
"""

# Insert before "    # Generation 5" in the NORMAL list
# The NORMAL list contains "    # Generation 5" multiple times, we need the first one (after rotom 479)
match_str = "    479,  # dusknoir, froslass, rotom\n    # Generation 5"
replace_str = "    479,  # dusknoir, froslass, rotom\n" + insert_str + "    # Generation 5"

content = content.replace(match_str, replace_str)

with open(file_path_encounter_data, "w") as f:
    f.write(content)

print("Updated NORMAL list!")
