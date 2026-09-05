#!/usr/bin/env python3
"""
Tier 2 Focused Proof Template for PR Review.

Boots genuine Ankimon with real PyQt6 in offscreen mode.
Use this template to author a NEW test proving that a UI/widget or state persistence feature works.
"""

import os
import sys

# Ensure repo root is on sys.path
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _find_widget(app, cls, **match):
    """Finds first visible widget of class `cls` whose getter text matches `match`."""
    for w in app.allWidgets():
        if not isinstance(w, cls) or not w.isVisible():
            continue
        for getter, sub in match.items():
            try:
                val = getattr(w, getter)() or ""
            except Exception:
                val = ""
            if sub.lower() in str(val).lower():
                return w
    return None


def run_proof():
    from harness.real_driver import RealDriver
    from PyQt6.QtWidgets import QPushButton, QLineEdit
    from PyQt6.QtTest import QTest

    # 1. Seed initial game state matching the PR scenario
    d = RealDriver(seed={
        "main": {"species": "Pikachu", "level": 25},
        "box": [
            {"species": "Gengar", "level": 50, "nickname": "OriginalNick"},
            {"species": "Snorlax", "level": 40},
        ],
        "items": {"Pokeball": 10},
    })
    
    app = d.app
    db = d.services.db
    pc = d.services.pc_box_window

    print(">>> 1. Initial State Initialized.")

    # 2. Drive the specific UI feature or dialog
    # Example: Inspect a Box Pokemon and perform an action
    all_pkmn = db.get_all_pokemon()
    target_pkmn = all_pkmn[0]
    iid = target_pkmn["individual_id"]
    
    # pc.show_pokemon_details(target_pkmn)
    # app.processEvents()

    # 3. Simulate user interactions with widgets using QTest
    # edit = _find_widget(app, QLineEdit, placeholderText="Nickname")
    # btn = _find_widget(app, QPushButton, text="Rename")
    # if edit and btn:
    #     edit.clear()
    #     QTest.keyClicks(edit, "NewNick")
    #     btn.click()
    #     app.processEvents()

    # 4. Assert that DB / game state updated as expected
    updated_pkmn = db.get_pokemon(iid)
    # assert updated_pkmn.get("nickname") == "NewNick", "Nickname should update in SQLite DB"

    # 5. Check event stream for unexpected errors
    events = d.drain_events()
    errors = [e for e in events if e.get("type") == "error"]
    assert not errors, f"Unexpected error events fired: {errors}"

    print("✅ Tier 2 Proof PASSED: Real Qt feature interaction and persistence verified.")
    return True


if __name__ == "__main__":
    success = run_proof()
    sys.exit(0 if success else 1)
