import re

with open("src/Ankimon/battle_loop.py", "r") as f:
    content = f.read()

replacement = """            item_window = services.test_window
            show_item_popup = settings_obj.get(
                "gui.pop_up_dialog_message_on_item", True
            )
            item_name = None
            wishlist = settings_obj.get("gui.item_wishlist", [])
            wishlist_override = settings_obj.get("gui.pop_up_dialog_message_wishlist_override", True)

            if not show_item_popup and wishlist_override:
                # Need to determine the item first to see if it overrides
                try:
                    item_name = random_item()
                    if item_name in wishlist:
                        show_item_popup = True
                except Exception:
                    pass

            if show_item_popup and is_alive(item_window):
                try:
                    item_window.display_item(item_name)
                except RuntimeError:
                    if item_name is None:
                        try:
                            random_item()
                        except Exception:
                            pass
            else:
                if item_name is None:
                    try:
                        random_item()
                    except Exception:
                        pass"""

# Match the old logic
search = """            item_window = services.test_window
            show_item_popup = settings_obj.get(
                "gui.pop_up_dialog_message_on_item", True
            )
            if show_item_popup and is_alive(item_window):
                try:
                    item_window.display_item()
                except RuntimeError:
                    pass
            else:
                try:
                    random_item()
                except Exception:
                    pass"""

if search in content:
    content = content.replace(search, replacement)
else:
    print("Could not find the block in battle_loop.py")

with open("src/Ankimon/battle_loop.py", "w") as f:
    f.write(content)
