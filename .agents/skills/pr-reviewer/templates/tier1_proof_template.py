#!/usr/bin/env python3
"""
Tier 1 Focused Proof Template for PR Review.

Runs without Anki or Qt (install the harness dependency: requests).
Use this template to author a NEW test proving that battle calculations,
encounters, item usage, leveling, or core state logic behaves correctly.
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


def run_proof():
    """Example battle proof; replace the setup and assertions with the PR claim."""
    from harness.driver import Driver

    # 1. Seed initial game state matching the PR scenario
    d = Driver(
        seed={
            "main": {"species": "Pikachu", "level": 25},
            "box": [{"species": "Bulbasaur", "level": 15}],
            "items": {"Pokeball": 5, "Potion": 3},
        },
        settings_overrides={"battle.cards_per_round": 1},
        first_encounter=False,
    )

    print(">>> 1. Driver Initialized with Seed State.")

    # 2. Drive the specific affected mechanic
    # Example: Spawn specific enemy and simulate card answer
    events = d.set_enemy(species="Pidgey", level=10)
    events.extend(d.answer("good"))

    # 3. Observe event stream and assert state invariants
    state = d.get_state()

    # 4. Invariant assertions proving the PR behavior
    assert state["main"]["level"] >= 25, "Level should not decrease"
    assert 0 <= state["main"]["hp"] <= state["main"]["max_hp"], (
        "Main HP must remain valid"
    )
    assert any(e["type"] == "battle" for e in events), (
        "A card answer must produce a battle"
    )
    assert not any(e["type"] == "error" for e in events), (
        "No error events should be emitted"
    )

    print(
        "✅ Tier 1 sample PASSED: A card answer produced a battle with valid main HP."
    )
    return True


if __name__ == "__main__":
    success = run_proof()
    sys.exit(0 if success else 1)
