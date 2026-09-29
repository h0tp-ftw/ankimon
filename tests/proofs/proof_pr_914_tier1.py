#!/usr/bin/env python3
"""PR #914: actual victory/SQLite friendship awards and mobile persistence.

Run in a fresh interpreter: python tests/proofs/proof_pr_914_tier1.py
Only the friendship die is fixed; game progression, evolution lookup and SQLite
are the production implementations. All records live in a throwaway profile.
"""

import json
from pathlib import Path
import random
import sqlite3
import sys
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


def _persisted(db, individual_id):
    # A separate connection proves that changes were committed, not merely kept
    # in a mutable fixture or an uncommitted production connection.
    with sqlite3.connect(db.db_path) as conn:
        row = conn.execute(
            "SELECT data FROM captured_pokemon WHERE individual_id = ?",
            (individual_id,),
        ).fetchone()
    assert row is not None, individual_id
    return json.loads(row[0])


def run_proof():
    from harness.driver import Driver

    random.seed(914)
    driver = Driver(
        seed={
            "main": {
                "species": "Gengar", "level": 50, "friendship": 400,
                "individual_id": "pr914-main",
            },
            "team": [
                {
                    "species": "Snorlax", "level": 50, "friendship": 300,
                    "held_item": "soothe-bell", "individual_id": "pr914-holder",
                },
                {
                    "species": "Gyarados", "level": 50,
                    "individual_id": "pr914-teammate",
                },
            ],
            "box": [{
                "species": "Rhydon", "level": 50, "friendship": 254,
                "individual_id": "pr914-box",
            }],
        },
        settings_overrides={
            "trainer.xp_share_mode": "classic",
            "trainer.xp_share": "pr914-holder",
            "battle.automatic_battle": 0,
            "controls.allow_to_choose_moves": False,
            "audio.sounds": False,
            "audio.sound_effects": False,
        },
        evolution_policy="ignore",
        first_encounter=False,
    )
    from Ankimon.functions.friendship_evolution import get_friendship_evolutions_for_species
    from Ankimon.functions.mobile_sync import _attribute_xp_and_evs_to_companion
    from Ankimon.functions.trainer_functions import xp_share_gain_exp
    from harness.fixtures import build_pokemon

    services = driver.services
    db = services.db
    events = []
    randint = random.randint

    def fixed_friendship(low, high):
        return 6 if (low, high) == (5, 9) else randint(low, high)

    with patch("random.randint", side_effect=fixed_friendship):
        # Actual defeat resolution calls XP Share and the active Pokemon's
        # progress path. The holder receives a Soothe Bell boost exactly once.
        events.extend(driver.set_enemy(species="Magikarp", level=5, hp=0))
        events.extend(driver.defeat())
        assert _persisted(db, "pr914-main")["friendship"] == 406
        holder = _persisted(db, "pr914-holder")
        assert holder["friendship"] == 309 and holder["xp"] > 0
        assert _persisted(db, "pr914-teammate")["friendship"] == 0
        assert _persisted(db, "pr914-box")["friendship"] == 254

        driver.set_setting("trainer.xp_share_mode", "oras")
        events.extend(driver.set_enemy(species="Magikarp", level=5, hp=0))
        events.extend(driver.defeat())
        assert _persisted(db, "pr914-main")["friendship"] == 412
        assert _persisted(db, "pr914-holder")["friendship"] == 318
        teammate = _persisted(db, "pr914-teammate")
        assert teammate["friendship"] == 6 and teammate["xp"] > 0
        assert _persisted(db, "pr914-box")["friendship"] == 254
        assert services.main_pokemon.friendship == 412
        print("PASS: classic and ORAS victories persist one friendship award per recipient")

        # Cross a real bundled friendship threshold without gaining a level.
        evolution = next(
            entry for entry in get_friendship_evolutions_for_species(172)
            if entry.evo_id == 25
        )
        pichu = build_pokemon({
            "species": "Pichu", "level": 50,
            "friendship": evolution.min_happiness - 6,
            "individual_id": "pr914-pichu",
        })
        db.save_pokemon(pichu.to_dict())
        driver.set_setting("trainer.xp_share_mode", "classic")
        active_xp = xp_share_gain_exp(
            services.logger, services.settings, services.evo_window,
            "pr914-main", 2, "pr914-pichu",
        )
        assert active_xp == 1
        stored = _persisted(db, "pr914-pichu")
        assert stored["friendship"] == evolution.min_happiness
        assert stored["level"] == 50 and stored["xp"] == 1
        evolution_events = driver.drain_events()
        events.extend(evolution_events)
        prompts = [e for e in evolution_events if e["type"] == "evolution_prompt"]
        assert len(prompts) == 1 and prompts[0]["evo_id"] == 25
        assert prompts[0]["individual_id"] == "pr914-pichu"
        print("PASS: XP Share crosses the real Pichu threshold and offers Pikachu immediately")

        # Exercise actual mobile attribution and persistence for a boxed
        # companion, then an active companion whose singleton must stay in sync.
        def mobile_award(individual_id, xp=1, battles=1):
            _attribute_xp_and_evs_to_companion(
                individual_id, xp, {}, services.settings,
                battles_fought=battles, db=db, logger=services.logger,
            )

        mobile_award("pr914-box")
        assert _persisted(db, "pr914-box")["friendship"] == 260
        boxed = db.get_pokemon("pr914-box")
        boxed.update(friendship=400, held_item="soothe-bell")
        db.save_pokemon(boxed)
        mobile_award("pr914-box")
        assert _persisted(db, "pr914-box")["friendship"] == 409
        assert services.main_pokemon.friendship == 412
        mobile_award("pr914-main")
        assert _persisted(db, "pr914-main")["friendship"] == 418
        assert services.main_pokemon.friendship == 418
        before_noop = _persisted(db, "pr914-main")
        mobile_award("pr914-main", xp=0, battles=0)
        assert _persisted(db, "pr914-main") == before_noop
        print("PASS: mobile friendship crosses 255 and 400, preserving active and stored state")

    events.extend(driver.drain_events())
    errors = [
        e for e in events
        if e["type"] == "error" or (e["type"] == "log" and e.get("level") == "error")
    ]
    assert not errors, errors
    assert sum(e["type"] == "defeat" for e in events) == 2
    print("PR #914 Tier-1 proof PASSED")
    return True


if __name__ == "__main__":
    raise SystemExit(0 if run_proof() else 1)
