"""PR #945: a confirmed moveset must retain every selected unique move."""

import json

import pytest

from test_xp_share_moves import BULBASAUR, xp_share  # noqa: F401


def test_duplicate_trade_moves_store_every_confirmed_move(xp_share):
    """Trade's repeated filler moves must not crowd selected new moves out."""
    s = xp_share
    s.save("active", BULBASAUR, 50, ["solarbeam"])
    s.save("holder", BULBASAUR, 14, ["tackle", "growl", "tackle", "tackle"])
    s.db.save_team([{"individual_id": "active"}, {"individual_id": "holder"}])
    s.ui.choose_moveset.return_value = [
        "tackle",
        "growl",
        "poisonpowder",
        "sleeppowder",
    ]

    s.grant(s.xp_to_reach(BULBASAUR, 14, 15))

    holder = s.db.get_pokemon("holder")
    assert holder["level"] == 15
    assert holder["attacks"] == [
        "tackle",
        "growl",
        "poisonpowder",
        "sleeppowder",
    ]


@pytest.mark.parametrize("encoded", [False, True], ids=["list-moves", "json-moves"])
def test_duplicate_move_choice_preserves_a_mobile_reward(
    xp_share, monkeypatch, encoded
):
    """Duplicate cleanup and a worker's reward must both survive the modal."""
    from test_xp_share_persistence import _commit_mobile_reward

    s = xp_share
    original = ["tackle", "growl", "tackle", "tackle"]
    chosen = ["tackle", "growl", "poisonpowder", "sleeppowder"]
    s.save("active", BULBASAUR, 50, ["solarbeam"])
    s.save("holder", BULBASAUR, 14, json.dumps(original) if encoded else original)
    s.db.save_team([{"individual_id": "active"}, {"individual_id": "holder"}])
    monkeypatch.setattr(s.trainer.random, "randint", lambda *args: 1)
    monkeypatch.setattr(
        s.trainer, "check_evolution_for_pokemon", lambda *args, **kwargs: None
    )
    seen = {}

    def answer(*args):
        assert s.db.get_pokemon("holder")["level"] == 15
        seen["mobile"] = _commit_mobile_reward(s)
        return chosen

    s.ui.choose_moveset.side_effect = answer

    s.grant(s.xp_to_reach(BULBASAUR, 14, 15))

    assert seen["mobile"]["level"] == 17
    assert s.db.get_pokemon("holder") == dict(seen["mobile"], attacks=chosen)
    s.ui.choose_moveset.assert_called_once()


@pytest.mark.parametrize(
    ("attacks", "new_attacks", "chosen", "expected"),
    [
        (
            ["tackle", "growl", "vinewhip", "razorleaf"],
            ["poisonpowder", "sleeppowder"],
            ["sleeppowder", "vinewhip", "poisonpowder", "tackle"],
            ["tackle", "poisonpowder", "vinewhip", "sleeppowder"],
        ),
        (
            ["Tackle", "Growl", "tackle", "TACKLE"],
            ["poisonpowder", "sleeppowder"],
            ["Tackle", "Growl", "poisonpowder", "sleeppowder"],
            ["Tackle", "Growl", "poisonpowder", "sleeppowder"],
        ),
        (
            ["tackle", "growl", "tackle", "tackle", "tackle"],
            ["poisonpowder", "sleeppowder"],
            ["tackle", "growl", "poisonpowder", "sleeppowder"],
            ["tackle", "growl", "poisonpowder", "sleeppowder"],
        ),
        (
            ["tackle", "growl", "vinewhip", "razorleaf", "leechseed"],
            ["poisonpowder", "sleeppowder"],
            ["vinewhip", "razorleaf", "leechseed", "poisonpowder"],
            ["poisonpowder", "leechseed", "vinewhip", "razorleaf"],
        ),
        (
            ["tackle", "growl", "vinewhip", "razorleaf"],
            ["poisonpowder", "poisonpowder", "sleeppowder"],
            ["tackle", "vinewhip", "poisonpowder", "sleeppowder"],
            ["tackle", "poisonpowder", "vinewhip", "sleeppowder"],
        ),
    ],
)
def test_choice_keeps_unique_retained_slots_and_candidate_order(
    xp_share, attacks, new_attacks, chosen, expected
):
    """Retain original slots when possible, filling each hole exactly once."""
    result = xp_share.trainer._apply_moveset_choice(attacks, new_attacks, chosen)

    assert result == expected
    assert len(result) == 4
    assert set(result) == set(chosen)
    assert len({xp_share.trainer._normalize_move_id(move) for move in result}) == 4
    assert attacks is not result


@pytest.mark.parametrize(
    "chosen",
    [
        ["tackle", "growl", ["poisonpowder"], "sleeppowder"],
        ["tackle", "growl", {"move": "poisonpowder"}, "sleeppowder"],
        ["tackle", "growl", 1, "sleeppowder"],
        ["tackle", "Tackle", "poisonpowder", "sleeppowder"],
        ["tackle", "poisonpowder", "poison-powder", "sleeppowder"],
    ],
)
def test_malformed_or_normalized_duplicate_choices_fail_safe(xp_share, chosen):
    """A bad presenter answer must not crash or claim duplicate move slots."""
    attacks = ["tackle", "growl", "Tackle", "vinewhip"]
    new_attacks = ["poisonpowder", "poison-powder", "sleeppowder"]

    assert xp_share.trainer._apply_moveset_choice(attacks, new_attacks, chosen) is None


def test_unhashable_prompt_answer_keeps_moves_and_reward(xp_share):
    """Untrusted answers are cancelled safely after an earned level-up."""
    s = xp_share
    original = ["tackle", "growl", "vinewhip", "razorleaf"]
    s.save("active", BULBASAUR, 50, ["solarbeam"])
    s.save("holder", BULBASAUR, 14, original)
    s.db.save_team([{"individual_id": "active"}, {"individual_id": "holder"}])
    s.ui.choose_moveset.return_value = [
        "tackle",
        "growl",
        ["poisonpowder"],
        "sleeppowder",
    ]

    s.grant(s.xp_to_reach(BULBASAUR, 14, 15))

    holder = s.db.get_pokemon("holder")
    assert holder["level"] == 15
    assert holder["attacks"] == original
    assert any(call.args[0] == "warning" for call in s.logger.log.call_args_list)
