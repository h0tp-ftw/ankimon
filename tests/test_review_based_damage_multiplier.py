"""Parity test for F37 — Review-based-damage poke_engine multiplier rewrite.

Behaviour ported from BRRRR_Experimental onto main's seam architecture. The
feature wraps ``poke_engine.instruction_generator.get_instructions_from_damage``
so that, when ``settings.battle.review_based_damage`` is on, opponent-directed
damage is scaled by the reviewer multiplier at the engine level (and flagged so
the post-hoc pass in ``simulate_battle_with_poke_engine`` does not double-apply).

These are deterministic golden-value checks of the observable contract:
  * the exact damage-scaling math (floor, with ``max(1, ...)`` for positive
    damage, plain floor for non-positive damage),
  * the "applied" dedup flag,
  * pass-through when the wrapper must not fire (non-opponent defender, no
    multiplier attribute, ``damage is None``),
  * reload-safety: the monkeypatch is idempotent and never double-wraps.

The end-to-end seed->golden behaviour of the whole battle path is additionally
covered by the GAMEPLAY harness gates (probe_real_play / longrun / economy).
"""

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_src = Path(__file__).parent.parent / "src"


def _ensure_pkg(name):
    """(Re)establish ``name`` as a real package rooted at src/.

    Sibling test modules pollute ``sys.modules`` (e.g. test_database_manager
    replaces ``sys.modules["Ankimon"]`` with a path-less ModuleType, which breaks
    ``from Ankimon.*`` imports for whichever test runs next). Restoring the
    package stub with a ``__path__`` makes this test robust to collection order.
    """
    mod = sys.modules.get(name)
    if not isinstance(mod, types.ModuleType) or not getattr(mod, "__path__", None):
        stub = types.ModuleType(name)
        stub.__path__ = [str(_src / name.replace(".", "/"))]
        stub.__package__ = name
        sys.modules[name] = stub


for _pkg in ("Ankimon", "Ankimon.functions", "Ankimon.pyobj"):
    _ensure_pkg(_pkg)

# The module under test imports the services registry and the error dialog at
# module scope but does not use them in the code paths exercised here; stub them
# so the import never needs aqt or a booted app.
sys.modules.setdefault("Ankimon.services", MagicMock())
sys.modules["Ankimon.pyobj.error_handler"] = MagicMock()

# The real poke_engine IS needed (the wrapper delegates to the genuine engine);
# drop any non-real stand-ins so the actual modules load under Ankimon.__path__.
for _name in [n for n in list(sys.modules) if n.startswith("Ankimon.poke_engine")]:
    _m = sys.modules[_name]
    if not isinstance(_m, types.ModuleType) or getattr(_m, "__file__", None) is None:
        del sys.modules[_name]

from Ankimon.poke_engine import constants, instruction_generator

# Load the module under test fresh from its file; importing installs the
# poke-engine damage-wrapper monkeypatch (idempotently).
_spec = importlib.util.spec_from_file_location(
    "Ankimon.functions.ankimon_hooks_to_poke_engine",
    _src / "Ankimon" / "functions" / "ankimon_hooks_to_poke_engine.py",
)
hook = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = hook
_spec.loader.exec_module(hook)


class _FakeMutator:
    """Minimal stand-in; the wrapper only touches these attributes."""
    state = None


@pytest.fixture
def spy(monkeypatch):
    """Redirect the installed wrapper's delegated original to a recording spy.

    The wrapper resolves ``_original_get_instructions_from_damage`` as a module
    global of ``hook`` at call time, so patching that global intercepts the call
    without needing a real poke-engine ``State``.
    """
    calls = {}

    def _spy(mutator, defender, damage, accuracy, attacking_move, instruction):
        calls["damage"] = damage
        calls["defender"] = defender
        return ["SENTINEL"]

    monkeypatch.setattr(hook, "_original_get_instructions_from_damage", _spy)
    return calls


def _call(defender, damage, mult=None):
    m = _FakeMutator()
    if mult is not None:
        m.review_based_damage_multiplier = mult
    out = instruction_generator.get_instructions_from_damage(
        m, defender, damage, True, {}, object()
    )
    return m, out


def test_wrapper_is_installed():
    fn = instruction_generator.get_instructions_from_damage
    assert getattr(fn, "_ankimon_review_wrapped", False) is True
    # The captured original must be the genuine engine function, never a wrapper.
    assert (
        getattr(
            hook._original_get_instructions_from_damage,
            "_ankimon_review_wrapped",
            False,
        )
        is False
    )


def test_positive_damage_scaled_and_flagged(spy):
    m, out = _call(constants.OPPONENT, 10, mult=2.5)
    assert spy["damage"] == 25  # max(1, floor(10 * 2.5))
    assert m.review_based_damage_multiplier_applied is True
    assert out == ["SENTINEL"]


def test_positive_damage_floors_to_at_least_one(spy):
    # floor(3 * 0.05) == 0, but positive damage never drops below 1.
    _call(constants.OPPONENT, 3, mult=0.05)
    assert spy["damage"] == 1


def test_zero_damage_uses_plain_floor_and_still_flags(spy):
    m, _ = _call(constants.OPPONENT, 0, mult=2.0)
    assert spy["damage"] == 0  # floor(0 * 2.0), no max(1, ...) clamp
    assert m.review_based_damage_multiplier_applied is True


def test_negative_damage_uses_plain_floor(spy):
    # Healing / negative damage keeps its sign (no min-1 clamp).
    _call(constants.OPPONENT, -4, mult=1.5)
    assert spy["damage"] == -6  # floor(-4 * 1.5)


def test_passthrough_for_non_opponent_defender(spy):
    m, _ = _call("__self__", 10, mult=9.0)
    assert spy["damage"] == 10  # unchanged
    assert not hasattr(m, "review_based_damage_multiplier_applied")


def test_passthrough_without_multiplier_attribute(spy):
    m, _ = _call(constants.OPPONENT, 10, mult=None)
    assert spy["damage"] == 10  # unchanged
    assert not hasattr(m, "review_based_damage_multiplier_applied")


def test_passthrough_when_damage_is_none(spy):
    m, _ = _call(constants.OPPONENT, None, mult=2.0)
    assert spy["damage"] is None
    assert not hasattr(m, "review_based_damage_multiplier_applied")


def test_monkeypatch_is_idempotent_on_reload():
    fn_before = instruction_generator.get_instructions_from_damage
    original_before = hook._original_get_instructions_from_damage

    importlib.reload(hook)

    # Reload must not re-wrap: the installed function and the captured pristine
    # original are unchanged, so damage can never be scaled twice.
    assert instruction_generator.get_instructions_from_damage is fn_before
    assert hook._original_get_instructions_from_damage is original_before
    assert getattr(original_before, "_ankimon_review_wrapped", False) is False


def test_normalize_name_fix_matters_for_reset_trigger():
    """The reset trigger now compares against normalize_name(name).

    poke-engine ``Pokemon.id`` values are normalized (lowercased, punctuation and
    whitespace stripped). The base code compared them against ``name.lower()``,
    which disagrees for multi-word / punctuated species and forced a spurious
    full state reset every turn; F37 uses ``normalize_name`` instead.
    """
    from Ankimon.poke_engine.helpers import normalize_name

    for name in ["Mr. Mime", "Farfetch'd", "Type: Null", "Ho-Oh"]:
        assert normalize_name(name) != name.lower()
