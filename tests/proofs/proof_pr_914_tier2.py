#!/usr/bin/env python3
"""PR #914: real XP Share friendship evolution acceptance and cancellation.

Run with the Tier-2 Python environment in a fresh interpreter. The genuine
EvoWindow buttons act on a throwaway SQLite profile. Set
ANKIMON_PR914_SCREENSHOT to capture the real friendship evolution prompt.
"""

import json
import os
from pathlib import Path
import random
import sqlite3
import sys
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


def _persisted(db, individual_id):
    with sqlite3.connect(db.db_path) as conn:
        row = conn.execute(
            "SELECT data FROM captured_pokemon WHERE individual_id = ?",
            (individual_id,),
        ).fetchone()
    assert row is not None, individual_id
    return json.loads(row[0])


def run_proof():
    from harness.real_driver import RealDriver

    random.seed(914)
    driver = RealDriver(
        first_encounter=False,
        settings_overrides={
            "trainer.xp_share_mode": "classic",
            "gui.show_sprites_across_ankimon": False,
            "audio.sounds": False,
            "audio.sound_effects": False,
        },
    )
    from Ankimon.functions.friendship_evolution import get_friendship_evolutions_for_species
    from Ankimon.functions.trainer_functions import xp_share_gain_exp
    from Ankimon.pyobj.evolution_window import EvoWindow
    from Ankimon.singletons import get_evo_window
    from harness.fixtures import build_pokemon
    from PyQt6 import sip
    from PyQt6.QtWidgets import QPushButton

    services, app = driver.services, driver.env.app
    db = services.db
    evo_window = get_evo_window()
    assert isinstance(evo_window, EvoWindow)
    threshold = next(
        entry.min_happiness for entry in get_friendship_evolutions_for_species(172)
        if entry.evo_id == 25
    )

    def add_pichu(individual_id):
        pokemon = build_pokemon({
            "species": "Pichu", "level": 50, "friendship": threshold - 2,
            "individual_id": individual_id, "moves": ["thundershock"],
            "nickname": "Friendship proof",
        })
        db.save_pokemon(pokemon.to_dict())

    def fixed_shared_friendship(low, high):
        assert (low, high) == (1, 2), "XP Share must use the reduced 1–2 range"
        return 2

    def grant(individual_id):
        with patch("random.randint", side_effect=fixed_shared_friendship):
            active_xp = xp_share_gain_exp(
                services.logger, services.settings, evo_window,
                services.main_pokemon.individual_id, 2, individual_id,
            )
        assert active_xp == 1
        app.processEvents()

    def click_button(text):
        layout = evo_window.layout()
        buttons = [
            layout.itemAt(index).widget() for index in range(layout.count())
            if isinstance(layout.itemAt(index).widget(), QPushButton)
            and layout.itemAt(index).widget().isVisible()
            and layout.itemAt(index).widget().text() == text
        ]
        assert len(buttons) == 1, (text, [b.text() for b in buttons])
        buttons[0].click()
        app.processEvents()

    try:
        add_pichu("pr914-accept")
        grant("pr914-accept")
        assert evo_window.isVisible(), "Threshold-crossing XP Share must show the real prompt"
        before = _persisted(db, "pr914-accept")
        assert before["friendship"] == threshold and before["xp"] == 1
        screenshot_path = os.environ.get("ANKIMON_PR914_SCREENSHOT")
        if screenshot_path:
            assert evo_window.grab().save(screenshot_path)
        click_button("Evolve Pokémon")
        accepted = _persisted(db, "pr914-accept")
        assert accepted["id"] == 25, accepted
        assert accepted["friendship"] == threshold
        assert accepted["nickname"] == "Friendship proof"
        assert accepted["evolution_rejected"] is False
        evo_window.close()
        app.processEvents()
        print("PASS: real XP Share friendship prompt evolves Pichu and preserves the award")

        add_pichu("pr914-cancel")
        grant("pr914-cancel")
        click_button("Cancel Evolution")
        cancelled = _persisted(db, "pr914-cancel")
        assert cancelled["id"] == 172
        assert cancelled["friendship"] == threshold
        assert cancelled["evolution_rejected"] is True
        assert not evo_window.isVisible()
        grant("pr914-cancel")
        after = _persisted(db, "pr914-cancel")
        assert after["friendship"] == threshold + 2
        assert after["xp"] == 2 and after["evolution_rejected"] is True
        assert not evo_window.isVisible(), "A rejected evolution must not prompt on the next award"
        print("PASS: real cancellation persists and later XP Share awards do not reopen the prompt")

        errors = [
            event for event in driver.drain_events()
            if event["type"] == "error"
            or (event["type"] == "log" and event.get("level") == "error")
        ]
        assert not errors, errors

        # Destroy the genuine C++ widget, as can happen during profile teardown.
        # A ready-to-evolve recipient must retain its award and must not prevent
        # later ORAS team recipients from receiving theirs.
        add_pichu("pr914-deleted-window")
        later = build_pokemon({
            "species": "Snorlax", "level": 50, "friendship": 400,
            "individual_id": "pr914-later-teammate",
        })
        db.save_pokemon(later.to_dict())
        db.save_team([
            {"individual_id": "pr914-deleted-window"},
            {"individual_id": "pr914-later-teammate"},
        ])
        driver.set_setting("trainer.xp_share_mode", "oras")
        sip.delete(evo_window)
        assert sip.isdeleted(evo_window)
        with patch("random.randint", side_effect=fixed_shared_friendship):
            active_xp = xp_share_gain_exp(
                services.logger, services.settings, evo_window,
                services.main_pokemon.individual_id, 2, None,
            )
        assert active_xp == 2
        threshold_recipient = _persisted(db, "pr914-deleted-window")
        assert threshold_recipient["friendship"] == threshold
        assert threshold_recipient["xp"] == 2
        later_recipient = _persisted(db, "pr914-later-teammate")
        assert later_recipient["friendship"] == 402 and later_recipient["xp"] == 2
        diagnostics = driver.drain_events()
        assert not any(event["type"] == "error" for event in diagnostics)
        expected_errors = [
            event for event in diagnostics
            if event["type"] == "log" and event.get("level") == "error"
        ]
        assert len(expected_errors) == 1, expected_errors
        assert "XP Share evolution prompt failed" in expected_errors[0]["message"]
        assert "deleted" in expected_errors[0]["message"]
        print("PASS: a deleted real EvoWindow preserves awards for every ORAS teammate")
        print("PR #914 Tier-2 proof PASSED")
        return True
    finally:
        if not sip.isdeleted(evo_window):
            evo_window.close()
        app.processEvents()


if __name__ == "__main__":
    raise SystemExit(0 if run_proof() else 1)
