"""Regression test for the 'allies' move-target patch in the poke-engine hooks.

Howl, Life Dew, Jungle Healing and Lunar Blessing carry Showdown's doubles target
``allies``. poke_engine's ``constants.MOVE_TARGET_SELF`` never listed it, so
``find_state_instructions`` resolved Howl's boost to the *defender* -- answering a
card with Howl raised the WILD Pokemon's Attack. ``_patch_engine_constants`` in
``ankimon_hooks_to_poke_engine`` appends ``allies`` to that list at import.

The patch runs inside a bare ``except Exception: pass``, so a regression here would
be silent in production; these checks are what make it loud. Covered:
  * the constant is patched by the mere act of importing the hooks module,
  * Howl's boost instruction lands on ``user``, end to end through the engine,
  * the append is idempotent (module reload must not duplicate the entry),
  * the three ``allies`` healing moves are untouched -- they key on ``heal_target``,
    not on this list, and already recovered the attacker.
"""

import importlib.util
import sys
import types
from collections import defaultdict
from unittest.mock import MagicMock

_src = __import__("pathlib").Path(__file__).parent.parent / "src"


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

# The real poke_engine IS needed (this asserts on genuine engine output); drop any
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

_HOOK_PATH = _src / "Ankimon" / "functions" / "ankimon_hooks_to_poke_engine.py"

# The module under test imports the services registry and the error dialog at module
# scope but does not use them in these paths, so they get stubbed for the import.
# Those stubs are UNDONE afterwards: this file sorts early in the tests/ directory, and
# leaving MagicMocks under "Ankimon.*" changes what later modules see at COLLECTION
# time, before conftest's autouse restore fixture can run.
_STUBBED = ("Ankimon.services", "Ankimon.pyobj.error_handler")


def _load_hook():
    """Import the hooks module fresh from its file; importing applies the patch.

    Restores every ``sys.modules`` entry it stubbed, so importing this test module
    leaves the module table as it found it. The objects the tests assert on (the
    engine's ``constants`` list, the hook module itself) are held by reference and
    stay valid regardless.

    Also unwinds the unrelated F37 damage-multiplier monkeypatch that the same import
    installs. That one is guarded by a "already wrapped" flag on the engine function,
    so leaving it in place would make a later import of the hooks module skip the
    wrap -- and skip defining ``_original_get_instructions_from_damage``, which
    test_review_based_damage_multiplier asserts on. Only the MOVE_TARGET_SELF append
    under test here is meant to survive this import.
    """
    saved = {k: sys.modules.get(k) for k in _STUBBED}
    for name in _STUBBED:
        sys.modules[name] = MagicMock()
    spec = importlib.util.spec_from_file_location(
        "Ankimon.functions.ankimon_hooks_to_poke_engine", _HOOK_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    saved[spec.name] = sys.modules.get(spec.name)
    sys.modules[spec.name] = mod
    pristine_damage_fn = instruction_generator.get_instructions_from_damage
    try:
        spec.loader.exec_module(mod)
    finally:
        instruction_generator.get_instructions_from_damage = pristine_damage_fn
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
    return mod


hook = _load_hook()


def _state():
    """Singles state with both actives at half HP, so heals have room to show."""
    ShowdownConfig.damage_calc_type = "average"
    state = State(
        Side(
            Pokemon.from_state_pokemon_dict(StatePokemon("rockruff", 50).to_dict()),
            {},
            (0, 0),
            defaultdict(lambda: 0),
            (0, "some_pkmn"),
        ),
        Side(
            Pokemon.from_state_pokemon_dict(StatePokemon("snorlax", 50).to_dict()),
            {},
            (0, 0),
            defaultdict(lambda: 0),
            (0, "some_pkmn"),
        ),
        None,
        None,
        False,
    )
    for side in (state.user, state.opponent):
        # to_dict() yields capitalised types; the damage tables are lowercase.
        side.active.types = [t.lower() for t in side.active.types]
        side.active.hp = side.active.maxhp // 2
    return state


def _instructions(move):
    mutator = StateMutator(_state())
    return [
        i
        for t in get_all_state_instructions(mutator, move, "splash")
        for i in t.instructions
    ]


def test_allies_is_a_self_target_after_import():
    # Importing the hooks module is the only thing that applies this; nothing else
    # in the add-on touches MOVE_TARGET_SELF.
    assert callable(hook._patch_engine_constants)
    assert "allies" in constants.MOVE_TARGET_SELF


def test_howl_boosts_the_user_not_the_opponent():
    instructions = _instructions("howl")
    assert ("boost", "user", constants.ATTACK, 1) in instructions
    assert not [i for i in instructions if i[0] == "boost" and i[1] == "opponent"]


def test_patch_is_idempotent_across_reload():
    _load_hook()
    assert constants.MOVE_TARGET_SELF.count("allies") == 1


def test_ally_target_heals_still_recover_the_user():
    # These never depended on MOVE_TARGET_SELF (recovery keys on heal_target), so the
    # patch must leave them exactly as they were.
    for move in ("lifedew", "junglehealing", "lunarblessing"):
        heals = [i for i in _instructions(move) if i[0] == "heal"]
        assert heals, f"{move} produced no heal instruction"
        assert all(i[1] == "user" for i in heals), (
            f"{move} healed the opponent: {heals}"
        )


def test_opponent_targets_are_unchanged():
    # The append must not make an opponent-directed boost resolve to the user.
    assert "allies" not in constants.MOVE_TARGET_OPPONENT
    instructions = _instructions("growl")
    assert ("boost", "opponent", constants.ATTACK, -1) in instructions
