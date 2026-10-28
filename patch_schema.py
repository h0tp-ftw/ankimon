import re

with open("src/Ankimon/ankimon_items_web/settings_schema.py", "r") as f:
    schema = f.read()

schema = schema.replace('"Pop-Up on Item Receive",', '"Pop-Up on Item Receive",\n            "Wishlist Item Received Override",')
schema = schema.replace('"Always Catch Wishlist",', '"Always Catch Wishlist",\n                    "Item Wishlist",')

with open("src/Ankimon/ankimon_items_web/settings_schema.py", "w") as f:
    f.write(schema)

with open("src/Ankimon/pyobj/settings_window.py", "r") as f:
    window = f.read()

window = window.replace('"Pop-Up on Item Receive",', '"Pop-Up on Item Receive",\n                    "Wishlist Item Received Override",')
window = window.replace('"Always Catch Wishlist",', '"Always Catch Wishlist",\n                    "Item Wishlist",')

with open("src/Ankimon/pyobj/settings_window.py", "w") as f:
    f.write(window)
