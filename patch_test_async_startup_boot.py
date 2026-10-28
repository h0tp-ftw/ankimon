import re

with open("tests/test_async_startup_boot.py", "r") as f:
    content = f.read()

# Pattern for the first block (lines 373-383)
content = re.sub(
    r'(\s*"enemy_info",\s*"needs_starter",\s*"needs_rating",)(\s*})',
    r'\1\n        "wishlist_items_found",\2',
    content
)

# Pattern for the mock block (lines 835-845)
content = re.sub(
    r'(\s*"enemy_info": ENEMY_INFO,\s*"needs_starter": False,\s*"needs_rating": False,)(\s*})',
    r'\1\n        "wishlist_items_found": [],\2',
    content
)

with open("tests/test_async_startup_boot.py", "w") as f:
    f.write(content)
