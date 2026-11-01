with open("tests/test_headless_qt_guards.py", "r") as f:
    content = f.read()

content = content.replace("aqt_qt = types.ModuleType(\"aqt.qt\")", "aqt_qt = types.ModuleType(\"aqt.qt\")\nimport PyQt6")

with open("tests/test_headless_qt_guards.py", "w") as f:
    f.write(content)
