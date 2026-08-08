"""Characterization tests for the profile-close cache-clear hook (F20).

Pins the two contracts of the ported scaffolding without Qt:

* ``_on_profile_close()`` drops the three in-memory performance caches
  (pokedex / learnset / encounter) and never lets an exception escape onto
  Anki's ``profile_will_close`` hook chain.
* The close handler is registered *idempotently*. Its registration record
  lives on the services registry, so neither a second ``register_profile_hooks``
  call nor a re-execution of the module (an add-on reload: new function objects,
  surviving registry) can stack a second copy onto
  ``gui_hooks.profile_will_close``.

House pattern (same as tests/test_reload_safe_singletons.py): stub ``aqt`` +
the heavy sibling modules in ``sys.modules``, exec the REAL profile_hooks.py
under its dotted name, and give it a FRESH real ``services`` registry per test
so the module-level record cannot leak between tests. ``gui_hooks`` hooks are
plain lists — ``.append`` / ``.remove`` behave exactly like the aqt hook API
this code uses, and an unmatched remove would fail loudly.
"""

import importlib.util
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_src = __import__("pathlib").Path(__file__).parent.parent / "src"


def _stub_module(name, **attrs):
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


def _fresh_services(monkeypatch):
    """Exec a fresh, REAL services registry (isolated from other tests)."""
    services_spec = importlib.util.spec_from_file_location(
        "Ankimon.services", _src / "Ankimon" / "services.py"
    )
    services_mod = importlib.util.module_from_spec(services_spec)
    monkeypatch.setitem(sys.modules, "Ankimon.services", services_mod)
    services_spec.loader.exec_module(services_mod)
    return services_mod.services


def _fresh_gui_hooks():
    """gui_hooks stub: plain lists, so an unmatched remove() fails loudly."""
    return SimpleNamespace(profile_did_open=[], profile_will_close=[])


def _exec_profile_hooks(monkeypatch, gui_hooks):
    """Exec the real profile_hooks.py against stubbed siblings. Each call
    returns a FRESH module object (fresh handler functions) — exactly what an
    add-on reload produces — while ``Ankimon.services`` is left to the caller.

    The three ``clear_*`` callables are separate MagicMocks so a test can assert
    each was called (and so encounter_functions' real pokedex.json import-time
    IO is never triggered)."""
    clear_pokedex = MagicMock(name="clear_pokedex_caches")
    clear_learnset = MagicMock(name="clear_learnset_cache")
    clear_encounter = MagicMock(name="clear_encounter_cache")
    clear_utils = MagicMock(name="clear_utils_caches")

    monkeypatch.setitem(
        sys.modules,
        "anki.hooks",
        _stub_module("anki.hooks", addHook=lambda *a, **k: None),
    )
    monkeypatch.setitem(
        sys.modules,
        "aqt",
        _stub_module("aqt", gui_hooks=gui_hooks, mw=SimpleNamespace()),
    )
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.singletons",
        _stub_module(
            "Ankimon.singletons",
            settings_obj=MagicMock(name="settings_obj"),
            logger=MagicMock(name="logger"),
        ),
    )
    monkeypatch.setitem(
        sys.modules, "Ankimon.utils",
        _stub_module("Ankimon.utils", test_online_connectivity=lambda: False, clear_utils_caches=clear_utils),
    )
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.pyobj.ankimon_sync",
        _stub_module(
            "Ankimon.pyobj.ankimon_sync",
            setup_ankimon_sync_hooks=MagicMock(),
            check_and_sync_pokemon_data=MagicMock(),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.pyobj.tip_of_the_day",
        _stub_module("Ankimon.pyobj.tip_of_the_day", show_tip_of_the_day=MagicMock()),
    )
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.pyobj.pokemon_trade",
        _stub_module(
            "Ankimon.pyobj.pokemon_trade",
            check_and_award_monthly_pokemon=MagicMock(),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.pyobj.error_handler",
        _stub_module(
            "Ankimon.pyobj.error_handler", show_warning_with_traceback=MagicMock()
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.functions.pokedex_functions",
        _stub_module(
            "Ankimon.functions.pokedex_functions", clear_pokedex_caches=clear_pokedex
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.functions.learnset_retrieval",
        _stub_module(
            "Ankimon.functions.learnset_retrieval", clear_learnset_cache=clear_learnset
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.functions.encounter_functions",
        _stub_module(
            "Ankimon.functions.encounter_functions",
            clear_encounter_cache=clear_encounter,
        ),
    )

    spec = importlib.util.spec_from_file_location(
        "Ankimon.profile_hooks", _src / "Ankimon" / "profile_hooks.py"
    )
    profile_hooks = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "Ankimon.profile_hooks", profile_hooks)
    spec.loader.exec_module(profile_hooks)
    profile_hooks._clears = (clear_pokedex, clear_learnset, clear_encounter, clear_utils)
    return profile_hooks


def _register(profile_hooks):
    profile_hooks.register_profile_hooks(
        online_connectivity=False,
        backup_manager=MagicMock(on_anki_close=MagicMock()),
        CatchPokemonHook=MagicMock(),
        DefeatPokemonHook=MagicMock(),
        add_catch_pokemon_hook=MagicMock(),
        add_defeat_pokemon_hook=MagicMock(),
        collected_pokemon_ids=set(),
    )


def test_on_profile_close_clears_all_caches(monkeypatch):
    _fresh_services(monkeypatch)
    gui_hooks = _fresh_gui_hooks()
    profile_hooks = _exec_profile_hooks(monkeypatch, gui_hooks)

    profile_hooks._on_profile_close()

    for clear in profile_hooks._clears:
        clear.assert_called_once_with()


def test_on_profile_close_swallows_clear_errors(monkeypatch):
    """A cache-clear failure must not propagate onto the profile-close chain."""
    _fresh_services(monkeypatch)
    gui_hooks = _fresh_gui_hooks()
    profile_hooks = _exec_profile_hooks(monkeypatch, gui_hooks)
    profile_hooks._clears[0].side_effect = RuntimeError("boom")

    # Must not raise.
    profile_hooks._on_profile_close()


def test_cache_clear_hook_registered_once(monkeypatch):
    _fresh_services(monkeypatch)
    gui_hooks = _fresh_gui_hooks()
    profile_hooks = _exec_profile_hooks(monkeypatch, gui_hooks)

    _register(profile_hooks)

    assert gui_hooks.profile_will_close.count(profile_hooks._on_profile_close) == 1


def test_cache_clear_hook_idempotent_on_repeat_register(monkeypatch):
    _fresh_services(monkeypatch)
    gui_hooks = _fresh_gui_hooks()
    profile_hooks = _exec_profile_hooks(monkeypatch, gui_hooks)

    _register(profile_hooks)
    _register(profile_hooks)

    assert gui_hooks.profile_will_close.count(profile_hooks._on_profile_close) == 1


def test_cache_clear_hook_idempotent_on_module_reexec(monkeypatch):
    """The F31 failure this guards: re-executing profile_hooks (an add-on
    reload) creates a NEW ``_on_profile_close`` object, so a module-level flag
    would reset and remove-by-identity would miss the old handler. The
    registry-stored record must swap the handler instead of stacking a copy."""
    _fresh_services(monkeypatch)
    gui_hooks = _fresh_gui_hooks()

    mod1 = _exec_profile_hooks(monkeypatch, gui_hooks)
    _register(mod1)

    # Reload: fresh module + functions, same gui_hooks, surviving registry.
    mod2 = _exec_profile_hooks(monkeypatch, gui_hooks)
    assert mod2._on_profile_close is not mod1._on_profile_close
    _register(mod2)

    assert gui_hooks.profile_will_close.count(mod2._on_profile_close) == 1
    assert mod1._on_profile_close not in gui_hooks.profile_will_close


# --- Mobile-review sync wiring (decoupling fix) ------------------------------
# The profile_did_open handler must register the AnkiWeb sync hooks
# SYNCHRONOUSLY and independent of the legacy misc.ankiweb_sync file-sync
# toggle, so mobile-review detection is live for a default-config user. The
# file-based data-sync DIALOG, by contrast, stays gated behind that toggle.


class _Future:
    def __init__(self, value):
        self._value = value

    def result(self):
        return self._value


def _fire_profile_did_open(monkeypatch, *, ankiweb_sync, mobile_enabled=True):
    """Register hooks, then fire the profile_did_open handler with the given
    settings and return (profile_hooks, its stubbed ankimon_sync module)."""
    _fresh_services(monkeypatch)
    gui_hooks = _fresh_gui_hooks()
    profile_hooks = _exec_profile_hooks(monkeypatch, gui_hooks)

    def _get(key, default=None):
        return {
            "misc.ankiweb_sync": ankiweb_sync,
            "mobile.enabled": mobile_enabled,
        }.get(key, default)

    profile_hooks.settings_obj.get.side_effect = _get

    # Run the backgrounded connectivity task synchronously so on_done executes.
    def _run_in_background(task, on_done=None):
        value = task()
        if on_done is not None:
            on_done(_Future(value))

    profile_hooks.mw.taskman = SimpleNamespace(run_in_background=_run_in_background)

    _register(profile_hooks)
    handler = gui_hooks.profile_did_open[0]
    handler()

    sync_mod = sys.modules["Ankimon.pyobj.ankimon_sync"]
    return profile_hooks, sync_mod


def test_sync_hooks_registered_even_when_ankiweb_sync_disabled(monkeypatch):
    """Regression guard: mobile-review detection must be wired for a DEFAULT
    user (misc.ankiweb_sync=False). Previously on_done returned early on the
    False flag and setup_ankimon_sync_hooks was never called, so a mid-session
    sync never turned phone reviews into battles."""
    _, sync_mod = _fire_profile_did_open(monkeypatch, ankiweb_sync=False)

    sync_mod.setup_ankimon_sync_hooks.assert_called_once()
    # The OPT-IN file-based data-sync dialog stays gated behind the toggle.
    sync_mod.check_and_sync_pokemon_data.assert_not_called()


def test_sync_hooks_registered_when_ankiweb_sync_enabled(monkeypatch):
    """With the file-sync toggle on, the hooks still register (unconditional)."""
    _, sync_mod = _fire_profile_did_open(monkeypatch, ankiweb_sync=True)

    sync_mod.setup_ankimon_sync_hooks.assert_called_once()
