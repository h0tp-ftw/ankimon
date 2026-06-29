"""
Scenario: the shop economy — earn/grant cash and buy an item.

Drives the purchase logic headlessly (cash check, price lookup, inventory add).
The Qt shop window is not built in the harness; this exercises its economics.

Run:  python3 harness/scenarios/economy.py
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from harness.driver import Driver


def run(item: str = "potion", verbose: bool = True):
    d = Driver(settings_overrides={"audio.sounds": False, "audio.sound_effects": False})

    d.add_cash(1000)
    before = d.get_state()["trainer"]["cash"]

    # A purchase we can afford.
    buy_events = [e for e in d.buy_item(item) if e["type"] == "buy"]

    # A purchase we cannot afford (spend down, then try something pricey).
    d.set_setting("trainer.cash", 0)
    broke_events = [e for e in d.buy_item(item) if e["type"] == "buy"]

    after = d.get_state()["trainer"]["cash"]

    if verbose:
        print(f"cash granted -> {before}")
        print("buy (funded):", buy_events)
        print("buy (broke): ", broke_events)
        print(f"final cash: {after}")

    assert before >= 1000
    assert buy_events, "no buy event emitted for a funded purchase"
    # When broke, the purchase must be refused (unless the item is unknown).
    if broke_events and broke_events[0].get("reason") != "unknown_item":
        assert broke_events[0]["ok"] is False, "broke purchase should fail"

    return {"cash_before": before, "buy": buy_events, "broke": broke_events}


if __name__ == "__main__":
    print("economy: OK", run())
