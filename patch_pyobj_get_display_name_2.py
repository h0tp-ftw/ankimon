import re

file_path = "src/Ankimon/pyobj/test_window.py"
with open(file_path, "r") as f:
    content = f.read()

# I also noticed another `int(pokemon.id)` which could fail in `test_window.py` or elsewhere? Let's check `test_window.py`.
