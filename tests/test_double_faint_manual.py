"""Manual-mode double faint: when the player's Pokémon and the wild Pokémon
faint on the same turn, ``handle_enemy_faint()`` puts the catch/defeat screen up
and returns without replacing the encounter. The main-faint handling must then
wait for the player's catch/defeat choice instead of running immediately —
otherwise ``handle_main_pokemon_faint()`` heals the main Pokémon and spawns a
fresh encounter over the screen the player still has to answer.

The bootstrap runs inside a fixture (not at collection time) and every
``sys.modules`` entry it touches is restored afterwards, so importing
``battle_loop`` here cannot change what a later test file sees.
"""

import sys
import types
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"


@pytest.fixture
def bl(monkeypatch, request):
    monkeypatch.syspath_prepend(str(_SRC))
    for name in ("Ankimon", "Ankimon.functions", "Ankimon.pyobj"):
        mod = sys.modules.get(name)
        if mod is None or not hasattr(mod, "__path__"):
            pkg = types.ModuleType(name)
            pkg.__path__ = [str(_SRC / name.replace(".", "/"))]
            pkg.__package__ = name
            monkeypatch.setitem(sys.modules, name, pkg)

    import importlib

    # Force a fresh import and fully undo it on teardown — restore both the
    # sys.modules entry and the attribute the import machinery binds on the
    # parent package — so a later test re-imports cleanly instead of inheriting
    # this module's patched state.
    parent = sys.modules["Ankimon"]
    prev_module = sys.modules.get("Ankimon.battle_loop")
    had_attr = "battle_loop" in vars(parent)
    prev_attr = getattr(parent, "battle_loop", None)

    def _restore():
        if prev_module is not None:
            sys.modules["Ankimon.battle_loop"] = prev_module
        else:
            sys.modules.pop("Ankimon.battle_loop", None)
        if had_attr:
            parent.battle_loop = prev_attr
        else:
            vars(parent).pop("battle_loop", None)

    request.addfinalizer(_restore)

    sys.modules.pop("Ankimon.battle_loop", None)
    module = importlib.import_module("Ankimon.battle_loop")
    module._main_faint_deferred = False
    return module


class _FakePokemon:
    def __init__(self, name="pikachu", hp=0, max_hp=20):
        self.name = name
        self.hp = hp
        self.current_hp = hp
        self.max_hp = max_hp

    def reset_bonuses(self):
        pass


class _FakeReviewer:
    def __init__(self):
        self.hud_refreshes = 0

    def refresh_hud(self):
        self.hud_refreshes += 1


@pytest.fixture
def hook_registry(monkeypatch):
    """Stand-in for ``Ankimon.hook_registry`` (the real one imports aqt)."""
    mod = types.ModuleType("Ankimon.hook_registry")
    mod.catch_pokemon_hooks = []
    mod.defeat_pokemon_hooks = []
    mod.add_catch_pokemon_hook = mod.catch_pokemon_hooks.append
    mod.add_defeat_pokemon_hook = mod.defeat_pokemon_hooks.append
    monkeypatch.setitem(sys.modules, "Ankimon.hook_registry", mod)
    return mod


@pytest.fixture
def spy_faint(bl, monkeypatch):
    calls = []
    monkeypatch.setattr(
        bl, "handle_main_pokemon_faint", lambda *a, **k: calls.append((a, k))
    )
    monkeypatch.setattr(bl, "is_alive", lambda w: w is not None)
    monkeypatch.setattr(
        bl, "services", types.SimpleNamespace(test_window=object()), raising=False
    )
    return calls


def test_deferral_waits_for_the_player_choice(bl, hook_registry, spy_faint):
    main, enemy = _FakePokemon(), _FakePokemon()
    reviewer = _FakeReviewer()

    bl._defer_main_faint_until_enemy_resolved(main, enemy, reviewer, translator=object())

    # Nothing runs yet: both hook buckets are armed, the faint handler is idle.
    assert spy_faint == []
    assert len(hook_registry.catch_pokemon_hooks) == 1
    assert len(hook_registry.defeat_pokemon_hooks) == 1

    # Player picks "catch" -> that hook fires -> the deferred faint resolves once,
    # with spawn_replacement=False (the catch flow spawns the encounter itself).
    hook_registry.catch_pokemon_hooks[0]()

    assert len(spy_faint) == 1
    assert spy_faint[0][1]["spawn_replacement"] is False
    assert reviewer.hud_refreshes == 1
    # Both one-shot hooks are removed so the defeat path can't double-resolve.
    assert hook_registry.catch_pokemon_hooks == []
    assert hook_registry.defeat_pokemon_hooks == []


def test_second_round_does_not_stack_another_callback(bl, hook_registry, spy_faint):
    main, enemy = _FakePokemon(), _FakePokemon()
    reviewer = _FakeReviewer()

    bl._defer_main_faint_until_enemy_resolved(main, enemy, reviewer, translator=object())
    bl._defer_main_faint_until_enemy_resolved(main, enemy, reviewer, translator=object())

    assert len(hook_registry.catch_pokemon_hooks) == 1
    assert len(hook_registry.defeat_pokemon_hooks) == 1

    hook_registry.defeat_pokemon_hooks[0]()
    assert len(spy_faint) == 1

    # After resolution the module is disarmed and can defer a fresh faint again.
    bl._defer_main_faint_until_enemy_resolved(main, enemy, reviewer, translator=object())
    assert len(hook_registry.catch_pokemon_hooks) == 1


# --- Regression: an ABANDONED deferral must not fire a phantom faint --------
#
# _resolve() used to be the only thing that cleared _main_faint_deferred or
# unregistered itself. A player who closed the Ankimon Window instead of
# answering its death screen left the deferral armed forever, and the stale
# callback then ran handle_main_pokemon_faint() against a full-HP Pokemon on
# the next unrelated catch/defeat.


def test_cancel_disarms_the_flag_and_unregisters_from_both_buckets(
    bl, hook_registry, spy_faint
):
    main = _FakePokemon(hp=0)
    enemy = _FakePokemon("rattata", hp=0)
    bl._defer_main_faint_until_enemy_resolved(main, enemy, _FakeReviewer(), None)
    assert bl._main_faint_deferred is True
    assert len(hook_registry.catch_pokemon_hooks) == 1
    assert len(hook_registry.defeat_pokemon_hooks) == 1

    bl._cancel_main_faint_deferral()

    assert bl._main_faint_deferred is False
    assert hook_registry.catch_pokemon_hooks == []
    assert hook_registry.defeat_pokemon_hooks == []
    # Idempotent: calling it again with nothing armed is a no-op, not an error.
    bl._cancel_main_faint_deferral()


def test_abandoned_deferral_fires_no_phantom_faint_on_a_later_catch(
    bl, hook_registry, spy_faint
):
    main = _FakePokemon(hp=0)
    enemy = _FakePokemon("rattata", hp=0)
    reviewer = _FakeReviewer()

    # Round 1: double faint with the window open -> deferral armed.
    bl._defer_main_faint_until_enemy_resolved(main, enemy, reviewer, None)

    # Round 2: the player closed the window, so on_review_card takes the
    # immediate branch, which now disarms the stale deferral first.
    bl._cancel_main_faint_deferral()
    bl.handle_main_pokemon_faint(main, enemy, None, reviewer, None)
    settled = len(spy_faint)

    # Healed and back in a normal battle.
    main.hp = main.max_hp

    # A later, unrelated catch must NOT resurrect the main faint.
    for hook in list(hook_registry.catch_pokemon_hooks):
        hook()
    assert len(spy_faint) - settled == 0
    for hook in list(hook_registry.defeat_pokemon_hooks):
        hook()
    assert len(spy_faint) - settled == 0


def test_resolve_repaints_the_ankimon_window_after_healing(
    bl, hook_registry, monkeypatch
):
    """new_pokemon() paints the new encounter while main hp is still 0, so the
    resolver has to repaint or the main HP bar reads 0 until the next card."""
    repaints = []
    window = types.SimpleNamespace(
        current_view="battle",
        force_display_battle=lambda *a, **k: repaints.append(1),
    )
    monkeypatch.setattr(bl, "handle_main_pokemon_faint", lambda *a, **k: None)
    monkeypatch.setattr(bl, "is_alive", lambda w: w is not None)
    monkeypatch.setattr(
        bl, "services", types.SimpleNamespace(test_window=window), raising=False
    )

    main = _FakePokemon(hp=0)
    bl._defer_main_faint_until_enemy_resolved(
        main, _FakePokemon("rattata", hp=0), _FakeReviewer(), None
    )
    for hook in list(hook_registry.catch_pokemon_hooks):
        hook()

    assert repaints, "the Ankimon Window was never repainted after the heal"


# --- The deferral must report whether it could actually arm ----------------
#
# hook_registry imports aqt. Where that import fails there is nothing to fire
# the resolver, so deferring would park the main Pokemon at 0 HP permanently;
# the caller has to fall back to handling the faint immediately.


def test_defer_returns_true_when_it_arms(bl, hook_registry, spy_faint):
    armed = bl._defer_main_faint_until_enemy_resolved(
        _FakePokemon(hp=0), _FakePokemon("rattata", hp=0), _FakeReviewer(), None
    )
    assert armed is True
    assert bl._main_faint_deferred is True
    assert len(hook_registry.catch_pokemon_hooks) == 1


def test_defer_reports_already_armed_as_still_deferred(bl, hook_registry, spy_faint):
    main, enemy, rev = _FakePokemon(hp=0), _FakePokemon("rattata", hp=0), _FakeReviewer()
    assert bl._defer_main_faint_until_enemy_resolved(main, enemy, rev, None) is True
    # A second round must NOT stack a callback, but a deferral IS still in
    # effect -- reporting False here would make the caller handle the faint
    # immediately and heal main out from under the pending decision.
    assert bl._defer_main_faint_until_enemy_resolved(main, enemy, rev, None) is True
    assert len(hook_registry.catch_pokemon_hooks) == 1


def test_defer_returns_false_and_stays_disarmed_without_a_hook_registry(
    bl, monkeypatch, spy_faint
):
    # sys.modules[name] = None makes `from . import hook_registry` raise.
    monkeypatch.setitem(sys.modules, "Ankimon.hook_registry", None)

    armed = bl._defer_main_faint_until_enemy_resolved(
        _FakePokemon(hp=0), _FakePokemon("rattata", hp=0), _FakeReviewer(), None
    )
    assert armed is False
    assert bl._main_faint_deferred is False, (
        "a deferral that could not register must not leave the flag armed"
    )
