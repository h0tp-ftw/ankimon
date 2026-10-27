import re

with open("src/Ankimon/pyobj/settings.py", "r") as f:
    settings = f.read()

settings = settings.replace('"gui.pop_up_dialog_message_on_item": True,', '"gui.pop_up_dialog_message_on_item": True,\n    "gui.pop_up_dialog_message_wishlist_override": True,')
settings = settings.replace('"battle.auto_catch_wishlist": [25, 133],', '"battle.auto_catch_wishlist": [25, 133],\n    "gui.item_wishlist": ["linking-cord"],')

with open("src/Ankimon/pyobj/settings.py", "w") as f:
    f.write(settings)

with open("src/Ankimon/config.json", "r") as f:
    config = f.read()

config = config.replace('"gui.pop_up_dialog_message_on_item": true,', '"gui.pop_up_dialog_message_on_item": true,\n    "gui.pop_up_dialog_message_wishlist_override": true,')
config = config.replace('"battle.auto_catch_wishlist": [25, 133],', '"battle.auto_catch_wishlist": [25, 133],\n    "gui.item_wishlist": ["linking-cord"],')

with open("src/Ankimon/config.json", "w") as f:
    f.write(config)
