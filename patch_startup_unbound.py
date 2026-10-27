import re

with open("src/Ankimon/startup.py", "r") as f:
    content = f.read()

# Initialize wishlist_items_found unconditionally at the top of the function to avoid UnboundLocalError
replacement = """    enemy_info = None
    needs_starter = False
    needs_rating = False
    wishlist_items_found = []"""

search = """    enemy_info = None
    needs_starter = False
    needs_rating = False"""

if search in content:
    content = content.replace(search, replacement)
else:
    print("Could not find the block in startup.py (1)")


search2 = """        # Check item wishlist against daily shop items
        wishlist_items_found = []"""

replacement2 = """        # Check item wishlist against daily shop items"""

if search2 in content:
    content = content.replace(search2, replacement2)
else:
    print("Could not find the block in startup.py (2)")

with open("src/Ankimon/startup.py", "w") as f:
    f.write(content)
