# Authoring New Focused Tier 1 & Tier 2 Proof Tests

This guide explains how to **author bespoke test scenarios** during PR reviews to actively prove that a PR's changes work as intended.

---

## 🎯 The Philosophy of PR Proof Verification

When reviewing a PR, standard baseline suites (`harness/check.py`) only confirm that nothing *catastrophically regressed*. They do **not** prove that the *new feature or fix* actually works in practice.

As a reviewer, you must:
1. **Extract the Core Hypothesis**: What specific behavior does this PR claim to fix, improve, or introduce?
2. **Author a Targeted Test Script**: Write a focused Python test script using Tier 1 (`Driver`) or Tier 2 (`RealDriver` + real PyQt6 offscreen).
3. **Execute & Observe**: Run the new test, inspect state transitions and event emissions, and verify database persistence.
4. **Attach Evidence**: Include your authored test and its execution output in the review report.

---

## 🖥️ Authoring New Tier 2 Proofs (Real Qt / Widgets / Persistence)

Tier 2 boots the genuine add-on with offscreen Qt. Use Tier 2 whenever a PR touches:
- Qt dialogs, menus, context actions, buttons, text inputs, dropdowns.
- PC Box movements, move management, nickname renaming, favorite toggling.
- Settings window persistence and tab navigation.
- Reviewer UI shortcuts, hooks, and HUD updates.
- WebShell host (`webshell/host.py`), `LiveUpdateBridge`, and QWebChannel messaging.

### Pattern 1: Authoring a Real UI Interaction Check (QTest)
```python
def check_pr_nickname_rename(d, app, db, pc, pool):
    """Proves that renaming a Pokemon via the real Qt details dialog persists to DB."""
    from PyQt6.QtWidgets import QLineEdit, QPushButton
    from PyQt6.QtTest import QTest

    iid = pool.pop()
    pkmn = db.get_pokemon(iid)
    pc.show_pokemon_details(pkmn)
    app.processEvents()

    # Locate real widgets in dialog
    edit = None
    btn = None
    for w in app.allWidgets():
        if isinstance(w, QLineEdit) and w.placeholderText() == "Nickname":
            edit = w
        elif isinstance(w, QPushButton) and w.text() == "Rename":
            btn = w

    assert edit is not None and btn is not None, "Could not find rename widgets"

    edit.clear()
    QTest.keyClicks(edit, "ThunderGod")
    btn.click()
    app.processEvents()

    # Assert persistence in database
    after = (db.get_pokemon(iid) or {}).get("nickname", "")
    assert after == "ThunderGod", f"Expected 'ThunderGod', got {after}"
    return True
```

### Pattern 2: Authoring a Real Hook / Reviewer Catch Proof
```python
def check_pr_catch_flow(d, app, db, pc, pool):
    """Proves that catching a fainted enemy updates the captured collection."""
    before_count = db.get_pokemon_count()
    
    # Simulate enemy fainting
    d.services.enemy_pokemon.hp = 0
    d.services.enemy_pokemon.current_hp = 0
    
    # Trigger real reviewer catch hook
    d.catch()
    app.processEvents()

    after_count = db.get_pokemon_count()
    assert after_count == before_count + 1, f"Expected {before_count + 1} Pokemon, found {after_count}"
    return True
```

### Pattern 3: Authoring a Settings Dropdown Persistence Proof
```python
def check_pr_settings_channel(d, app, db, pc, pool):
    """Proves that changing a settings dropdown updates both memory and settings DB."""
    from Ankimon.pyobj.settings_dialog import SettingsDialog
    
    dlg = SettingsDialog()
    app.processEvents()
    
    # Change setting via UI widget
    # dlg.some_dropdown.setCurrentIndex(1)
    # dlg.save_and_close()
    # app.processEvents()

    # Assert setting state
    # assert d.services.settings.get("some_key") == "expected_val"
    dlg.close()
    dlg.deleteLater()
    return True
```

---

## 🏎️ Authoring New Tier 1 Proofs (Domain Logic / Battle Loop)

Tier 1 runs with zero dependencies under plain Python. Use Tier 1 whenever a PR touches:
- Battle calculations, damage formulas, move execution, type effectiveness.
- Card answer processing and multiplier calculations.
- Leveling up, EXP curves, evolutionary thresholds.
- Encounter rate algorithms, pity counters, mastery calculations.
- Item inventory operations and SQLite queries.

### Pattern 1: Authoring a Battle Damage & Faint Proof
```python
from harness.driver import Driver

def run_proof():
    d = Driver(seed={
        "main": {"species": "Charizard", "level": 50, "moves": ["Flamethrower"]},
    })
    d.set_enemy(species="Oddish", level=5)  # Grass/Poison weak to Fire
    
    d.answer("good")  # Attacks
    events = d.drain_events()
    state = d.get_state()

    # Assert battle events
    assert any(e["type"] == "battle" for e in events), "Expected battle event"
    assert any(e["type"] in ("defeat", "faint") for e in events), "Expected Oddish to faint"
    print("✅ Battle proof passed!")
```

### Pattern 2: Authoring an Encounter Pity / Roll Proof
```python
from harness.driver import Driver

def run_proof():
    d = Driver(seed={"main": {"species": "Pikachu", "level": 20}})
    
    # Simulate answering 20 cards and track spawned enemy tiers
    spawned_tiers = []
    for _ in range(20):
        d.answer("good")
        events = d.drain_events()
        for e in events:
            if e["type"] == "encounter":
                spawned_tiers.append(e.get("tier"))
                
    assert len(spawned_tiers) > 0, "Expected encounters to trigger"
    print(f"✅ Encounter proof passed! Tiers: {spawned_tiers}")
```

---

## 📁 Where to Save Authored Tests

When reviewing a PR, save your newly authored proof test in one of these locations:
- `tests/proofs/test_pr_<PR_NUMBER>_proof.py`
- Or run it via:
  ```bash
  python .agents/skills/pr-reviewer/scripts/run_proof_scenario.py --file path/to/proof.py
  ```

Once verified, paste the test script and its execution output directly into the review report!
