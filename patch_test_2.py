import re

with open("tests/test_evolution_item_consumption.py", "r") as f:
    content = f.read()

content = content.replace('"evo_moves": patch(p + "get_evolution_moves_for_pokemon", return_value=[]).start(),\n', '')

with open("tests/test_evolution_item_consumption.py", "w") as f:
    f.write(content)
