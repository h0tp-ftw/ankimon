import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

_SRC = Path(__file__).parent.parent / "src"
_HOOK_PATH = _SRC / "Ankimon" / "functions" / "ankimon_hooks_to_poke_engine.py"


def _module(name, **attrs):
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


def _package(name):
    mod = types.ModuleType(name)
    mod.__path__ = []
    mod.__package__ = name
    return mod


@pytest.fixture
def hook_env(monkeypatch):
    for name in (
        "Ankimon",
        "Ankimon.functions",
        "Ankimon.pyobj",
        "Ankimon.poke_engine",
        "Ankimon.poke_engine.special_effects",
        "Ankimon.poke_engine.special_effects.moves",
        "Ankimon.poke_engine.special_effects.abilities",
    ):
        monkeypatch.setitem(sys.modules, name, _package(name))

    constants = _module(
        "Ankimon.poke_engine.constants",
        WEIGHT="weight",
        OPPONENT="opponent",
        MOVE_TARGET_SELF=[],
        MUTATOR_CHANGE_STATS="change_stats",
    )
    monkeypatch.setitem(sys.modules, constants.__name__, constants)

    instruction_generator = _module(
        "Ankimon.poke_engine.instruction_generator",
        get_instructions_from_damage=lambda *a, **k: None,
    )
    monkeypatch.setitem(sys.modules, instruction_generator.__name__, instruction_generator)

    poke_engine = sys.modules["Ankimon.poke_engine"]
    poke_engine.constants = constants
    poke_engine.instruction_generator = instruction_generator
    poke_engine.damage_calculator = SimpleNamespace(pokedex=None)

    dummy = type("Dummy", (), {})
    monkeypatch.setitem(sys.modules, "Ankimon.poke_engine.battle", _module(
        "Ankimon.poke_engine.battle", Move=dummy
    ))
    monkeypatch.setitem(sys.modules, "Ankimon.poke_engine.objects", _module(
        "Ankimon.poke_engine.objects",
        Pokemon=dummy,
        State=dummy,
        StateMutator=dummy,
        Side=dummy,
    ))
    monkeypatch.setitem(sys.modules, "Ankimon.poke_engine.helpers", _module(
        "Ankimon.poke_engine.helpers", normalize_name=lambda value: value
    ))
    monkeypatch.setitem(sys.modules, "Ankimon.poke_engine.find_state_instructions", _module(
        "Ankimon.poke_engine.find_state_instructions",
        get_all_state_instructions=lambda *a, **k: [],
    ))
    monkeypatch.setitem(sys.modules, "Ankimon.poke_engine.data", _module(
        "Ankimon.poke_engine.data", pokedex={"aegislash": {"weight": 53.0}}
    ))

    modify_move = _module("Ankimon.poke_engine.special_effects.moves.modify_move", pokedex=None)
    sys.modules["Ankimon.poke_engine.special_effects.moves"].modify_move = modify_move
    monkeypatch.setitem(sys.modules, modify_move.__name__, modify_move)

    before_move = _module(
        "Ankimon.poke_engine.special_effects.abilities.before_move",
        stancechange=lambda *a, **k: None,
    )
    sys.modules["Ankimon.poke_engine.special_effects.abilities"].before_move = before_move
    monkeypatch.setitem(sys.modules, before_move.__name__, before_move)

    monkeypatch.setitem(sys.modules, "Ankimon.services", _module(
        "Ankimon.services", services=SimpleNamespace()
    ))
    monkeypatch.setitem(sys.modules, "Ankimon.pyobj.error_handler", _module(
        "Ankimon.pyobj.error_handler", show_warning_with_traceback=lambda *a, **k: None
    ))

    spec = importlib.util.spec_from_file_location(
        "Ankimon.functions.ankimon_hooks_to_poke_engine", _HOOK_PATH
    )
    hook = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, hook)
    spec.loader.exec_module(hook)
    return hook, before_move


def _call(wrapper, pokemon):
    return wrapper(None, "user", {"id": "shadowball"}, pokemon, object())


def test_shield_form_is_delegated_as_engine_aegislash_and_restored(hook_env):
    hook, before_move = hook_env
    seen = []

    def original(state, side, move, attacker, defender):
        seen.append(attacker.id)
        return None

    before_move.stancechange = original
    hook._install_stancechange_compat()
    pokemon = SimpleNamespace(id="aegislashshield", maxhp=300)

    assert _call(before_move.stancechange, pokemon) is None
    assert seen == ["aegislash"]
    assert pokemon.id == "aegislashshield"


@pytest.mark.parametrize("form", ["aegislashshield", "aegislash", "aegislashblade"])
def test_form_id_is_restored_when_engine_raises(hook_env, form):
    hook, before_move = hook_env

    def original(state, side, move, attacker, defender):
        assert attacker.id == ("aegislash" if form == "aegislashshield" else form)
        raise RuntimeError("engine failure")

    before_move.stancechange = original
    hook._install_stancechange_compat()
    pokemon = SimpleNamespace(id=form, maxhp=300)

    with pytest.raises(RuntimeError, match="engine failure"):
        _call(before_move.stancechange, pokemon)

    assert pokemon.id == form


def test_other_species_pass_through_unchanged(hook_env):
    hook, before_move = hook_env
    seen = []

    def original(state, side, move, attacker, defender):
        seen.append(attacker.id)
        return "ok"

    before_move.stancechange = original
    hook._install_stancechange_compat()
    pokemon = SimpleNamespace(id="pikachu")

    assert _call(before_move.stancechange, pokemon) == "ok"
    assert seen == ["pikachu"]
    assert pokemon.id == "pikachu"


def test_installation_is_idempotent(hook_env):
    hook, before_move = hook_env
    before_move.stancechange = lambda *a, **k: "ok"

    hook._install_stancechange_compat()
    first_wrapper = before_move.stancechange
    hook._install_stancechange_compat()

    assert before_move.stancechange is first_wrapper


def test_hot_reload_upgrades_the_old_id_only_adapter(hook_env):
    hook, before_move = hook_env

    def old_adapter(state, side, move, attacker, defender):
        return [("change_stats", side, (300, 160, 70, 160, 70, 100),
                 (100, 70, 160, 70, 160, 100))]

    old_adapter._ankimon_stancechange_compat = True
    before_move.stancechange = old_adapter
    hook._install_stancechange_compat()
    pokemon = SimpleNamespace(id="aegislashshield", maxhp=300)
    result = _call(before_move.stancechange, pokemon)
    assert result[0][3] == (300, 70, 160, 70, 160, 100)
    assert pokemon.id == "aegislashshield"
