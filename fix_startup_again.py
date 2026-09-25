import re

with open("tests/test_async_startup_boot.py", "r") as f:
    content = f.read()

# Update test ENEMY_INFO to return 20 values instead of 18 in lambda mock
match = re.search(r'ENEMY_INFO = \((.*?)\)', content, re.DOTALL)
if match:
    new_lambda = """ENEMY_INFO = (
    "Pikachu",
    25,
    7,
    "static",
    ["electric"],
    {"hp": 35},
    ["thunder-shock", "growl", "tail-whip"],
    112,
    "medium",
    {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
    {"hp": 15, "atk": 15, "def": 15, "spa": 15, "spd": 15, "spe": 15},
    "male",
    "fighting",
    {"hp": 35, "atk": 55, "def": 40, "spa": 50, "spd": 50, "spe": 90},
    "Normal",
    {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 2},
    False,
    "hardy",
    False,
    None,
)"""
    content = content.replace(match.group(0), new_lambda)

with open("tests/test_async_startup_boot.py", "w") as f:
    f.write(content)
