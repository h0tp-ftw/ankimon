"""
Tier-2 scenario: screenshot the real Ankimon windows (offscreen).

Boots the real add-on, plays a couple of turns, and grabs PNGs of the real
battle window and the PC box — so you can *see* the genuine UI (real sprites,
layout) with no display.

    source .tier2/env.sh
    python -m harness.scenarios.screenshots
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from harness.real_driver import RealDriver
from harness import screenshot


def run(shots_dir=None, verbose: bool = True):
    shots = pathlib.Path(shots_dir) if shots_dir else (
        pathlib.Path(__file__).resolve().parents[2] / ".tier2" / "shots"
    )

    d = RealDriver(settings_overrides={
        "battle.cards_per_round": 1,
        "audio.sounds": False,
        "audio.sound_effects": False,
    })
    ts = d.services.test_window

    # Open the real battle window and play a few turns so it has battle state.
    try:
        ts.open_dynamic_window()
    except Exception:
        pass
    for _ in range(3):
        d.answer("good")
    try:
        ts.display_battle()
    except Exception:
        pass

    battle_png = screenshot.grab(ts, shots / "battle.png", size=(720, 540))

    if verbose:
        st = d.get_state()
        print(f"battle: {st['main']['name']} vs {st['enemy']['name']} "
              f"(enemy HP {st['enemy']['hp']}/{st['enemy']['max_hp']})")
        print(f"battle screenshot: {battle_png}")

    return {"battle_png": battle_png}


if __name__ == "__main__":
    print("screenshots: OK", run())
