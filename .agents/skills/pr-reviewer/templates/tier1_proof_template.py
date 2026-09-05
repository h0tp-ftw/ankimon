#!/usr/bin/env python3
"""
Tier 1 Focused Proof Template for PR Review.

Runs in plain Python (zero external dependencies).
Use this template to author a NEW test proving that battle calculations,
encounters, item usage, leveling, or core state logic behaves correctly.
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


def run_proof():
    from harness.driver import Driver

    # 1. Seed initial game state matching the PR scenario
    d = Driver(seed={
        "main": {"species": "Pikachu", "level": 25, "hp": 60, "max_hp": 60},
        "box": [{"species": "Bulbasaur", "level": 15}],
        "items": {"Pokeball": 5, "Potion": 3},
    })

    print(">>> 1. Driver Initialized with Seed State.")

    # 2. Drive the specific affected mechanic
    # Example: Spawn specific enemy and simulate card answer
    d.set_enemy(species="Pidgey", level=10)
    d.answer("good")

    # 3. Observe event stream and assert state invariants
    events = d.drain_events()
    state = d.get_state()

    # 4. Invariant assertions proving the PR behavior
    assert state["main"]["level"] >= 25, "Level should not decrease"
    assert not any(e["type"] == "error" for e in events), "No error events should be emitted"

    print("✅ Tier 1 Proof PASSED: Domain logic and events verified cleanly.")
    return True


if __name__ == "__main__":
    success = run_proof()
    sys.exit(0 if success else 1)
