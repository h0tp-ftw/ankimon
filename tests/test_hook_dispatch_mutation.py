"""A hook may unregister itself from the bucket it is being dispatched from —
the manual-mode double-faint resolver does exactly that. Iterating the live
list meant ``remove()`` shifted every later element down one while the
iterator held an index, so the hook registered immediately AFTER the
self-removing one was silently skipped for that catch/defeat.

These buckets are published to other add-ons via ``mw.add_catch_pokemon_hook``
(profile_hooks.py), so the skipped hook can belong to a third party.
"""

import sys
import types
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"


@pytest.fixture
def hooks(monkeypatch, request):
    monkeypatch.syspath_prepend(str(_SRC))
    for name in ("Ankimon", "Ankimon.functions"):
        mod = sys.modules.get(name)
        if mod is None or not hasattr(mod, "__path__"):
            pkg = types.ModuleType(name)
            pkg.__path__ = [str(_SRC / name.replace(".", "/"))]
            pkg.__package__ = name
            monkeypatch.setitem(sys.modules, name, pkg)

    # Stub the two heavy imports hook_registry pulls in at module scope.
    singletons = types.ModuleType("Ankimon.singletons")
    singletons.enemy_pokemon = types.SimpleNamespace(hp=10)  # >0 => skip catch/kill
    singletons.main_pokemon = object()
    singletons.ankimon_tracker_obj = object()
    singletons.get_test_window = lambda: None
    singletons.get_evo_window = lambda: None
    singletons.logger = None
    singletons.achievements = {}
    singletons.trainer_card = None
    singletons.reviewer_obj = None
    monkeypatch.setitem(sys.modules, "Ankimon.singletons", singletons)

    enc = types.ModuleType("Ankimon.functions.encounter_functions")
    enc.catch_pokemon = lambda *a, **k: None
    enc.kill_pokemon = lambda *a, **k: None
    enc.new_pokemon = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "Ankimon.functions.encounter_functions", enc)

    import importlib

    # Restore BOTH the sys.modules entry and the attribute the import machinery
    # binds on the parent package. Restoring only sys.modules leaves
    # ``Ankimon.hook_registry`` pointing at this test's replacement, so a later
    # ``from Ankimon import hook_registry`` keeps resolving to it.
    parent = sys.modules["Ankimon"]
    prev_module = sys.modules.get("Ankimon.hook_registry")
    had_attr = "hook_registry" in vars(parent)
    prev_attr = getattr(parent, "hook_registry", None)

    def _restore():
        if prev_module is not None:
            sys.modules["Ankimon.hook_registry"] = prev_module
        else:
            sys.modules.pop("Ankimon.hook_registry", None)
        if had_attr:
            parent.hook_registry = prev_attr
        else:
            vars(parent).pop("hook_registry", None)

    request.addfinalizer(_restore)
    sys.modules.pop("Ankimon.hook_registry", None)
    return importlib.import_module("Ankimon.hook_registry")


@pytest.mark.parametrize(
    "bucket_name,dispatch",
    [
        ("catch_pokemon_hooks", lambda hr: hr.CatchPokemonHook([])),
        ("defeat_pokemon_hooks", lambda hr: hr.DefeatPokemonHook()),
    ],
)
def test_a_self_removing_hook_does_not_skip_the_next_one(hooks, bucket_name, dispatch):
    bucket = getattr(hooks, bucket_name)
    bucket.clear()
    ran = []

    def self_removing():
        ran.append("first")
        bucket.remove(self_removing)

    def third_party():
        ran.append("second")

    bucket.append(self_removing)
    bucket.append(third_party)

    dispatch(hooks)

    assert ran == ["first", "second"], (
        f"{bucket_name}: the hook after a self-removing one was skipped ({ran})"
    )
    assert bucket == [third_party]
