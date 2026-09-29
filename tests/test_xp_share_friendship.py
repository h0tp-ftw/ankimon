"""PR #914 proofs: XP Share rewards persist and can unlock friendship evolution."""

import importlib
import sqlite3
import sys
import types
from pathlib import Path
from unittest.mock import Mock

import pytest

from conftest import isolated_modules


class Settings(dict):
    def set(self, key, value):
        self[key] = value


@pytest.fixture
def xp_share(tmp_path, monkeypatch):
    # Other test modules install import-time mocks. Isolate the real logic and
    # registry, so these proofs exercise SQLite and the bundled evolution data
    # in both a standalone run and the full suite without leaking module state.
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
        db = database.AnkimonDB(logger, db_path=tmp_path / "ankimon.db")
        settings = Settings({
            "trainer.xp_share_mode": "classic",
            "misc.remove_level_cap": False,
            "evolution.friendship_time_enabled": True,
        })
        trainer.services.populate(db=db, logger=logger, settings=settings)
        rng = Mock(return_value=7)
        monkeypatch.setattr(trainer.random, "randint", rng)
        window = Mock()
        window.translator.translate.return_value = "Friendship evolution offered"

        def save(individual_id, **overrides):
            pokemon = {
                "individual_id": individual_id,
                "id": 25,
                "name": "pikachu",
                "level": 30,
                "xp": 10,
                "growth_rate": "medium-fast",
                "friendship": 100,
                "held_item": None,
                "attacks": ["tackle"],
                "pokemon_defeated": 0,
                "everstone": False,
                "evolution_rejected": False,
                "gender": "M",
            }
            pokemon.update(overrides)
            assert db.save_pokemon(pokemon)
            return pokemon

        def grant(exp=100, holder="holder", mode="classic", evo_window=window):
            settings["trainer.xp_share_mode"] = mode
            settings["trainer.xp_share"] = holder
            return trainer.xp_share_gain_exp(
                logger, settings, evo_window, "active", exp, holder,
            )

        def persisted(individual_id):
            # An independent SQLite connection proves the reward is committed,
            # including when read synchronously from inside an evolution prompt.
            with sqlite3.connect(db.db_path) as connection:
                row = connection.execute(
                    "SELECT data FROM captured_pokemon WHERE individual_id = ?",
                    (individual_id,),
                ).fetchone()
            return db._deobfuscate(row[0]) if row else None

        try:
            yield types.SimpleNamespace(
                trainer=trainer, db=db, settings=settings, window=window,
                rng=rng, save=save, grant=grant, persisted=persisted, logger=logger,
            )
        finally:
            db.close()


@pytest.mark.parametrize("roll", [5, 7, 9])
@pytest.mark.parametrize("held_item", [None, "soothe-bell", "lucky-egg"])
@pytest.mark.parametrize("mode", ["classic", "oras"])
def test_rewards_follow_share_mode_and_held_item(xp_share, mode, held_item, roll):
    s = xp_share
    active = s.save("active")
    s.save("holder", held_item=held_item, friendship=398)
    outsider = s.save("outside")
    s.db.save_team([{"individual_id": "active"}, {"individual_id": "holder"}])
    s.rng.return_value = roll

    assert s.grant(mode=mode) == (50 if mode == "classic" else 100)

    stored = s.persisted("holder")
    expected_xp = 50 if mode == "classic" else 100
    if held_item == "lucky-egg":
        expected_xp = int(expected_xp * 1.5)
    assert stored["xp"] == 10 + expected_xp
    assert stored["friendship"] == 398 + (
        int(roll * 1.5) if held_item == "soothe-bell" else roll
    )
    assert stored["level"] == 30
    assert s.persisted("active") == active
    assert s.persisted("outside") == outsider
    s.rng.assert_called_once_with(5, 9)
    s.window.ask_pokemon_evo.assert_not_called()


def test_oras_rewards_each_recipient_once_without_a_classic_holder(xp_share):
    s = xp_share
    active = s.save("active")
    s.save("first")
    s.save("second", held_item="soothe-bell")
    s.db.save_team([
        {"individual_id": "active"}, {"individual_id": "first"},
        {"individual_id": "released"}, {"individual_id": "second"},
    ])
    s.rng.side_effect = [5, 9]

    assert s.grant(mode="oras", holder=None) == 100

    assert s.persisted("first")["friendship"] == 105
    assert s.persisted("second")["friendship"] == 113
    assert s.persisted("first")["xp"] == s.persisted("second")["xp"] == 110
    assert s.persisted("active") == active
    assert s.persisted("released") is None
    assert s.rng.call_count == 2


@pytest.mark.parametrize("mode, exp", [
    ("classic", 0), ("classic", 1), ("classic", -10),
    ("oras", 0), ("oras", -10),
])
def test_nonpositive_recipient_share_has_no_side_effects(xp_share, mode, exp):
    s = xp_share
    before = s.save("holder", id=42, name="golbat", friendship=220)
    s.db.save_team([{"individual_id": "holder"}])

    s.grant(mode=mode, exp=exp)

    assert s.persisted("holder") == before
    s.rng.assert_not_called()
    s.window.ask_pokemon_evo.assert_not_called()


@pytest.mark.parametrize("holder", [None, "active", "released"])
def test_classic_does_not_reward_absent_or_active_holder(xp_share, holder):
    s = xp_share
    active = s.save("active")
    other = s.save("holder")

    s.grant(holder=holder)

    assert s.persisted("active") == active
    assert s.persisted("holder") == other
    assert s.persisted("released") is None
    if holder == "released":
        assert s.settings["trainer.xp_share"] is None
    s.rng.assert_not_called()
    s.window.ask_pokemon_evo.assert_not_called()


@pytest.mark.parametrize("mode", ["classic", "oras"])
def test_friendship_threshold_uses_committed_reward_before_evolution(xp_share, mode):
    s = xp_share
    s.save("holder", id=42, name="golbat", friendship=214)
    s.db.save_team([{"individual_id": "holder"}])

    def evolve(individual_id, prevo_id, evo_id):
        stored = s.persisted(individual_id)
        assert stored["friendship"] == 221
        assert stored["xp"] == (60 if mode == "classic" else 110)
        assert (prevo_id, evo_id) == (42, 169)
        stored.update(id=evo_id, name="crobat")
        s.db.save_pokemon(stored)

    s.window.ask_pokemon_evo.side_effect = evolve

    s.grant(mode=mode)

    s.window.ask_pokemon_evo.assert_called_once_with("holder", 42, 169)
    stored = s.persisted("holder")
    assert stored["friendship"] == 221
    assert stored["id"] == 169, "Grant must not overwrite synchronous evolution"


@pytest.mark.parametrize("blocker", ["everstone", "evolution_rejected", "disabled"])
def test_evolution_suppression_keeps_the_friendship_reward(xp_share, blocker):
    s = xp_share
    overrides = {blocker: True} if blocker != "disabled" else {}
    s.save("holder", id=42, name="golbat", friendship=214, **overrides)
    if blocker == "disabled":
        s.settings["evolution.friendship_time_enabled"] = False

    s.grant()

    assert s.persisted("holder")["friendship"] == 221
    s.window.ask_pokemon_evo.assert_not_called()


def test_prompt_failure_cannot_lose_earned_friendship(xp_share):
    s = xp_share
    s.save("holder", id=42, name="golbat", friendship=214)
    s.window.ask_pokemon_evo.side_effect = RuntimeError("Evolution window closed")

    assert s.grant() == 50

    stored = s.persisted("holder")
    assert stored["friendship"] == 221
    assert stored["xp"] == 60
    s.logger.log.assert_any_call(
        "error", "XP Share evolution prompt failed for holder: Evolution window closed",
    )


def test_deleted_window_cannot_stop_later_oras_rewards(xp_share):
    s = xp_share
    for individual_id in ("first", "second"):
        s.save(individual_id, id=42, name="golbat", friendship=214)
    s.db.save_team([{"individual_id": "first"}, {"individual_id": "second"}])
    s.window.ask_pokemon_evo.side_effect = RuntimeError(
        "wrapped C/C++ object of type EvoWindow has been deleted",
    )

    assert s.grant(mode="oras") == 100

    for individual_id in ("first", "second"):
        assert s.persisted(individual_id)["friendship"] == 221
        assert s.persisted(individual_id)["xp"] == 110
        s.logger.log.assert_any_call(
            "error", f"XP Share evolution prompt failed for {individual_id}: "
            "wrapped C/C++ object of type EvoWindow has been deleted",
        )
    assert s.window.ask_pokemon_evo.call_count == 2


def test_missing_evolution_window_preserves_all_oras_rewards(xp_share):
    s = xp_share
    for individual_id in ("first", "second"):
        s.save(individual_id, id=42, name="golbat", friendship=214)
    s.db.save_team([{"individual_id": "first"}, {"individual_id": "second"}])

    assert s.grant(mode="oras", evo_window=None) == 100

    for individual_id in ("first", "second"):
        assert s.persisted(individual_id)["friendship"] == 221
        assert s.persisted(individual_id)["xp"] == 110
