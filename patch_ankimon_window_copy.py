import re

with open("src/Ankimon/gui_classes/AnkimonWindow copy.py", "r") as f:
    content = f.read()

replacement = """    def display_item(self, item_name=None):
        Receive_Window = QDialog(mw)
        layout = QHBoxLayout()
        if item_name is None:
            item_name = random_item()
        item_widget = self.pokemon_display_item(item_name)"""

content = re.sub(r'    def display_item\(self\):\n        Receive_Window = QDialog\(mw\)\n        layout = QHBoxLayout\(\)\n        item_name = random_item\(\)\n        item_widget = self\.pokemon_display_item\(item_name\)', replacement, content)

with open("src/Ankimon/gui_classes/AnkimonWindow copy.py", "w") as f:
    f.write(content)
