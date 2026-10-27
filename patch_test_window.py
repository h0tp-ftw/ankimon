import re

with open("src/Ankimon/pyobj/test_window.py", "r") as f:
    content = f.read()

replacement = """    def display_item(self, item_name=None):
        if item_name is None:
            item_name = random_item()
        if item_name is None:
            return
"""

content = re.sub(r'    def display_item\(self\):\n        item_name = random_item\(\)\n        if item_name is None:\n            return\n', replacement, content)

with open("src/Ankimon/pyobj/test_window.py", "w") as f:
    f.write(content)
