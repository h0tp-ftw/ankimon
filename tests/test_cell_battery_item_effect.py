"""Regression tests for the Cell Battery held-item effect.

Ankimon sells Cell Battery and lets a Pokemon hold it, and the held item does reach
the engine ("cell-battery" -> ``normalize_name`` -> "cellbattery" -> ``Pokemon.item``),
but poke-engine has no entry for it, so the item did nothing at all in battle.
``_install_on_hit_boost_items`` in ``ankimon_hooks_to_poke_engine`` registers the
effect (+1 Attack when the holder is hit by a damaging Electric move) and the damage
wrapper in the same module turns it into boost instructions.

The boost deliberately does NOT ride on the move's ``BOOSTS``, the way the engine's
own ``weaknesspolicy`` does. find_state_instructions resolves one boost payload
through an elif chain in which a SECONDARY always wins, so a top-level BOOSTS is read
only for a move with no secondary effect -- which drops it for 17 of the engine's 37
damaging Electric moves, Thunderbolt included. A test that only ever fired Shock Wave
would pass against that broken shape, so ``test_thunderbolt_...`` below is the point
of this file.

The item is consumed by the boost, as the real one is, so several tests here are
about what must NOT happen twice or on an outcome that never earned it: a hit taken
by a Substitute made earlier in the same turn, a holder already at the stat cap, and
a second Electric hit after the first spent the battery. Contrary is covered because
the engine runs a defender's ability before its item, which would otherwise hand a
Contrary holder the +1 it is supposed to lose.

Simple and Klutz are covered for a different reason: the engine implements neither,
so neither can be had by replaying the payload through its ability hook the way
Contrary is. Simple doubles the boost to +2 and Klutz stops the item firing and being
spent at all, and both are checked against the engine's suppression rules -- a
mold-breaker attacker turns Simple's doubling off, Neutralizing Gas hands a Klutz
holder its battery back, and Mold Breaker does NOT turn Klutz off.

Both the registration and the wrapper run inside ``_apply_engine_patch``, which
swallows the exception and merely logs, so a regression here would be silent in
production. These checks are what make it loud.
"""

import importlib.util
import math
import sys
import types
from collections import defaultdict
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_src = Path(__file__).parent.parent / "src"


def _ensure_pkg(name):
    """(Re)establish ``name`` as a real package rooted at src/.

    Sibling test modules pollute ``sys.modules`` (e.g. test_database_manager
    replaces ``sys.modules["Ankimon"]`` with a path-less ModuleType), which breaks
    ``from Ankimon.*`` imports for whichever test runs next.
    """
    mod = sys.modules.get(name)
    if not isinstance(mod, types.ModuleType) or not getattr(mod, "__path__", None):
        stub = types.ModuleType(name)
        stub.__path__ = [str(_src / name.replace(".", "/"))]
        stub.__package__ = name
        sys.modules[name] = stub


for _pkg in ("Ankimon", "Ankimon.functions", "Ankimon.pyobj"):
    _ensure_pkg(_pkg)

# The real poke_engine IS needed (these assert on genuine engine output); drop any
# non-real stand-ins so the actual modules load under Ankimon.__path__.
for _name in [n for n in list(sys.modules) if n.startswith("Ankimon.poke_engine")]:
    _m = sys.modules[_name]
    if not isinstance(_m, types.ModuleType) or getattr(_m, "__file__", None) is None:
        del sys.modules[_name]

from Ankimon.poke_engine import constants, instruction_generator
from Ankimon.poke_engine.battle import Pokemon as StatePokemon
from Ankimon.poke_engine.config import ShowdownConfig
from Ankimon.poke_engine.find_state_instructions import get_all_state_instructions
from Ankimon.poke_engine.objects import Pokemon, Side, State, StateMutator
from Ankimon.poke_engine.special_effects.items import modify_attack_against

_HOOK_PATH = _src / "Ankimon" / "functions" / "ankimon_hooks_to_poke_engine.py"

# The module under test imports the services registry and the error dialog at module
# scope but does not use them in these paths, so they get stubbed for the import and
# undone afterwards -- leaving MagicMocks under "Ankimon.*" changes what later test
# modules see at COLLECTION time, before conftest's autouse restore fixture can run.
_STUBBED = ("Ankimon.services", "Ankimon.pyobj.error_handler")

hook = None


def _load_hook():
    """Import the hooks module fresh from its file; importing installs the patches."""
    saved = {k: sys.modules.get(k) for k in _STUBBED}
    for name in _STUBBED:
        sys.modules[name] = MagicMock()
    spec = importlib.util.spec_from_file_location(
        "Ankimon.functions.ankimon_hooks_to_poke_engine", _HOOK_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    saved[spec.name] = sys.modules.get(spec.name)
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
    return mod


@pytest.fixture(scope="module", autouse=True)
def _installed():
    """Install the patches for this module only, then put the engine back.

    Unlike the sibling patches, the feature under test lives IN the damage wrapper,
    so the wrapper cannot be unwound before the assertions run -- which is why this
    is a fixture rather than a module-scope import. Whatever was installed on arrival
    is captured and restored, instead of forcing the pristine engine function back:
    test_review_based_damage_multiplier installs the same wrapper at collection time
    and asserts it is still in place when its own tests run.

    Everything else this module reaches into is engine-wide singleton state, so it is
    all put back on the way out: the ``cellbattery`` entry in the item lookup, and
    ShowdownConfig's damage calculation mode. These assertions want one averaged
    damage roll rather than the engine default of every roll, and leaving that set
    would quietly change what every test module collected after this one computes.
    """
    global hook
    previous_damage_fn = instruction_generator.get_instructions_from_damage
    previous_calc_type = ShowdownConfig.damage_calc_type
    lookup = modify_attack_against.item_lookup
    had_cellbattery = "cellbattery" in lookup
    previous_cellbattery = lookup.get("cellbattery")
    ShowdownConfig.damage_calc_type = "average"
    hook = _load_hook()
    yield hook
    instruction_generator.get_instructions_from_damage = previous_damage_fn
    ShowdownConfig.damage_calc_type = previous_calc_type
    if had_cellbattery:
        lookup["cellbattery"] = previous_cellbattery
    else:
        lookup.pop("cellbattery", None)


def _legacy_v1_wrapper(original):
    """A stand-in for the damage wrapper that shipped before on-hit items existed.

    It scales review-based damage and nothing else, marks itself with the bare
    boolean that generation used, and reaches its pristine original through a module
    global rather than an attribute -- which is the shape the upgrade path has to
    recognise and unwind. Built through FunctionType because a function's
    ``__globals__`` cannot be reassigned afterwards.
    """

    def template(mutator, defender, damage, accuracy, attacking_move, instruction):
        if (
            defender == constants.OPPONENT
            and hasattr(mutator, "review_based_damage_multiplier")
            and damage is not None
        ):
            if damage > 0:
                damage = max(
                    1, math.floor(damage * mutator.review_based_damage_multiplier)
                )
            else:
                damage = math.floor(damage * mutator.review_based_damage_multiplier)
            mutator.review_based_damage_multiplier_applied = True
        # Resolved out of the namespace handed to FunctionType below, not this
        # module -- which is the whole point of building the stand-in that way.
        return _original_get_instructions_from_damage(  # noqa: F821
            mutator, defender, damage, accuracy, attacking_move, instruction
        )

    legacy = types.FunctionType(
        template.__code__,
        {
            "constants": constants,
            "math": math,
            "_original_get_instructions_from_damage": original,
        },
        "legacy_v1_get_instructions_from_damage",
    )
    legacy._ankimon_review_wrapped = True
    return legacy


def _side(species, level=50):
    side = Side(
        Pokemon.from_state_pokemon_dict(StatePokemon(species, level).to_dict()),
        {},
        (0, 0),
        defaultdict(lambda: 0),
        (0, "some_pkmn"),
    )
    # to_dict() yields capitalised types; the damage tables are lowercase.
    side.active.types = [t.lower() for t in side.active.types]
    return side


def _state(
    holder="snorlax",
    item="cellbattery",
    ability=None,
    holder_level=50,
    attacker="rockruff",
    attacker_level=50,
    volatile=None,
    attack_boost=0,
    holder_speed=None,
    attacker_ability=None,
):
    """Singles state whose USER side holds the item and receives the attack."""
    state = State(
        _side(holder, holder_level),
        _side(attacker, attacker_level),
        None,
        None,
        False,
    )
    state.user.active.item = item
    state.user.active.attack_boost = attack_boost
    if ability is not None:
        state.user.active.ability = ability
    if attacker_ability is not None:
        state.opponent.active.ability = attacker_ability
    if volatile is not None:
        state.user.active.volatile_status.add(volatile)
    if holder_speed is not None:
        state.user.active.speed = holder_speed
    return state


def _outcomes(incoming_move, user_move="splash", **kwargs):
    """Every outcome of the opponent using ``incoming_move`` against the holder."""
    mutator = StateMutator(_state(**kwargs))
    return get_all_state_instructions(mutator, user_move, incoming_move)


def _of_type(outcomes, mutator_type, side=constants.USER):
    return [
        instr
        for outcome in outcomes
        for instr in outcome.instructions
        if instr[0] == mutator_type and instr[1] == side
    ]


def _boosts(incoming_move, side=constants.USER, **kwargs):
    return _of_type(_outcomes(incoming_move, **kwargs), constants.MUTATOR_BOOST, side)


def _opponent_damage(multiplier):
    """Damage dealt TO the opponent with the review multiplier set to ``multiplier``."""
    mutator = StateMutator(_state())
    mutator.review_based_damage_multiplier = multiplier
    return _of_type(
        get_all_state_instructions(mutator, "tackle", "splash"),
        constants.MUTATOR_DAMAGE,
        constants.OPPONENT,
    )


_ATTACK_UP = (constants.MUTATOR_BOOST, constants.USER, constants.ATTACK, 1)
_ATTACK_DOWN = (constants.MUTATOR_BOOST, constants.USER, constants.ATTACK, -1)
_ATTACK_UP_DOUBLED = (constants.MUTATOR_BOOST, constants.USER, constants.ATTACK, 2)
_SPENT = (constants.MUTATOR_CHANGE_ITEM, constants.USER, None, "cellbattery")


def test_cellbattery_is_registered_by_importing_the_hooks():
    assert callable(hook._install_on_hit_boost_items)
    assert "cellbattery" in modify_attack_against.item_lookup


def test_thunderbolt_boosts_the_holders_attack():
    """The whole point: Thunderbolt carries a SECONDARY, which shadows move BOOSTS.

    Every outcome of Thunderbolt damages the holder (paralysis only branches the
    status), so every outcome must carry the boost.
    """
    outcomes = _outcomes("thunderbolt")
    assert len(outcomes) > 1, "expected Thunderbolt to branch on its paralysis chance"
    for outcome in outcomes:
        assert _ATTACK_UP in outcome.instructions


def test_secondary_less_electric_move_boosts_exactly_once():
    # Shock Wave has no secondary, so it is the move a BOOSTS-based implementation
    # would have handled. Guards against the boost being applied twice.
    assert _boosts("shockwave") == [_ATTACK_UP]


def test_no_boost_without_the_item():
    assert _boosts("thunderbolt", item=None) == []
    # 'unknown_item' is what the engine itself puts on a Pokemon with no item.
    assert _boosts("thunderbolt", item="unknown_item") == []


def test_non_electric_damaging_move_does_not_trigger():
    assert _boosts("tackle") == []


def test_electric_status_move_does_not_trigger():
    # Thunder Wave is Electric but deals no damage, so Cell Battery must stay put.
    assert _boosts("thunderwave") == []


def test_electric_immune_holder_is_not_boosted():
    # Sandshrew is Ground; Cell Battery does not fire on an immune hit.
    assert "ground" in _state(holder="sandshrew").user.active.types
    assert _boosts("thunderbolt", holder="sandshrew") == []


@pytest.mark.parametrize(
    "ability,expected",
    [
        ("voltabsorb", []),
        (
            "lightningrod",
            [(constants.MUTATOR_BOOST, constants.USER, constants.SPECIAL_ATTACK, 1)],
        ),
        ("motordrive", [(constants.MUTATOR_BOOST, constants.USER, constants.SPEED, 1)]),
    ],
)
def test_electric_absorbing_abilities_keep_their_own_effect(ability, expected):
    """These abilities turn the move into a STATUS move before the item hook runs.

    The holder is not struck, so Cell Battery must not add its Attack boost -- and
    must not displace the ability's own boost either.
    """
    assert _boosts("thunderbolt", ability=ability) == expected


def test_a_miss_does_not_trigger():
    # Thunder is 70% accurate, so the outcome set includes a miss.
    outcomes = _outcomes("thunder")
    hit, missed = [], []
    for outcome in outcomes:
        damaged = any(
            instr[0] == constants.MUTATOR_DAMAGE and instr[1] == constants.USER
            for instr in outcome.instructions
        )
        (hit if damaged else missed).append(outcome)
    assert hit and missed, f"expected both a hit and a miss branch, got {outcomes}"
    for outcome in hit:
        assert _ATTACK_UP in outcome.instructions
    for outcome in missed:
        assert _ATTACK_UP not in outcome.instructions


def test_fainting_holder_is_not_boosted():
    """A Pokemon knocked out by the hit does not get the item's boost.

    The engine's own faint check in ``get_instructions_from_damage`` reads HP from
    before the killing blow joins the instruction set, so it never freezes here --
    the wrapper has to notice this itself.
    """
    kwargs = {
        "holder": "magikarp",
        "holder_level": 5,
        "attacker": "zapdos",
        "attacker_level": 100,
    }
    lethal = [
        instr
        for outcome in _outcomes("thunderbolt", **kwargs)
        for instr in outcome.instructions
        if instr[0] == constants.MUTATOR_DAMAGE and instr[1] == constants.USER
    ]
    assert lethal, "expected the holder to be hit"
    assert lethal[0][2] >= _state(**kwargs).user.active.maxhp, "expected a KO"
    assert _boosts("thunderbolt", **kwargs) == []


def test_hit_absorbed_by_a_substitute_does_not_trigger():
    # The Substitute takes the hit, so its holder was never struck.
    assert _boosts("thunderbolt", volatile=constants.SUBSTITUTE) == []


def test_an_abilitys_own_boost_is_not_clobbered():
    """Stamina sets the move's BOOSTS and leaves it damaging; both must survive.

    Shock Wave rather than Thunderbolt, because a SECONDARY would shadow Stamina's
    boost for reasons that have nothing to do with this change.
    """
    defense_up = (constants.MUTATOR_BOOST, constants.USER, constants.DEFENSE, 1)
    assert _boosts("shockwave", ability="stamina", item=None) == [defense_up]
    with_item = _boosts("shockwave", ability="stamina")
    assert sorted(with_item) == sorted([defense_up, _ATTACK_UP])


def test_weaknesspolicy_is_left_alone():
    # setdefault must not have displaced an item the engine already implements.
    assert (
        modify_attack_against.item_lookup["weaknesspolicy"].__module__
        == modify_attack_against.__name__
    )
    # Magikarp is Water, so Shock Wave is super effective and the policy fires.
    assert sorted(_boosts("shockwave", holder="magikarp", item="weaknesspolicy")) == [
        (constants.MUTATOR_BOOST, constants.USER, constants.ATTACK, 2),
        (constants.MUTATOR_BOOST, constants.USER, constants.SPECIAL_ATTACK, 2),
    ]


def test_a_holder_at_the_stat_cap_keeps_its_item():
    """At +6 Attack there is no stage to gain, so Cell Battery is not spent.

    The engine's boost generator would happily emit a zero-magnitude no-op here;
    consuming an item to pay for one is what the mechanic explicitly does not do.
    """
    outcomes = _outcomes("shockwave", attack_boost=constants.MAX_BOOSTS)
    assert _of_type(outcomes, constants.MUTATOR_BOOST) == []
    assert _of_type(outcomes, constants.MUTATOR_CHANGE_ITEM) == []


def test_contrary_holder_loses_attack_instead():
    """Contrary reverses the item's boost, so the holder drops to -1 Attack.

    The engine runs a defender's ability hook before its item hook, so a boost the
    item adds afterwards never meets Contrary on its own; the effect replays it
    through the engine's own ability hook to get this. (Shock Wave rather than
    Thunderbolt only to keep this to one outcome -- a paralysis chance would branch
    it and say nothing about Contrary.)
    """
    assert _boosts("shockwave", ability="contrary") == [_ATTACK_DOWN]


def test_contrary_holder_is_consumed_at_the_top_of_the_range():
    # +6 is the cap that stops a normal holder, and no obstacle at all to a drop.
    assert _boosts(
        "shockwave", ability="contrary", attack_boost=constants.MAX_BOOSTS
    ) == [_ATTACK_DOWN]


def test_contrary_holder_at_the_bottom_of_the_range_keeps_its_item():
    # Under Contrary the cap that matters is -6, not +6.
    outcomes = _outcomes(
        "shockwave", ability="contrary", attack_boost=-1 * constants.MAX_BOOSTS
    )
    assert _of_type(outcomes, constants.MUTATOR_BOOST) == []
    assert _of_type(outcomes, constants.MUTATOR_CHANGE_ITEM) == []


def test_simple_doubles_the_items_boost():
    """Simple doubles a stage change, so Cell Battery is worth +2 Attack to a holder.

    Unlike Contrary this cannot be had by replaying the payload through the engine's
    ability hook: the engine implements no Simple at all, it appears only in
    BYPASSABLE_ABILITIES, so the replay would hand back the +1 and the battery would
    be spent on it.
    """
    outcomes = _outcomes("shockwave", ability="simple")
    assert _of_type(outcomes, constants.MUTATOR_BOOST) == [_ATTACK_UP_DOUBLED]
    assert _of_type(outcomes, constants.MUTATOR_CHANGE_ITEM) == [_SPENT]


def test_simple_holder_takes_the_one_stage_it_has_left():
    # A doubled boost at +5 asks for +7. The stage the holder actually has left is
    # worth the item, so the engine clamps the instruction and the battery is spent.
    outcomes = _outcomes(
        "shockwave", ability="simple", attack_boost=constants.MAX_BOOSTS - 1
    )
    assert _of_type(outcomes, constants.MUTATOR_BOOST) == [_ATTACK_UP]
    assert _of_type(outcomes, constants.MUTATOR_CHANGE_ITEM) == [_SPENT]


def test_simple_holder_at_the_cap_keeps_its_item():
    # Doubling must not talk the holder past the cap into spending the battery on a
    # no-op: +6 is still +6 whether the item offers one stage or two.
    outcomes = _outcomes(
        "shockwave", ability="simple", attack_boost=constants.MAX_BOOSTS
    )
    assert _of_type(outcomes, constants.MUTATOR_BOOST) == []
    assert _of_type(outcomes, constants.MUTATOR_CHANGE_ITEM) == []


def test_mold_breaker_attacker_suppresses_simple():
    """Simple is bypassable, so a mold-breaker hit leaves the boost at +1.

    This is what the suppression check buys over a bare ``ability == "simple"``.
    """
    assert _boosts("shockwave", ability="simple", attacker_ability="moldbreaker") == [
        _ATTACK_UP
    ]


def test_klutz_prevents_the_item_from_activating():
    """Klutz switches the item off: the hit lands, the battery does nothing.

    Nothing else on the path would stop it -- ``to_engine_format`` passes ability and
    item through side by side and the engine's item dispatcher calls the registered
    callback with no check of its own -- so neither a boost nor a spent battery may
    come out of this.
    """
    outcomes = _outcomes("shockwave", ability="klutz")
    assert _of_type(outcomes, constants.MUTATOR_BOOST) == []
    assert _of_type(outcomes, constants.MUTATOR_CHANGE_ITEM) == []


def test_neutralizing_gas_turns_klutz_off_and_the_item_back_on():
    """The trap in checking Klutz unconditionally: Neutralizing Gas suppresses it."""
    outcomes = _outcomes(
        "shockwave", ability="klutz", attacker_ability="neutralizinggas"
    )
    assert _of_type(outcomes, constants.MUTATOR_BOOST) == [_ATTACK_UP]
    assert _of_type(outcomes, constants.MUTATOR_CHANGE_ITEM) == [_SPENT]


def test_mold_breaker_does_not_turn_klutz_off():
    # Klutz is not in the engine's BYPASSABLE_ABILITIES, so a mold-breaker attacker
    # leaves it alone. A blanket "any suppressor" check would hand the battery back.
    outcomes = _outcomes("shockwave", ability="klutz", attacker_ability="moldbreaker")
    assert _of_type(outcomes, constants.MUTATOR_BOOST) == []
    assert _of_type(outcomes, constants.MUTATOR_CHANGE_ITEM) == []


def test_substitute_made_earlier_in_the_turn_does_not_trigger():
    """A holder that Substitutes first must not read its own HP cost as a hit.

    An instruction set is cumulative over the whole turn: the 25% self-damage that
    paid for the Substitute is still in the list when the Electric move resolves
    against the Substitute. Only the instructions this hit added may be inspected.
    """
    outcomes = _outcomes("thunderbolt", user_move="substitute", holder_speed=999)
    hp_costs = _of_type(outcomes, constants.MUTATOR_DAMAGE)
    assert hp_costs, "expected the Substitute's own HP cost in the instruction set"
    assert _of_type(outcomes, constants.MUTATOR_BOOST) == []
    assert _of_type(outcomes, constants.MUTATOR_CHANGE_ITEM) == []


def test_the_item_is_consumed_by_the_boost():
    """Cell Battery is a consumable: the boost comes with a change_item instruction.

    ``modify_attack_against`` returns a move dict and cannot emit an instruction,
    which is why the engine models its own one-shot items as reusable -- but this
    effect does its work in the damage wrapper, where the instruction is expressible
    and reversible.
    """
    outcomes = _outcomes("shockwave")
    for outcome in outcomes:
        assert _ATTACK_UP in outcome.instructions
        assert _SPENT in outcome.instructions
        # Spent after the boost it paid for, so reversing the set restores both.
        assert outcome.instructions.index(_SPENT) > outcome.instructions.index(
            _ATTACK_UP
        )


def test_a_spent_cell_battery_does_not_fire_again():
    """The direct refutation of "repeat triggers are bounded by MAX_BOOSTS".

    One outcome is applied to the state, exactly as simulate_battle_with_poke_engine
    does, and the holder is hit a second time.
    """
    mutator = StateMutator(_state())
    first = get_all_state_instructions(mutator, "splash", "shockwave")[0]
    assert _ATTACK_UP in first.instructions
    mutator.apply(first.instructions)
    assert mutator.state.user.active.item is None
    assert mutator.state.user.active.hp > 0, "expected the holder to survive the hit"

    second = get_all_state_instructions(mutator, "splash", "shockwave")
    assert _of_type(second, constants.MUTATOR_BOOST) == []
    assert _of_type(second, constants.MUTATOR_CHANGE_ITEM) == []


def test_reload_installs_one_wrapper_and_one_effect():
    """Reloading the current generation must not stack a second damage wrapper.

    A second layer would delegate to a function that already scales review-based
    damage, scaling it twice.
    """
    damage_fn = instruction_generator.get_instructions_from_damage
    effect = modify_attack_against.item_lookup["cellbattery"]
    _load_hook()
    assert instruction_generator.get_instructions_from_damage is damage_fn
    assert modify_attack_against.item_lookup["cellbattery"] is effect
    assert _boosts("shockwave") == [_ATTACK_UP]


def test_upgrade_over_the_previous_wrapper_generation():
    """Loading over the wrapper that shipped before on-hit items must replace it.

    That generation marked itself with a bare boolean, so a guard that only asks
    "already wrapped?" skips the new definition on an in-process reload and leaves a
    wrapper that has never heard of on-hit items installed for the rest of the
    session -- Cell Battery inert until Anki is restarted. The replacement has to
    take over the ORIGINAL that wrapper was calling, not the wrapper itself, or
    review-based damage would be scaled once per layer.
    """
    installed = instruction_generator.get_instructions_from_damage
    pristine = hook._original_get_instructions_from_damage
    legacy = _legacy_v1_wrapper(pristine)
    instruction_generator.get_instructions_from_damage = legacy
    try:
        upgraded = _load_hook()
        assert instruction_generator.get_instructions_from_damage is not legacy
        assert upgraded._original_get_instructions_from_damage is pristine
        assert _boosts("shockwave") == [_ATTACK_UP]
        assert _opponent_damage(0.5)[0][2] == _opponent_damage(1.0)[0][2] // 2
    finally:
        instruction_generator.get_instructions_from_damage = installed


def test_a_stale_item_shim_is_replaced_on_reload():
    """A shim from an older generation is upgraded, not left registered.

    Its payload shape is whatever that generation stashed on the move, which the
    current wrapper has no contract with.
    """
    lookup = modify_attack_against.item_lookup
    current = lookup["cellbattery"]

    def stale(attacking_move, attacking_pokemon, defending_pokemon):
        return attacking_move

    stale._ankimon_on_hit_item_version = 0
    lookup["cellbattery"] = stale
    try:
        _load_hook()
        assert lookup["cellbattery"] is not stale
        assert _boosts("shockwave") == [_ATTACK_UP]
    finally:
        lookup["cellbattery"] = current


def test_a_native_engine_implementation_wins_over_the_shim():
    """A submodule bump that implements the item for real must not be displaced.

    The engine's own entries are recognised by the module they are defined in,
    which is how test_weaknesspolicy_is_left_alone identifies them too.
    """
    lookup = modify_attack_against.item_lookup
    current = lookup["cellbattery"]

    def native(attacking_move, attacking_pokemon, defending_pokemon):
        return attacking_move

    native.__module__ = modify_attack_against.__name__
    lookup["cellbattery"] = native
    try:
        _load_hook()
        assert lookup["cellbattery"] is native
    finally:
        lookup["cellbattery"] = current


def test_review_based_damage_multiplier_still_scales():
    """The boost fan-out was added inside the F37 wrapper; F37 must still work."""
    unscaled = _opponent_damage(1.0)
    scaled = _opponent_damage(0.5)
    assert unscaled and scaled
    assert scaled[0][2] == unscaled[0][2] // 2
