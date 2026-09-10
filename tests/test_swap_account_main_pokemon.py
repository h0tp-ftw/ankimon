"""Tier-1 contract for singletons.swap_ankimon_account()'s main-Pokemon refresh.

update_main_pokemon() mutates the passed-in object IN PLACE only when the newly
active DB already has a saved main Pokemon; when it does not (e.g. the first
switch to ankimonDEV.db) it RETURNS a fresh, unrelated PokemonObject. The bug:
swap_ankimon_account() used to discard that return, so the live main_pokemon
singleton (shared by test_window / battle_loop / pokemon_pc) kept showing the
previous account's Pokemon after the swap.

The fix captures the return and, when it is a different object, copies its stats
onto the live singleton. These tests pin both branches:

* different object returned  -> live singleton.update_stats(**new.to_dict())
* same object returned       -> no update_stats (already mutated in place)

House pattern (mirrors tests/test_ankidex_singleton.py): stub aqt + the heavy
sibling modules, exec the REAL singletons module, drive swap_ankimon_account().
"""

import importlib.util
import sys
import threading
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
    spec = importlib.util.spec_from_file_location(
        "Ankimon.services", _src / "Ankimon" / "services.py"
    )
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "Ankimon.services", mod)
    spec.loader.exec_module(mod)
    return mod.services


class _FakeReviewerManager:
    def __init__(self, *a, **k):
        pass


class _FakeShopManager:
    def __init__(self, *a, **k):
        pass


@pytest.fixture
def swap_env(monkeypatch):
    """A fully-stubbed singletons module plus captured swap-dependency spies."""
    mw = SimpleNamespace()
    tooltip = MagicMock()
    monkeypatch.setitem(sys.modules, "aqt", _stub_module("aqt", mw=mw))
    monkeypatch.setitem(
        sys.modules, "aqt.utils", _stub_module("aqt.utils", tooltip=tooltip)
    )

    def is_alive(obj):
        if obj is None:
            return False
        try:
            obj.objectName()
            return True
        except (RuntimeError, AttributeError):
            return False

    monkeypatch.setitem(
        sys.modules, "Ankimon.utils", _stub_module("Ankimon.utils", is_alive=is_alive)
    )

    services = _fresh_services(monkeypatch)

    # A real (identity-stable) main-Pokemon spy so we can assert update_stats.
    main_pokemon = MagicMock(name="live_main_pokemon")
    main_pokemon.objectName.side_effect = AttributeError  # not a Qt window

    def build_core():
        objs = {
            "logger": MagicMock(),
            "db": MagicMock(name="db"),
            "settings": MagicMock(name="settings"),
            "translator": MagicMock(),
            "tracker": MagicMock(name="tracker"),
            "main_pokemon": main_pokemon,
            "enemy_pokemon": MagicMock(),
            "trainer_card": MagicMock(),
            "achievements": {"1": False},
        }
        services.populate(**objs)
        return SimpleNamespace(
            logger=objs["logger"],
            ankimon_db=objs["db"],
            settings_obj=objs["settings"],
            translator=objs["translator"],
            main_pokemon=objs["main_pokemon"],
            mainpokemon_empty=False,
            enemy_pokemon=objs["enemy_pokemon"],
            trainer_card=objs["trainer_card"],
            ankimon_tracker_obj=objs["tracker"],
            achievements=objs["achievements"],
        )

    monkeypatch.setitem(
        sys.modules,
        "Ankimon.core",
        _stub_module(
            "Ankimon.core", build_core=build_core, bind_runtime_globals=lambda: None
        ),
    )

    class QtPresenter:
        pass

    monkeypatch.setitem(
        sys.modules,
        "Ankimon.gui_presenter",
        _stub_module("Ankimon.gui_presenter", QtPresenter=QtPresenter),
    )
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.pyobj.ankimon_shop",
        _stub_module("Ankimon.pyobj.ankimon_shop", PokemonShopManager=_FakeShopManager),
    )
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.pyobj.reviewer_obj",
        _stub_module("Ankimon.pyobj.reviewer_obj", Reviewer_Manager=_FakeReviewerManager),
    )

    # --- swap_ankimon_account()'s function-local imports -------------------
    update_main_pokemon = MagicMock(name="update_main_pokemon")
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.functions.update_main_pokemon",
        _stub_module(
            "Ankimon.functions.update_main_pokemon",
            update_main_pokemon=update_main_pokemon,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.functions.encounter_functions",
        _stub_module(
            "Ankimon.functions.encounter_functions",
            new_pokemon=MagicMock(),
            clear_encounter_cache=MagicMock(),
        ),
    )
    mobile_sync_lock = threading.Lock()
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.functions.mobile_sync",
        _stub_module(
            "Ankimon.functions.mobile_sync",
            _mobile_sync_lock=mobile_sync_lock,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.reviewer_ui",
        _stub_module("Ankimon.reviewer_ui", set_collected_ids=MagicMock()),
    )
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.battle_loop",
        _stub_module("Ankimon.battle_loop", init_battle_state=MagicMock()),
    )
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.menu_buttons",
        _stub_module("Ankimon.menu_buttons", update_mobile_badge=MagicMock()),
    )

    sys.modules.pop("Ankimon.singletons", None)
    spec = importlib.util.spec_from_file_location(
        "Ankimon.singletons", _src / "Ankimon" / "singletons.py"
    )
    singletons = importlib.util.module_from_spec(spec)
    sys.modules["Ankimon.singletons"] = singletons
    spec.loader.exec_module(singletons)

    yield SimpleNamespace(
        singletons=singletons,
        services=services,
        main_pokemon=main_pokemon,
        update_main_pokemon=update_main_pokemon,
        mobile_sync_lock=mobile_sync_lock,
        tooltip=tooltip,
    )

    sys.modules.pop("Ankimon.singletons", None)


def test_swap_applies_fresh_main_pokemon_to_live_singleton(swap_env):
    """No saved main in the target DB -> a fresh object is returned; its stats
    must be copied onto the live singleton (else it shows the old account)."""
    fresh = SimpleNamespace(to_dict=lambda: {"name": "Ditto", "level": 5})
    swap_env.update_main_pokemon.return_value = (fresh, True)

    swap_env.singletons.swap_ankimon_account()

    swap_env.update_main_pokemon.assert_called_once_with(swap_env.main_pokemon)
    swap_env.main_pokemon.update_stats.assert_called_once_with(name="Ditto", level=5)


def test_swap_does_not_double_apply_when_mutated_in_place(swap_env):
    """Target DB has a saved main -> update_main_pokemon mutates the SAME object
    in place and returns it; swap must NOT redundantly call update_stats."""
    swap_env.update_main_pokemon.return_value = (swap_env.main_pokemon, False)

    swap_env.singletons.swap_ankimon_account()

    swap_env.main_pokemon.update_stats.assert_not_called()


def test_swap_aborts_while_mobile_resolution_holds_lock(swap_env):
    swap_env.mobile_sync_lock.acquire()
    try:
        swap_env.singletons.swap_ankimon_account()
    finally:
        swap_env.mobile_sync_lock.release()

    swap_env.services.db.switch_database.assert_not_called()
    swap_env.update_main_pokemon.assert_not_called()
    swap_env.tooltip.assert_called_once_with(
        "Cannot switch accounts while mobile battles are resolving."
    )
