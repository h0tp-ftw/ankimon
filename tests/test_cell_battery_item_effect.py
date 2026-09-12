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

Both the registration and the wrapper run inside ``_apply_engine_patch``, which
swallows the exception and merely logs, so a regression here would be silent in
production. These checks are what make it loud.
"""

import importlib.util
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

    The ``cellbattery`` entry is left in the engine's item lookup, as harmless as the
    MOVE_TARGET_SELF append that test_allies_move_target leaves behind -- it changes
    nothing unless a battler is actually holding the item.
    """
    global hook
    previous_damage_fn = instruction_generator.get_instructions_from_damage
    hook = _load_hook()
    yield hook
    instruction_generator.get_instructions_from_damage = previous_damage_fn


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
):
    """Singles state whose USER side holds the item and receives the attack."""
    ShowdownConfig.damage_calc_type = "average"
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
    if volatile is not None:
        state.user.active.volatile_status.add(volatile)
    return state


def _outcomes(incoming_move, **kwargs):
    """Every outcome of the opponent using ``incoming_move`` while the user Splashes."""
    mutator = StateMutator(_state(**kwargs))
    return get_all_state_instructions(mutator, "splash", incoming_move)


def _boosts(incoming_move, side=constants.USER, **kwargs):
    return [
        instr
        for outcome in _outcomes(incoming_move, **kwargs)
        for instr in outcome.instructions
        if instr[0] == constants.MUTATOR_BOOST and instr[1] == side
    ]


_ATTACK_UP = (constants.MUTATOR_BOOST, constants.USER, constants.ATTACK, 1)


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


def test_boost_is_capped_at_max_boosts():
    # At +6 the engine's boost generator yields a zero-magnitude no-op, never a +7.
    assert _boosts("shockwave", attack_boost=constants.MAX_BOOSTS) == [
        (constants.MUTATOR_BOOST, constants.USER, constants.ATTACK, 0)
    ]


def test_reload_installs_one_wrapper_and_one_effect():
    """A module reload must not stack a second damage wrapper or re-register.

    A second layer would also lose the ``_ankimon_review_wrapped`` flag, and the next
    import would wrap again -- scaling review-based damage twice.
    """
    damage_fn = instruction_generator.get_instructions_from_damage
    effect = modify_attack_against.item_lookup["cellbattery"]
    _load_hook()
    assert instruction_generator.get_instructions_from_damage is damage_fn
    assert modify_attack_against.item_lookup["cellbattery"] is effect
    assert _boosts("shockwave") == [_ATTACK_UP]


def test_review_based_damage_multiplier_still_scales():
    """The boost fan-out was added inside the F37 wrapper; F37 must still work."""
    unscaled, scaled = [], []
    for multiplier, sink in ((1.0, unscaled), (0.5, scaled)):
        mutator = StateMutator(_state())
        mutator.review_based_damage_multiplier = multiplier
        sink += [
            instr[2]
            for outcome in get_all_state_instructions(mutator, "tackle", "splash")
            for instr in outcome.instructions
            if instr[0] == constants.MUTATOR_DAMAGE and instr[1] == constants.OPPONENT
        ]
    assert unscaled and scaled
    assert scaled[0] == unscaled[0] // 2
