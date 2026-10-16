"""Issue #943: XP Share recipients learn the level-up moves of every level gained.

Free slots are filled silently; a full moveset gets ONE ``choose_moveset``
prompt per Pokemon per battle, never one dialog per move.
"""

import importlib
import sys
import types
from pathlib import Path
from unittest.mock import Mock

import pytest

from conftest import isolated_modules


class Settings(dict):
    def set(self, key, value):
        """Store a setting, mirroring the real Settings API."""
        self[key] = value


# Gen 9 learnset facts the scenarios below rely on (src/Ankimon/data_files/learnsets.json):
#   charmander: ember @4, smokescreen @8        (medium-slow growth)
#   bulbasaur:  poisonpowder + sleeppowder @15   (medium-slow growth)
CHARMANDER = {"id": 4, "name": "charmander", "growth_rate": "medium-slow"}
BULBASAUR = {"id": 1, "name": "bulbasaur", "growth_rate": "medium-slow"}
FULL_BULBASAUR_SET = ["tackle", "growl", "vinewhip", "razorleaf"]


@pytest.fixture
def xp_share(tmp_path, monkeypatch):
    """Isolated SQLite + headless trainer_functions with a scripted UI presenter."""
    with isolated_modules("Ankimon", "aqt"):
        src = Path(__file__).resolve().parents[1] / "src"
        for name in ("Ankimon", "Ankimon.functions", "Ankimon.pyobj"):
            package = types.ModuleType(name)
            package.__path__ = [str(src / name.replace(".", "/"))]
            package.__package__ = name
            sys.modules[name] = package
        sys.modules["aqt"] = None
        monkeypatch.setenv("ANKIMON_USER_PATH", str(tmp_path))

        trainer = importlib.import_module("Ankimon.functions.trainer_functions")
        database = importlib.import_module("Ankimon.pyobj.database_manager")
        logger = Mock()
        ui = Mock()
        ui.choose_moveset.return_value = None
        db = database.AnkimonDB(logger, db_path=tmp_path / "ankimon.db")
        settings = Settings({
            "trainer.xp_share_mode": "classic",
            "misc.remove_level_cap": False,
            "evolution.friendship_time_enabled": False,
        })
        trainer.services.populate(db=db, logger=logger, settings=settings, ui=ui)
        window = Mock()
        window.translator.translate.return_value = ""

        def save(individual_id, species, level, attacks):
            """Persist a minimal Pokemon record of ``species`` at ``level``."""
            pokemon = {
                "individual_id": individual_id,
                "level": level,
                "xp": 0,
                "friendship": 70,
                "held_item": None,
                "attacks": list(attacks) if isinstance(attacks, list) else attacks,
                "pokemon_defeated": 0,
                "everstone": False,
                "evolution_rejected": False,
                "gender": "M",
                **species,
            }
            assert db.save_pokemon(pokemon)
            return pokemon

        def xp_to_reach(species, start, target):
            """XP that lifts ``species`` from ``start`` to exactly ``target``."""
            # The level-up loop advances while exp exceeds the current level's
            # requirement, so +1 guarantees the final level is crossed.
            needed = sum(
                int(trainer.find_experience_for_level(species["growth_rate"], lvl, False))
                for lvl in range(start, target)
            )
            return needed + 1

        def grant(holder_exp, mode="classic"):
            """Run the XP Share award so the holder receives ``holder_exp``."""
            # Classic mode halves the reward on its way to the holder.
            exp = holder_exp * 2 if mode == "classic" else holder_exp
            settings["trainer.xp_share_mode"] = mode
            settings["trainer.xp_share"] = "holder"
            return trainer.xp_share_gain_exp(logger, settings, window, "active", exp, "holder")

        try:
            yield types.SimpleNamespace(
                trainer=trainer, db=db, settings=settings, ui=ui, logger=logger,
                save=save, grant=grant, xp_to_reach=xp_to_reach,
            )
        finally:
            db.close()


def test_free_slot_learns_move_without_prompt(xp_share):
    """A free slot takes the new move silently; no dialog."""
    s = xp_share
    s.save("active", CHARMANDER, 50, ["flamethrower"])
    s.save("holder", CHARMANDER, 3, ["scratch", "growl"])
    s.db.save_team([{"individual_id": "active"}, {"individual_id": "holder"}])

    s.grant(s.xp_to_reach(CHARMANDER, 3, 4))

    holder = s.db.get_pokemon("holder")
    assert holder["level"] == 4
    assert holder["attacks"] == ["scratch", "growl", "ember"]
    s.ui.choose_moveset.assert_not_called()


def test_multi_level_jump_collects_moves_from_every_level(xp_share):
    """Moves from every gained level are learned, not just the last one."""
    s = xp_share
    s.save("active", CHARMANDER, 50, ["flamethrower"])
    s.save("holder", CHARMANDER, 3, ["scratch", "growl"])
    s.db.save_team([{"individual_id": "active"}, {"individual_id": "holder"}])

    s.grant(s.xp_to_reach(CHARMANDER, 3, 8))

    holder = s.db.get_pokemon("holder")
    assert holder["level"] == 8
    assert holder["attacks"] == ["scratch", "growl", "ember", "smokescreen"]
    s.ui.choose_moveset.assert_not_called()


def test_full_moveset_prompts_once_with_every_new_move(xp_share):
    """A full set gets one prompt listing all new moves; the answer is stored slot-wise."""
    s = xp_share
    s.save("active", BULBASAUR, 50, ["solarbeam"])
    s.save("holder", BULBASAUR, 14, FULL_BULBASAUR_SET)
    s.db.save_team([{"individual_id": "active"}, {"individual_id": "holder"}])
    chosen = ["tackle", "vinewhip", "poisonpowder", "sleeppowder"]
    s.ui.choose_moveset.return_value = chosen

    s.grant(s.xp_to_reach(BULBASAUR, 14, 15))

    s.ui.choose_moveset.assert_called_once_with(
        "Bulbasaur", FULL_BULBASAUR_SET, ["poisonpowder", "sleeppowder"]
    )
    holder = s.db.get_pokemon("holder")
    assert holder["level"] == 15
    # Retained moves keep their slots; new moves drop into the vacated ones
    # (growl -> poisonpowder, razorleaf -> sleeppowder) in candidate order.
    assert holder["attacks"] == ["tackle", "poisonpowder", "vinewhip", "sleeppowder"]


def test_declining_prompt_keeps_current_moves(xp_share):
    """Cancelling the prompt keeps the current moves but still levels up."""
    s = xp_share
    s.save("active", BULBASAUR, 50, ["solarbeam"])
    s.save("holder", BULBASAUR, 14, FULL_BULBASAUR_SET)
    s.db.save_team([{"individual_id": "active"}, {"individual_id": "holder"}])
    s.ui.choose_moveset.return_value = None

    s.grant(s.xp_to_reach(BULBASAUR, 14, 15))

    holder = s.db.get_pokemon("holder")
    assert holder["level"] == 15
    assert holder["attacks"] == FULL_BULBASAUR_SET


@pytest.mark.parametrize(
    "bad_answer",
    [
        ["tackle", "hyperbeam", "vinewhip", "sleeppowder"],  # move not offered
        ["tackle", "vinewhip", "sleeppowder"],  # too few
        ["tackle", "tackle", "vinewhip", "sleeppowder"],  # duplicate
        "tackle",  # wrong type
    ],
)
def test_invalid_prompt_answer_keeps_current_moves(xp_share, bad_answer):
    """Malformed presenter answers are treated as cancel and logged as a warning."""
    s = xp_share
    s.save("active", BULBASAUR, 50, ["solarbeam"])
    s.save("holder", BULBASAUR, 14, FULL_BULBASAUR_SET)
    s.db.save_team([{"individual_id": "active"}, {"individual_id": "holder"}])
    s.ui.choose_moveset.return_value = bad_answer

    s.grant(s.xp_to_reach(BULBASAUR, 14, 15))

    holder = s.db.get_pokemon("holder")
    assert holder["level"] == 15
    assert holder["attacks"] == FULL_BULBASAUR_SET
    assert any(call.args[0] == "warning" for call in s.logger.log.call_args_list)


def test_prompt_failure_still_saves_the_reward(xp_share):
    """A crashing prompt must not lose the level, XP or friendship reward."""
    s = xp_share
    s.save("active", BULBASAUR, 50, ["solarbeam"])
    s.save("holder", BULBASAUR, 14, FULL_BULBASAUR_SET)
    s.db.save_team([{"individual_id": "active"}, {"individual_id": "holder"}])
    s.ui.choose_moveset.side_effect = RuntimeError("dialog exploded")

    s.grant(s.xp_to_reach(BULBASAUR, 14, 15))

    holder = s.db.get_pokemon("holder")
    assert holder["level"] == 15
    assert holder["friendship"] > 70
    assert holder["attacks"] == FULL_BULBASAUR_SET


def test_new_moves_are_saved_before_the_evolution_check(xp_share, monkeypatch):
    """Learned moves are persisted and passed to the evolution check."""
    s = xp_share
    seen = {}

    def fake_evolution_check(individual_id, pokemon_id, level, evo_window, *args, **kwargs):
        """Record what the evolution check sees instead of prompting."""
        seen["current_attacks"] = list(kwargs.get("current_attacks") or [])
        seen["persisted"] = s.db.get_pokemon(individual_id)["attacks"]
        return None

    monkeypatch.setattr(s.trainer, "check_evolution_for_pokemon", fake_evolution_check)
    s.save("active", CHARMANDER, 50, ["flamethrower"])
    s.save("holder", CHARMANDER, 3, ["scratch", "growl"])
    s.db.save_team([{"individual_id": "active"}, {"individual_id": "holder"}])

    s.grant(s.xp_to_reach(CHARMANDER, 3, 4))

    # A move-based evolution must see the move learned on this very level,
    # and the record must already be committed with it when the check runs.
    assert "ember" in seen["current_attacks"]
    assert "ember" in seen["persisted"]


def test_oras_mode_teaches_every_teammate_but_not_the_active_one(xp_share):
    """ORAS mode grants moves to every teammate except the active Pokemon."""
    s = xp_share
    s.save("active", CHARMANDER, 3, ["scratch", "growl"])
    s.save("holder", CHARMANDER, 3, ["scratch", "growl"])
    s.save("mate", CHARMANDER, 3, ["scratch"])
    s.db.save_team([
        {"individual_id": "active"},
        {"individual_id": "holder"},
        {"individual_id": "mate"},
    ])

    s.grant(s.xp_to_reach(CHARMANDER, 3, 4), mode="oras")

    assert s.db.get_pokemon("holder")["attacks"] == ["scratch", "growl", "ember"]
    assert s.db.get_pokemon("mate")["attacks"] == ["scratch", "ember"]
    # The active Pokemon's level-up is handled by encounter_functions, not here.
    assert s.db.get_pokemon("active")["attacks"] == ["scratch", "growl"]
    s.ui.choose_moveset.assert_not_called()


def test_bulk_resolve_never_prompts(xp_share, monkeypatch):
    """During bulk resolve no dialog opens and the moveset stays unchanged."""
    s = xp_share
    utils = importlib.import_module("Ankimon.utils")
    monkeypatch.setattr(utils, "in_bulk_resolve", True, raising=False)
    s.save("active", BULBASAUR, 50, ["solarbeam"])
    s.save("holder", BULBASAUR, 14, FULL_BULBASAUR_SET)
    s.db.save_team([{"individual_id": "active"}, {"individual_id": "holder"}])

    s.grant(s.xp_to_reach(BULBASAUR, 14, 15))

    s.ui.choose_moveset.assert_not_called()
    assert s.db.get_pokemon("holder")["attacks"] == FULL_BULBASAUR_SET


@pytest.mark.parametrize(
    "stored",
    [
        "not json at all",  # unparseable string
        "\"tackle\"",  # JSON, but a scalar
        42,  # scalar
        ["tackle", 5],  # list with a non-id entry
        None,  # missing
    ],
)
def test_unreadable_stored_moves_are_never_overwritten(xp_share, stored):
    """A corrupt ``attacks`` value is left exactly as stored; only the level-up is saved."""
    s = xp_share
    s.save("active", CHARMANDER, 50, ["flamethrower"])
    s.save("holder", CHARMANDER, 3, stored)
    s.db.save_team([{"individual_id": "active"}, {"individual_id": "holder"}])

    s.grant(s.xp_to_reach(CHARMANDER, 3, 4))

    holder = s.db.get_pokemon("holder")
    assert holder["level"] == 4
    assert holder["attacks"] == stored
    s.ui.choose_moveset.assert_not_called()
    assert any(call.args[0] == "error" for call in s.logger.log.call_args_list)
