"""Real-engine regression tests for Shell Bell, including earlier turn effects.

Each generated outcome is applied and reversed as in the battle bridge. Import
stubs and the installed damage wrapper are restored so these tests also compose
with the existing review-multiplier and Cell Battery suites.
"""

import importlib
import importlib.util
import sys
import types
from collections import defaultdict
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _load_hook():
    names = (
        "Ankimon.services",
        "Ankimon.pyobj.error_handler",
        "Ankimon.functions.ankimon_hooks_to_poke_engine",
    )
    saved = {name: sys.modules.get(name) for name in names}
    for name in names[:2]:
        sys.modules[name] = MagicMock()
    spec = importlib.util.spec_from_file_location(
        names[2],
        Path(__file__).parents[1]
        / "src/Ankimon/functions/ankimon_hooks_to_poke_engine.py",
    )
    hook = importlib.util.module_from_spec(spec)
    sys.modules[names[2]] = hook
    try:
        spec.loader.exec_module(hook)
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
    return hook


@pytest.fixture(scope="module")
def engine():
    src = Path(__file__).parents[1] / "src"
    for name in ("Ankimon", "Ankimon.functions", "Ankimon.pyobj"):
        package = sys.modules.get(name)
        if not isinstance(package, types.ModuleType) or not getattr(
            package, "__path__", None
        ):
            package = types.ModuleType(name)
            package.__path__ = [str(src / name.replace(".", "/"))]
            package.__package__ = name
            sys.modules[name] = package
    for name in list(sys.modules):
        if name.startswith("Ankimon.poke_engine") and not getattr(
            sys.modules[name], "__file__", None
        ):
            del sys.modules[name]

    instruction_generator = importlib.import_module(
        "Ankimon.poke_engine.instruction_generator"
    )
    instructions = importlib.import_module(
        "Ankimon.poke_engine.find_state_instructions"
    )
    config = importlib.import_module("Ankimon.poke_engine.config").ShowdownConfig
    items = importlib.import_module(
        "Ankimon.poke_engine.special_effects.items.modify_attack_against"
    )
    previous_damage_fn = instruction_generator.get_instructions_from_damage
    previous_recovery_fn = instruction_generator.get_instructions_from_attacker_recovery
    previous_move_fn = instructions.get_state_instructions_from_move
    previous_calc = config.damage_calc_type
    previous_items = dict(items.item_lookup)
    try:
        hook = _load_hook()
        config.damage_calc_type = "average"
        yield types.SimpleNamespace(
            hook=hook,
            constants=hook.constants,
            battle_pokemon=importlib.import_module(
                "Ankimon.poke_engine.battle"
            ).Pokemon,
            instructions=instructions,
            transpose=importlib.import_module(
                "Ankimon.poke_engine.objects"
            ).TransposeInstruction,
        )
    finally:
        instruction_generator.get_instructions_from_damage = previous_damage_fn
        instruction_generator.get_instructions_from_attacker_recovery = (
            previous_recovery_fn
        )
        instructions.get_state_instructions_from_move = previous_move_fn
        config.damage_calc_type = previous_calc
        items.item_lookup.clear()
        items.item_lookup.update(previous_items)


def _state(engine, hp=50, speed=200, item="shellbell", ability=None):
    def pokemon():
        result = engine.hook.Pokemon.from_state_pokemon_dict(
            engine.battle_pokemon("mew", 50).to_dict()
        )
        result.types = [value.lower() for value in result.types]
        result.hp = result.maxhp = 100
        result.item = result.ability = None
        return result

    holder, opponent = pokemon(), pokemon()
    holder.hp, holder.speed = hp, speed
    holder.item, holder.ability = item, ability
    opponent.speed = 100
    return engine.hook.State(
        engine.hook.reset_side(holder),
        engine.hook.reset_side(opponent),
        None,
        None,
        False,
    )


def _snapshot(value):
    if isinstance(value, dict):
        # Engine lookups may materialize absent zero-valued side conditions.
        return {
            key: _snapshot(item)
            for key, item in value.items()
            if not isinstance(value, defaultdict) or item
        }
    if isinstance(value, (list, tuple)):
        return tuple(_snapshot(item) for item in value)
    if hasattr(value, "__slots__"):
        return {name: _snapshot(getattr(value, name)) for name in value.__slots__}
    return value


def _outcomes(
    engine, state, user_move="tackle", opponent_move="splash", multiplier=None
):
    mutator = engine.hook.StateMutator(state)
    if multiplier is not None:
        mutator.review_based_damage_multiplier = multiplier
    before = _snapshot(state)
    outcomes = engine.hook.get_all_state_instructions(mutator, user_move, opponent_move)
    assert _snapshot(state) == before, "generating outcomes mutated the battle state"
    assert sum(outcome.percentage for outcome in outcomes) == pytest.approx(1)
    for outcome in outcomes:
        mutator.apply(outcome.instructions)
        try:
            for side in (state.user, state.opponent):
                assert 0 <= side.active.hp <= side.active.maxhp
        finally:
            mutator.reverse(outcome.instructions)
        assert _snapshot(state) == before, "applying/reversing an outcome leaked state"
    return outcomes


def _amounts(engine, outcome, kind, side="user"):
    return [
        instruction[2]
        for instruction in outcome.instructions
        if instruction[0] == kind and instruction[1] == side
    ]


def _heals(engine, outcome, side="user"):
    return _amounts(engine, outcome, engine.constants.MUTATOR_HEAL, side)


def test_heals_one_eighth_of_damage(engine):
    (outcome,) = _outcomes(engine, _state(engine))
    assert _amounts(engine, outcome, engine.constants.MUTATOR_DAMAGE, "opponent") == [
        17
    ]
    assert _heals(engine, outcome) == [2]


def test_drain_punch_cannot_heal_past_full_hp(engine):
    (outcome,) = _outcomes(engine, _state(engine, hp=95), user_move="drainpunch")
    assert _heals(engine, outcome) == [5]


def test_lethal_recoil_cannot_be_undone_by_shell_bell(engine):
    (outcome,) = _outcomes(engine, _state(engine, hp=1), user_move="doubleedge")
    assert _amounts(engine, outcome, engine.constants.MUTATOR_DAMAGE) == [1]
    assert _heals(engine, outcome) == []


def test_initially_full_holder_heals_after_being_hit_first(engine):
    (outcome,) = _outcomes(
        engine, _state(engine, hp=100, speed=50), opponent_move="tackle"
    )
    assert _amounts(engine, outcome, engine.constants.MUTATOR_DAMAGE) == [17]
    assert _heals(engine, outcome) == [2]


def test_earlier_opponent_recoil_is_not_the_holders_damage(engine):
    outcomes = _outcomes(engine, _state(engine, speed=50), opponent_move="takedown")
    (hit,) = [
        outcome
        for outcome in outcomes
        if 9 in _amounts(engine, outcome, engine.constants.MUTATOR_DAMAGE, "opponent")
    ]
    assert _amounts(engine, hit, engine.constants.MUTATOR_DAMAGE, "opponent") == [9, 17]
    assert _heals(engine, hit) == [2]


@pytest.mark.parametrize("move", ["knockoff", "trick"])
def test_removed_item_does_not_heal_later_in_turn(engine, move):
    (outcome,) = _outcomes(engine, _state(engine, hp=90, speed=50), opponent_move=move)
    assert _heals(engine, outcome) == []


def test_item_acquired_earlier_in_turn_heals(engine):
    state = _state(engine, speed=50, item=None)
    state.opponent.active.item = "shellbell"
    (outcome,) = _outcomes(engine, state, opponent_move="trick")
    assert _heals(engine, outcome) == [2]


@pytest.mark.parametrize(
    ("ability", "opponent_ability", "expected"),
    [
        ("klutz", None, []),
        ("klutz", "neutralizinggas", [2]),
        ("klutz", "moldbreaker", []),
        ("klutz", "teravolt", []),
        (None, "moldbreaker", [2]),
    ],
)
def test_klutz_obeys_suppression_rules(engine, ability, opponent_ability, expected):
    state = _state(engine, ability=ability)
    state.opponent.active.ability = opponent_ability
    (outcome,) = _outcomes(engine, state)
    assert _heals(engine, outcome) == expected


def test_klutz_resumes_when_the_neutralizing_gas_foe_faints(engine):
    state = _state(engine, ability="klutz")
    state.opponent.active.ability = "neutralizinggas"
    state.opponent.active.hp = 1
    (outcome,) = _outcomes(engine, state)
    assert _amounts(engine, outcome, engine.constants.MUTATOR_DAMAGE, "opponent") == [1]
    assert _heals(engine, outcome) == []


@pytest.mark.parametrize(("hp", "item"), [(100, "shellbell"), (50, None)])
def test_full_hp_or_missing_item_does_not_heal(engine, hp, item):
    (outcome,) = _outcomes(engine, _state(engine, hp=hp, item=item))
    assert _heals(engine, outcome) == []


def test_miss_does_not_heal(engine):
    outcomes = _outcomes(engine, _state(engine), user_move="takedown")
    (missed,) = [
        outcome
        for outcome in outcomes
        if not _amounts(engine, outcome, engine.constants.MUTATOR_DAMAGE, "opponent")
    ]
    assert _heals(engine, missed) == []
    assert any(_heals(engine, outcome) for outcome in outcomes if outcome is not missed)


def test_immune_target_does_not_heal(engine):
    state = _state(engine)
    state.opponent.active.ability = "voltabsorb"
    (outcome,) = _outcomes(engine, state, user_move="shockwave")
    assert _heals(engine, outcome) == []


@pytest.mark.parametrize("damage", [0, None])
def test_zero_or_nondamaging_move_does_not_heal(engine, damage):
    state = _state(engine)
    mutator = engine.hook.StateMutator(state)
    before = _snapshot(state)
    outcomes = engine.hook.instruction_generator.get_instructions_from_damage(
        mutator,
        engine.constants.OPPONENT,
        damage,
        True,
        engine.instructions.lookup_move("tackle"),
        engine.transpose(1, []),
    )
    assert _snapshot(state) == before
    assert all(not _heals(engine, outcome) for outcome in outcomes)


@pytest.mark.parametrize(("multiplier", "damage", "healing"), [(0.5, 8, 1), (2, 34, 4)])
def test_heals_from_actual_review_scaled_damage(engine, multiplier, damage, healing):
    (outcome,) = _outcomes(engine, _state(engine), multiplier=multiplier)
    assert _amounts(engine, outcome, engine.constants.MUTATOR_DAMAGE, "opponent") == [
        damage
    ]
    assert _heals(engine, outcome) == [healing]


def test_each_secondary_outcome_heals_and_leaves_state_unchanged(engine):
    outcomes = _outcomes(engine, _state(engine), user_move="thunderbolt")
    assert len(outcomes) > 1
    for outcome in outcomes:
        assert _heals(engine, outcome)


def test_opponent_holder_also_heals(engine):
    state = _state(engine, item=None)
    state.opponent.active.item = "shellbell"
    state.opponent.active.hp = 50
    (outcome,) = _outcomes(engine, state, user_move="splash", opponent_move="tackle")
    assert _heals(engine, outcome, "opponent") == [2]


def test_healing_uses_damage_capped_by_targets_remaining_hp(engine):
    state = _state(engine)
    state.opponent.active.hp = 7
    (outcome,) = _outcomes(engine, state)
    assert _amounts(engine, outcome, engine.constants.MUTATOR_DAMAGE, "opponent") == [7]
    assert _heals(engine, outcome) == [1]


def test_healing_is_capped_at_current_missing_hp(engine):
    (outcome,) = _outcomes(engine, _state(engine, hp=99))
    assert _heals(engine, outcome) == [1]


@pytest.mark.parametrize(
    ("ability", "item", "hp"),
    [("roughskin", None, 6), ("ironbarbs", None, 12), (None, "rockyhelmet", 16)],
)
def test_contact_damage_faints_holder_before_shell_bell(engine, ability, item, hp):
    state = _state(engine, hp=hp)
    state.opponent.active.ability = ability
    state.opponent.active.item = item
    (outcome,) = _outcomes(engine, state)
    # The engine represents contact damage as negative recovery. Shell Bell
    # must not save the holder by inserting positive recovery before that cost.
    assert _heals(engine, outcome) == [-hp]
    mutator = engine.hook.StateMutator(state)
    mutator.apply(outcome.instructions)
    try:
        assert state.user.active.hp == 0
    finally:
        mutator.reverse(outcome.instructions)


@pytest.mark.parametrize(
    ("ability", "item", "contact_damage"),
    [
        ("roughskin", None, 100 / 16),
        ("ironbarbs", None, 100 / 8),
        (None, "rockyhelmet", 100 / 6),
    ],
)
def test_full_hp_holder_heals_after_contact_damage(
    engine, ability, item, contact_damage
):
    state = _state(engine, hp=100)
    state.opponent.active.ability = ability
    state.opponent.active.item = item
    (outcome,) = _outcomes(engine, state)
    assert _amounts(engine, outcome, engine.constants.MUTATOR_DAMAGE, "opponent") == [
        17
    ]
    assert _heals(engine, outcome) == pytest.approx([-contact_damage, 2])


def test_contact_recovery_uses_this_moves_damage_after_earlier_recoil(engine):
    state = _state(engine, speed=50)
    state.opponent.active.ability = "roughskin"
    outcomes = _outcomes(engine, state, opponent_move="takedown")
    (hit,) = [
        outcome
        for outcome in outcomes
        if 9 in _amounts(engine, outcome, engine.constants.MUTATOR_DAMAGE, "opponent")
    ]
    assert _amounts(engine, hit, engine.constants.MUTATOR_DAMAGE, "opponent") == [9, 17]
    assert _heals(engine, hit) == [-6.25, 2]


def test_reloading_does_not_stack_damage_move_or_recovery_wrappers(engine):
    generator = engine.hook.instruction_generator
    previous = (
        generator.get_instructions_from_damage,
        generator.get_instructions_from_attacker_recovery,
        engine.instructions.get_state_instructions_from_move,
    )
    _load_hook()
    assert previous == (
        generator.get_instructions_from_damage,
        generator.get_instructions_from_attacker_recovery,
        engine.instructions.get_state_instructions_from_move,
    )


def test_reloading_upgrades_the_previous_damage_wrapper(engine):
    generator = engine.hook.instruction_generator
    installed = generator.get_instructions_from_damage
    pristine = engine.hook._original_get_instructions_from_damage

    def old_wrapper(*args):
        return pristine(*args)

    old_wrapper._ankimon_damage_wrapper_version = 2
    old_wrapper._ankimon_wrapped_function = pristine
    generator.get_instructions_from_damage = old_wrapper
    try:
        upgraded = _load_hook()
        assert generator.get_instructions_from_damage is not old_wrapper
        assert upgraded._original_get_instructions_from_damage is pristine
        (outcome,) = _outcomes(engine, _state(engine), multiplier=2)
        assert _heals(engine, outcome) == [4]
    finally:
        generator.get_instructions_from_damage = installed
