import re

with open("tests/test_addon_integrity.py", "r") as f:
    content = f.read()

new_content = content.replace(
    "aqt.mw = MockMainWindow()",
    "aqt.mw = MockMainWindow()\n    sys.modules['aqt'].mw = aqt.mw"
)

with open("tests/test_addon_integrity.py", "w") as f:
    f.write(new_content)
