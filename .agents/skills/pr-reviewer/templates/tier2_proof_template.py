#!/usr/bin/env python3
"""
Tier 2 Focused Proof Template for PR Review.

Boots genuine Ankimon with real PyQt6 in offscreen mode.
Use this template to author a NEW test proving that a UI/widget or state persistence feature works.
"""

import sys
from pathlib import Path

# Ensure repo root is on sys.path
for candidate in (*Path(__file__).resolve().parents, Path.cwd(), *Path.cwd().parents):
    if (candidate / "harness" / "driver.py").is_file() and (
        candidate / "src" / "Ankimon"
    ).is_dir():
        REPO_ROOT = str(candidate)
        break
else:
    raise RuntimeError(
        "Save this proof inside the Ankimon checkout or run it from the checkout root."
    )
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _find_widget(parent, cls, **match):
    """Find a visible child matching every supplied text getter."""
    for w in parent.findChildren(cls):
        if not w.isVisible():
            continue
        if all(
            sub.casefold() in str(getattr(w, getter)() or "").casefold()
            for getter, sub in match.items()
        ):
            return w
    return None


def run_proof():
    """Example rename proof; adapt the actual interaction and assertion to the PR."""
    from harness.real_driver import RealDriver
    from harness.fixtures import build_pokemon
    from PyQt6.QtWidgets import QPushButton, QLineEdit
    from PyQt6.QtTest import QTest

    # RealDriver has no seed argument. Boot a throwaway profile, then insert
    # this non-main Pokemon through the existing fixture and database APIs.
    d = RealDriver(
        first_encounter=False,
        settings_overrides={
            "audio.sounds": False,
            "audio.sound_effects": False,
        },
    )
    app = d.env.app
    db = d.services.db
    pc = d.services.pokemon_pc
    pokemon = build_pokemon(
        {"species": "Gengar", "level": 50, "nickname": "OriginalNick"}
    )
    iid = pokemon.individual_id
    db.save_pokemon(pokemon.to_dict())
    assert db.get_pokemon(iid)["nickname"] == "OriginalNick"

    try:
        pc.show()
        pc.refresh_pokemon_grid()
        pc.show_pokemon_details({"individual_id": iid})
        app.processEvents()

        edit = _find_widget(
            pc, QLineEdit, placeholderText="Enter a new Nickname for your Pokémon"
        )
        button = _find_widget(pc, QPushButton, text="Rename Pokémon")
        assert edit is not None and button is not None, "Rename widgets must be visible"
        edit.clear()
        QTest.keyClicks(edit, "NewNick")
        button.click()
        app.processEvents()

        updated_pkmn = db.get_pokemon(iid)
        assert updated_pkmn["nickname"] == "NewNick", "Rename must persist in SQLite"
        events = d.drain_events()
        errors = [e for e in events if e.get("type") == "error"]
        assert not errors, f"Unexpected error events fired: {errors}"

        print(
            "✅ Tier 2 sample PASSED: Renaming through real Qt widgets persisted in SQLite."
        )
        return True
    finally:
        pc.close()
        app.processEvents()


if __name__ == "__main__":
    success = run_proof()
    sys.exit(0 if success else 1)
