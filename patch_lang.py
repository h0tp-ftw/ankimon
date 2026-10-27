import re

with open("src/Ankimon/lang/setting_name.json", "r") as f:
    names = f.read()

names = names.replace('"gui.pop_up_dialog_message_on_item": "Pop-Up on Item Receive",', '"gui.pop_up_dialog_message_on_item": "Pop-Up on Item Receive",\n    "gui.pop_up_dialog_message_wishlist_override": "Wishlist Item Received Override",')
names = names.replace('"battle.auto_catch_wishlist": "Always Catch Wishlist",', '"battle.auto_catch_wishlist": "Always Catch Wishlist",\n    "gui.item_wishlist": "Item Wishlist",')

with open("src/Ankimon/lang/setting_name.json", "w") as f:
    f.write(names)

with open("src/Ankimon/lang/setting_description.json", "r") as f:
    desc = f.read()

desc = desc.replace('"gui.pop_up_dialog_message_on_item": "Show a pop-up window when you receive an item. Turning this off still awards the item, it just does not interrupt your reviews.",', '"gui.pop_up_dialog_message_on_item": "Show a pop-up window when you receive an item. Turning this off still awards the item, it just does not interrupt your reviews.",\n    "gui.pop_up_dialog_message_wishlist_override": "Show a pop-up window when you receive an item that is on your item wishlist, even if the generic item pop-up setting is turned off.",')
desc = desc.replace('"battle.auto_catch_wishlist": "List of Pokémon IDs that are always caught when encountered, regardless of other auto-catch settings.",', '"battle.auto_catch_wishlist": "List of Pokémon IDs that are always caught when encountered, regardless of other auto-catch settings.",\n    "gui.item_wishlist": "List of item identifiers (e.g. \\"linking-cord\\") to notify you about when received or seen in the shop.",')

with open("src/Ankimon/lang/setting_description.json", "w") as f:
    f.write(desc)
