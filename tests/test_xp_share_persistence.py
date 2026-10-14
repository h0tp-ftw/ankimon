"""XP Share move prompts must not overwrite changes committed while they are open."""

import importlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from test_xp_share_moves import (
    BULBASAUR,
    CHARMANDER,
    FULL_BULBASAUR_SET,
    xp_share,
)


CHOICE = ["tackle", "vinewhip", "poisonpowder", "sleeppowder"]
CHOSEN_SLOTS = ["tackle", "poisonpowder", "vinewhip", "sleeppowder"]


def _persisted(s, individual_id="holder", db_path=None):
    """Read from another SQLite connection, proving changes are committed."""
    with sqlite3.connect(db_path or s.db.db_path) as connection:
        row = connection.execute(
            "SELECT data FROM captured_pokemon WHERE individual_id = ?",
            (individual_id,),
        ).fetchone()
    return s.db._deobfuscate(row[0]) if row else None


def _full_holder(s, monkeypatch):
    s.save("active", BULBASAUR, 50, ["solarbeam"])
    s.save("holder", BULBASAUR, 14, FULL_BULBASAUR_SET)
    s.db.save_team([{"individual_id": "active"}, {"individual_id": "holder"}])
    monkeypatch.setattr(s.trainer.random, "randint", lambda *args: 1)
    monkeypatch.setattr(
        s.trainer, "check_evolution_for_pokemon", lambda *args, **kwargs: None
    )


def _commit_mobile_reward(s):
    """Use the production mobile reward writer on its own database thread."""
    mobile = importlib.import_module("Ankimon.functions.mobile_sync")

    def award():
        before = s.db.get_pokemon("holder")
        mobile._attribute_xp_and_evs_to_companion(
            "holder",
            s.xp_to_reach(BULBASAUR, before["level"], 17),
            {"atk": 2},
            s.settings,
            battles_fought=3,
            db=s.db,
            logger=s.logger,
            xp_share=True,
        )
        latest = s.db.get_pokemon("holder")
        latest.update(
            nickname="Phone renamed me", held_item="leftovers", custom_note="keep me"
        )
        assert s.db.save_pokemon(latest)
        return latest

    # Completion happens before the presenter returns, reproducing a worker
    # finishing while the modal event loop still owns the old desktop snapshot.
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(award).result(timeout=10)


@pytest.mark.parametrize("outcome", ["cancel", "accept", "exception"])
def test_prompt_preserves_a_committed_mobile_reward(xp_share, monkeypatch, outcome):
    s = xp_share
    _full_holder(s, monkeypatch)
    seen = {}

    def answer(*args):
        seen["at_prompt"] = _persisted(s)
        seen["mobile"] = _commit_mobile_reward(s)
        seen["after_mobile"] = _persisted(s)
        if outcome == "exception":
            raise RuntimeError("dialog closed after the mobile worker finished")
        return CHOICE if outcome == "accept" else None

    s.ui.choose_moveset.side_effect = answer

    s.grant(s.xp_to_reach(BULBASAUR, 14, 15))

    # The desktop XP and friendship are durable before any modal input.
    assert seen["at_prompt"]["level"] == 15
    assert seen["at_prompt"]["xp"] == 1
    assert seen["at_prompt"]["friendship"] == 71
    assert seen["after_mobile"] == seen["mobile"]
    assert seen["mobile"]["level"] == 17
    assert seen["mobile"]["friendship"] == 72
    assert seen["mobile"]["pokemon_defeated"] == 3
    expected = dict(seen["mobile"])
    expected["attacks"] = CHOSEN_SLOTS if outcome == "accept" else FULL_BULBASAUR_SET
    assert _persisted(s) == expected
    s.ui.choose_moveset.assert_called_once()


@pytest.mark.parametrize("outcome", ["cancel", "accept", "exception"])
def test_autofilled_moves_survive_prompt_and_newer_rewards(
    xp_share, monkeypatch, outcome
):
    s = xp_share
    _full_holder(s, monkeypatch)
    original = ["tackle", "growl", "vinewhip"]
    s.save("holder", BULBASAUR, 14, original)
    seen = {}

    def answer(display_name, attacks, candidates):
        seen["offered"] = (list(attacks), list(candidates))
        seen["mobile"] = _commit_mobile_reward(s)
        if outcome == "exception":
            raise RuntimeError("dialog failed after automatic learning")
        return CHOICE if outcome == "accept" else None

    s.ui.choose_moveset.side_effect = answer

    s.grant(s.xp_to_reach(BULBASAUR, 14, 15))

    autofilled = original + ["poisonpowder"]
    assert seen["offered"] == (autofilled, ["sleeppowder"])
    expected = dict(seen["mobile"])
    expected["attacks"] = (
        ["tackle", "sleeppowder", "vinewhip", "poisonpowder"]
        if outcome == "accept"
        else autofilled
    )
    assert _persisted(s) == expected
    assert expected["level"] == 17
    assert expected["friendship"] == 72
    s.ui.choose_moveset.assert_called_once()


@pytest.mark.parametrize(
    "change",
    [
        {"attacks": ["tackle", "growl", "leechseed", "razorleaf"]},
        {"id": 2, "name": "ivysaur"},
        {"attacks": "damaged moves written by another editor"},
        {"attacks": None},
    ],
    ids=["changed-moves", "evolved-species", "corrupt-moves", "missing-moves"],
)
def test_stale_move_answer_keeps_the_newer_record(xp_share, monkeypatch, change):
    s = xp_share
    _full_holder(s, monkeypatch)
    seen = {}

    def answer(*args):
        latest = s.db.get_pokemon("holder")
        latest.update(change)
        latest.update(level=19, xp=47, nickname="Another editor")
        assert s.db.save_pokemon(latest)
        seen["updated"] = latest
        return CHOICE

    s.ui.choose_moveset.side_effect = answer

    s.grant(s.xp_to_reach(BULBASAUR, 14, 15))

    assert _persisted(s) == seen["updated"]
    s.ui.choose_moveset.assert_called_once()


@pytest.mark.parametrize("outcome", ["cancel", "accept", "exception"])
def test_prompt_cannot_resurrect_a_deleted_recipient(xp_share, monkeypatch, outcome):
    s = xp_share
    _full_holder(s, monkeypatch)
    deleted = []

    def answer(*args):
        deleted.append(s.db.delete_pokemon("holder"))
        if outcome == "exception":
            raise RuntimeError("recipient was released while the dialog was open")
        return CHOICE if outcome == "accept" else None

    s.ui.choose_moveset.side_effect = answer

    s.grant(s.xp_to_reach(BULBASAUR, 14, 15))

    assert deleted == [True]
    assert _persisted(s) is None


def test_oras_keeps_distributing_after_recipient_disappears(xp_share, monkeypatch):
    s = xp_share
    _full_holder(s, monkeypatch)
    active = _persisted(s, "active")
    s.save("mate", CHARMANDER, 3, ["scratch"])
    s.db.save_team(
        [
            {"individual_id": "active"},
            {"individual_id": "holder"},
            {"individual_id": "mate"},
        ]
    )

    def answer(*args):
        s.db.delete_pokemon("holder")
        return CHOICE

    s.ui.choose_moveset.side_effect = answer

    award = s.xp_to_reach(BULBASAUR, 14, 15)
    assert s.grant(award, mode="oras") == award

    assert _persisted(s) is None
    assert _persisted(s, "active") == active
    mate = _persisted(s, "mate")
    assert mate["level"] > 3
    assert mate["friendship"] == 71
    assert "ember" in mate["attacks"]


@pytest.mark.parametrize("mode", ["classic", "oras"])
@pytest.mark.parametrize("switch_kind", ["manager-replaced", "same-manager"])
def test_save_switch_during_prompt_does_not_modify_destination(
    xp_share, monkeypatch, mode, switch_kind
):
    s = xp_share
    _full_holder(s, monkeypatch)
    s.save("mate", CHARMANDER, 3, ["scratch"])
    s.db.save_team(
        [
            {"individual_id": "active"},
            {"individual_id": "holder"},
            {"individual_id": "mate"},
        ]
    )
    original_path = s.db.db_path
    destination_path = original_path.with_name("other-save.db")
    destination = type(s.db)(s.logger, db_path=destination_path)
    target = dict(s.db.get_pokemon("holder"), level=30, xp=99, nickname="Other save")
    mate = dict(s.db.get_pokemon("mate"), level=40, xp=321, nickname="Other teammate")
    assert destination.save_pokemon(target)
    assert destination.save_pokemon(mate)
    checks = []
    switched = []
    monkeypatch.setattr(
        s.trainer,
        "check_evolution_for_pokemon",
        lambda *args, **kwargs: checks.append(args),
    )

    def answer(*args):
        if switch_kind == "manager-replaced":
            s.trainer.services.db = destination
        else:
            assert s.db.switch_database(destination_path.name)
        switched.append(True)
        return CHOICE

    s.ui.choose_moveset.side_effect = answer

    try:
        s.grant(s.xp_to_reach(BULBASAUR, 14, 15), mode=mode)

        assert switched == [True]
        assert _persisted(s, db_path=destination_path) == target
        assert _persisted(s, "mate", destination_path) == mate
        assert checks == []
        original = _persisted(s, db_path=original_path)
        assert original["level"] == 15
        assert original["friendship"] == 71
        assert original["attacks"] == FULL_BULBASAUR_SET
    finally:
        destination.close()


def test_evolution_reads_latest_reward_and_committed_move_choice(xp_share, monkeypatch):
    s = xp_share
    _full_holder(s, monkeypatch)
    checks = []
    seen = {}

    def answer(*args):
        seen["mobile"] = _commit_mobile_reward(s)
        return CHOICE

    def check(individual_id, species_id, level, window, *args, **kwargs):
        checks.append(
            (species_id, level, kwargs["current_attacks"], _persisted(s, individual_id))
        )
        return None

    s.ui.choose_moveset.side_effect = answer
    monkeypatch.setattr(s.trainer, "check_evolution_for_pokemon", check)

    s.grant(s.xp_to_reach(BULBASAUR, 14, 15))

    expected = dict(seen["mobile"], attacks=CHOSEN_SLOTS)
    assert checks == [(1, 17, CHOSEN_SLOTS, expected)]
    assert _persisted(s) == expected
