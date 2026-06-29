"""
Tier-2 scenario: open the REAL Pokemon PC box and change a Pokemon's moves.

This is the interaction you flagged as missing in Tier 1 — it drives the genuine
PokemonPC window (real grid + details panel) and the real move-edit functions
(remember_attack / forget_attack, which persist to the DB). It also screenshots
the battle window and the PC box so you can see the real UI.

Requires the Tier-2 env:
    source .tier2/env.sh
    python -m harness.scenarios.pc_box_moves
"""

import sys
import json
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from harness.real_driver import RealDriver
from harness import screenshot


def _first_caught(db):
    row = db.execute(
        "SELECT individual_id, data FROM captured_pokemon WHERE is_main=0 LIMIT 1"
    ).fetchone()
    if not row:
        return None, None
    return row["individual_id"], json.loads(row["data"])


def run(verbose: bool = True, shots_dir=None):
    shots = pathlib.Path(shots_dir) if shots_dir else (
        pathlib.Path(__file__).resolve().parents[2] / ".tier2" / "shots"
    )

    d = RealDriver(settings_overrides={
        "battle.cards_per_round": 1,
        "battle.automatic_battle": 0,
        "audio.sounds": False,
        "audio.sound_effects": False,
    })
    s = d.services

    # --- play until we've caught a wild Pokemon to edit ---
    caught = False
    for _ in range(150):
        d.answer("good")
        if s.enemy_pokemon.hp == 0:
            d.catch()
            caught = True
            break
    assert caught, "couldn't catch a Pokemon to edit"

    iid, data = _first_caught(s.db)
    assert iid, "no captured Pokemon in the DB"
    before = list(data["attacks"])

    # --- drive the REAL PC box window (grid + details panel), offscreen ---
    import Ankimon.gui_classes.pokemon_details as pd

    pc = s.pokemon_pc                 # the real PokemonPC window
    pc.show()
    pc.refresh_pokemon_grid()
    pc.show_pokemon_details({"individual_id": iid})

    shot_pc = screenshot.grab(pc, shots / "pc_box.png")

    # --- change moves via the real move-edit functions (persist to DB) ---
    logger = s.logger
    if len(before) > 1:
        pd.forget_attack(iid, before, before[-1], logger)   # drop the last move
    # learn a move it almost certainly doesn't have yet
    candidates = ["splash", "tackle", "ember", "watergun", "vinewhip", "thundershock"]
    new_move = next((m for m in candidates if m not in [a.lower() for a in before]), "splash")
    pd.remember_attack(iid, [], new_move, logger)

    after = json.loads(
        s.db.execute(
            "SELECT data FROM captured_pokemon WHERE individual_id=?", (iid,)
        ).fetchone()["data"]
    )["attacks"]

    if verbose:
        print(f"caught: {data['name']} ({iid[:8]})")
        print(f"  moves before: {before}")
        print(f"  moves after : {after}")
        print(f"  pc_box screenshot: {shot_pc}")

    assert after != before, "moves did not change via the real PC box path"
    return {"pokemon": data["name"], "before": before, "after": after, "pc_box_png": shot_pc}


if __name__ == "__main__":
    print("pc_box_moves: OK", run())
