"""Regression coverage for encounter, evolution, and startup fixes."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from harness.driver import Driver


def run_probe():
    """Exercise the merged regressions against the real headless core."""
    driver = Driver(first_encounter=False)

    # Encounter tier labels are strings; enabling the rare encounter popup must
    # not turn an ordinary encounter into a TypeError.
    driver.set_setting("gui.pop_up_dialog_message_on_encounter", True)
    events = driver.encounter()
    assert any(event["type"] == "encounter" for event in events), events
    assert not [event for event in events if event["type"] == "error"], events

    # Force every tier and a shiny ordinary encounter so RNG cannot bypass the
    # comparison or leave the notification side of the regression untested.
    from Ankimon.functions import encounter_functions

    generated = list(
        encounter_functions.generate_random_pokemon(
            driver.services.main_pokemon.level, driver.services.tracker
        )
    )
    for tier, shiny, popup in (
        ("Normal", False, False),
        ("Baby", False, False),
        ("Starter", False, True),
        ("Ultra", False, True),
        ("Gmax", False, True),
        ("Legendary", False, True),
        ("Mega", False, True),
        ("Mythical", False, True),
        ("Normal", True, True),
    ):
        generated[14], generated[16] = tier, shiny
        with patch.object(
            encounter_functions,
            "generate_random_pokemon",
            return_value=tuple(generated),
        ):
            with patch.object(
                driver.services.logger, "log_and_showinfo"
            ) as notification:
                events = driver.encounter()
        assert not [event for event in events if event["type"] == "error"], events
        assert notification.call_count == int(popup), (
            tier,
            shiny,
            notification.call_args_list,
        )

    from Ankimon.functions.friendship_evolution import evolution_readiness
    from Ankimon.functions.pokedex_functions import check_evolution_by_item

    for species, level, gender, expected in (
        (281, 30, "M", 282),
        (361, 42, "F", 362),
    ):
        result = evolution_readiness({"id": species, "level": level, "gender": gender})
        assert (result["method"], result["evo_id"], result["ready"]) == (
            "level",
            expected,
            True,
        )
        assert result["item_status_text"]

    for region, expected in (("Kanto", 26), ("No Region", 26), ("Alola", 10100)):
        driver.set_setting("misc.active_region", region)
        assert check_evolution_by_item(25, 83, gender="M") == expected
    driver.set_setting("misc.active_region", "Kanto")
    assert check_evolution_by_item(123, 10001) == 900

    # The first tracker window must read the setting saved in the prior run.
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    with tempfile.TemporaryDirectory(prefix="ankimon_round_setting_") as profile:
        init = (
            "from harness.driver import Driver; import sys; "
            "d=Driver(user_path=sys.argv[1],first_encounter=False); "
            "d.set_setting('battle.cards_per_round',5)"
        )
        check = (
            "from harness.driver import Driver; import sys; "
            "d=Driver(user_path=sys.argv[1],first_encounter=False); "
            "assert d.services.tracker.cards_until_calc_multiplier == 5"
        )
        subprocess.run([sys.executable, "-c", init, profile], check=True, env=env)
        subprocess.run([sys.executable, "-c", check, profile], check=True, env=env)

    print("probe_recent_merges: OK")


if __name__ == "__main__":
    run_probe()
