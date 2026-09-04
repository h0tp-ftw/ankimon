"""Parity / characterization tests for F46 (PokemonObject persistence + display helpers).

Behaviour under test is BRRRR_Experimental's, re-fit onto main's service seam:

* ``PokemonObject.give_held_item`` / ``remove_held_item`` persist the held item to
  the thread-safe DB (``services.db``) *and* mirror it onto the in-memory
  ``services.main_pokemon`` singleton when the edited individual is the main pokemon
  (exp used ``mw.ankimon_db`` / ``mw.main_pokemon``; here it is the seam).
  Re-fit of exp's ``tests/test_held_items.py`` (Web-Bag test dropped: other domain).
* ``PokemonObject.display_name`` / ``pokedex_id`` / ``generation`` display helpers.
* ``functions.pokemon_functions.save_fossil_pokemon`` tags fossils with
  ``tier="Fossil"`` again and hardens the HP read via ``safe_int``.
* ``functions.update_main_pokemon`` prefers a stored species ``name`` and only
  falls back to a pokedex-id lookup for legacy records.

Runs Qt-free (venv_t1); heavy/aqt dependencies are stubbed in ``sys.modules``.
"""

import csv
import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_src = Path(__file__).parent.parent / "src"


class _MockResources:
    """Path-returning stand-in for Ankimon.resources (any attr -> /tmp/<name>)."""

    pokedex_path = _src / "Ankimon" / "data_files" / "pokedex.json"

    def __getattr__(self, name):
        return Path("/tmp") / name


def _register_packages():
    for _pkg in ("Ankimon", "Ankimon.functions", "Ankimon.pyobj"):
        mod = sys.modules.get(_pkg)
        if not mod or isinstance(mod, MagicMock) or not hasattr(mod, "__path__"):
            mod = types.ModuleType(_pkg)
            sys.modules[_pkg] = mod
        mod.__path__ = [str(_src / _pkg.replace(".", "/"))]
        mod.__package__ = _pkg


def _stub_aqt():
    for name in (
        "aqt",
        "aqt.qt",
        "aqt.utils",
        "aqt.gui_hooks",
        "aqt.operations",
        "aqt.reviewer",
        "aqt.webview",
        "aqt.main",
        "aqt.theme",
        "anki",
        "anki.hooks",
        "anki.collection",
        "anki.notes",
    ):
        sys.modules[name] = MagicMock()


def _force_load(name, filepath):
    spec = importlib.util.spec_from_file_location(name, filepath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_seam_and_pokemon_obj():
    """Load the real (aqt-free) service seam + a freshly executed pokemon_obj."""
    _stub_aqt()
    _register_packages()
    sys.modules["Ankimon.resources"] = _MockResources()
    sys.modules["Ankimon.singletons"] = MagicMock()
    # to_dict() lazily computes CP via ..business; ensure a functional stub even
    # if a prior test file left Ankimon.business as a bare MagicMock.
    _business = MagicMock()
    _business.pokemon_go_raw_stats.return_value = (100, 100, 100)
    _business.calculate_pokemon_go_cp.return_value = 500
    sys.modules["Ankimon.business"] = _business

    # Real seam modules (deliberately NOT stubbed) so we can populate them.
    services = _force_load(
        "Ankimon.services", _src / "Ankimon" / "services.py"
    ).services
    _force_load("Ankimon.events", _src / "Ankimon" / "events.py")
    db_mod = _force_load(
        "Ankimon.pyobj.database_manager",
        _src / "Ankimon" / "pyobj" / "database_manager.py",
    )
    pokemon_obj_mod = _force_load(
        "Ankimon.pyobj.pokemon_obj", _src / "Ankimon" / "pyobj" / "pokemon_obj.py"
    )
    services.reset()
    return services, db_mod.AnkimonDB, pokemon_obj_mod.PokemonObject


class _MockLogger:
    def log(self, *a, **k):
        pass

    def log_and_showinfo(self, *a, **k):
        pass


def _make_pokemon(PokemonObject, **overrides):
    base = dict(
        name="pikachu",
        id=25,
        shiny=False,
        level=5,
        ability="Static",
        type=["Electric"],
        gender="M",
        growth_rate="medium",
        captured_date=None,
        tier="Normal",
        individual_id="test-uuid",
        held_item=None,
    )
    base.update(overrides)
    return PokemonObject(**base)


@pytest.mark.parametrize("hp", [None, 0, 7])
def test_pokemon_object_normalizes_explicit_hp(hp):
    services, _AnkimonDB, PokemonObject = _load_seam_and_pokemon_obj()
    services.reset()

    pokemon = _make_pokemon(PokemonObject, hp=hp, current_hp=hp)
    expected_hp = pokemon.max_hp if hp is None else hp

    assert pokemon.hp == expected_hp
    assert pokemon.current_hp == expected_hp


# --------------------------------------------------------------------------- #
# Held-item persistence (re-fit of exp tests/test_held_items.py onto the seam) #
# --------------------------------------------------------------------------- #


@pytest.fixture
def held_item_env(tmp_path):
    services, AnkimonDB, PokemonObject = _load_seam_and_pokemon_obj()
    db = AnkimonDB(_MockLogger(), db_path=tmp_path / "ankimon.db")
    services.populate(db=db)

    # remove_held_item lazily does `from ..utils import give_item`; supply a fake
    # utils module that returns the item to the bag.
    utils_stub = types.ModuleType("Ankimon.utils")

    def _give_item(item_name, item_type=None):
        if db.get_item(item_name):
            db.update_item_quantity(item_name, 1)
        else:
            db.add_item(item_name, 1, {"type": item_type} if item_type else None)

    utils_stub.give_item = _give_item
    sys.modules["Ankimon.utils"] = utils_stub

    try:
        yield services, db, PokemonObject
    finally:
        services.reset()


def test_held_item_lifecycle(held_item_env):
    services, db, PokemonObject = held_item_env
    db.add_item("lucky-egg", 1)
    assert db.get_item("lucky-egg")["quantity"] == 1

    pkm = _make_pokemon(PokemonObject, individual_id="test-uuid")
    db.save_pokemon(pkm.to_dict())
    assert db.get_pokemon("test-uuid")["held_item"] is None

    pkm.give_held_item("lucky-egg")
    assert db.get_item("lucky-egg") is None  # quantity 0 -> removed
    assert db.get_pokemon("test-uuid")["held_item"] == "lucky-egg"

    pkm.remove_held_item()
    assert db.get_item("lucky-egg")["quantity"] == 1
    assert db.get_pokemon("test-uuid")["held_item"] is None


def test_main_pokemon_singleton_sync(held_item_env):
    services, db, PokemonObject = held_item_env

    main_pkm = _make_pokemon(
        PokemonObject, name="charizard", id=6, individual_id="main-uuid"
    )
    services.populate(main_pokemon=main_pkm)
    db.save_pokemon(main_pkm.to_dict())
    db.save_main_pokemon(main_pkm.to_dict())
    db.add_item("lucky-egg", 1)

    # A *different* in-memory object edits the same individual (UI action).
    editor = PokemonObject.from_dict(db.get_pokemon("main-uuid"))
    editor.give_held_item("lucky-egg")

    assert db.get_pokemon("main-uuid")["held_item"] == "lucky-egg"
    # The seam singleton must be mirrored, even though `editor` is a distinct object.
    assert services.main_pokemon.held_item == "lucky-egg"

    editor2 = PokemonObject.from_dict(db.get_pokemon("main-uuid"))
    editor2.remove_held_item()
    assert db.get_pokemon("main-uuid")["held_item"] is None
    assert services.main_pokemon.held_item is None


def test_singleton_sync_skips_non_main(held_item_env):
    services, db, PokemonObject = held_item_env
    main_pkm = _make_pokemon(PokemonObject, id=6, individual_id="main-uuid")
    services.populate(main_pokemon=main_pkm)
    db.add_item("lucky-egg", 1)

    other = _make_pokemon(PokemonObject, id=1, individual_id="other-uuid")
    db.save_pokemon(other.to_dict())
    other.give_held_item("lucky-egg")

    # Editing a non-main individual must NOT touch the main singleton.
    assert services.main_pokemon.held_item is None


# --------------------------------------------------------------------------- #
# Display helpers: display_name / pokedex_id / generation                      #
# --------------------------------------------------------------------------- #


def _install_pokedex_stub(**funcs):
    mod = types.ModuleType("Ankimon.functions.pokedex_functions")
    for k, v in funcs.items():
        setattr(mod, k, v)
    sys.modules["Ankimon.functions.pokedex_functions"] = mod
    setattr(sys.modules["Ankimon.functions"], "pokedex_functions", mod)


def _install_const_stub(gen_ids):
    mod = types.ModuleType("Ankimon.const")
    mod.gen_ids = gen_ids
    sys.modules["Ankimon.const"] = mod


def _install_encounter_data_stub(regional=None):
    mod = types.ModuleType("Ankimon.functions.encounter_data")
    if regional is not None:
        mod.REGIONAL_FORME_GEN = regional
    sys.modules["Ankimon.functions.encounter_data"] = mod
    setattr(sys.modules["Ankimon.functions"], "encounter_data", mod)


def test_display_name_prefers_distinct_nickname():
    services, _AnkimonDB, PokemonObject = _load_seam_and_pokemon_obj()
    services.reset()
    _install_pokedex_stub(
        get_pokemon_diff_lang_name=lambda pid, lang: "Pikachu",
        get_pretty_name_for_name=lambda name: "Pikachu",
    )
    pkm = _make_pokemon(PokemonObject, name="pikachu", nickname="Sparky")
    assert pkm.display_name == "Sparky"


def test_display_name_ignores_redundant_nickname():
    services, _AnkimonDB, PokemonObject = _load_seam_and_pokemon_obj()
    services.reset()
    _install_pokedex_stub(
        get_pokemon_diff_lang_name=lambda pid, lang: "Pikachu",
        get_pretty_name_for_name=lambda name: "Pikachu",
    )
    # Nickname is just a punctuation/spacing variant of the pretty name.
    pkm = _make_pokemon(PokemonObject, name="pikachu", nickname="pika-chu")
    # normalize() strips '-', so "pikachu" == "pikachu" -> redundant -> pretty name
    pkm.nickname = "Pikachu"
    assert pkm.display_name == "Pikachu"


def test_display_name_falls_back_to_pretty_when_untranslated():
    services, _AnkimonDB, PokemonObject = _load_seam_and_pokemon_obj()
    services.reset()
    _install_pokedex_stub(
        get_pokemon_diff_lang_name=lambda pid, lang: "No Translation in this language",
        get_pretty_name_for_name=lambda name: "Mega Charizard",
    )
    pkm = _make_pokemon(PokemonObject, name="charizardmegax", nickname="")
    assert pkm.display_name == "Mega Charizard"


def test_pokedex_id_resolves_form_to_species():
    services, _AnkimonDB, PokemonObject = _load_seam_and_pokemon_obj()
    services.reset()
    _install_pokedex_stub(
        search_pokedex_by_id=lambda i: "raichualola",
        search_pokedex=lambda name, var: 26 if var == "species_id" else None,
        safe_int=lambda v, default=0: int(v) if str(v).isdigit() else default,
    )
    # A form id (>= 10000) resolves to its base-species dex id.
    form = _make_pokemon(PokemonObject, id=10100)
    assert form.pokedex_id == 26
    # A normal id is returned unchanged (no lookup needed).
    normal = _make_pokemon(PokemonObject, id=25)
    assert normal.pokedex_id == 25


def test_generation_maps_species_via_gen_ids():
    services, _AnkimonDB, PokemonObject = _load_seam_and_pokemon_obj()
    services.reset()
    _install_pokedex_stub(
        search_pokedex_by_id=lambda i: "Pokémon not found",
        search_pokedex=lambda name, var: None,
        safe_int=lambda v, default=0: int(v) if str(v).isdigit() else default,
    )
    _install_const_stub({"gen_1": 151, "gen_2": 251, "gen_9": 1025})
    _install_encounter_data_stub(
        regional=None
    )  # REGIONAL_FORME_GEN absent (base state)

    assert _make_pokemon(PokemonObject, id=25).generation == 1
    assert _make_pokemon(PokemonObject, id=200).generation == 2
    assert _make_pokemon(PokemonObject, id=900).generation == 9


def test_generation_regional_forme_when_available():
    services, _AnkimonDB, PokemonObject = _load_seam_and_pokemon_obj()
    services.reset()
    _install_pokedex_stub(
        search_pokedex_by_id=lambda i: "meowthalola",
        search_pokedex=lambda name, var: "Alola" if var == "forme" else 52,
        safe_int=lambda v, default=0: int(v) if str(v).isdigit() else default,
    )
    _install_const_stub({"gen_1": 151, "gen_7": 809})
    # When the encounter-overhaul leaf lands REGIONAL_FORME_GEN, forms use it.
    _install_encounter_data_stub(regional={"Alola": 7})

    assert _make_pokemon(PokemonObject, id=10052).generation == 7


# --------------------------------------------------------------------------- #
# String-id robustness (ids from legacy / migrated JSON records may be str)    #
# --------------------------------------------------------------------------- #


def test_display_name_int_casts_string_id():
    services, _AnkimonDB, PokemonObject = _load_seam_and_pokemon_obj()
    services.reset()

    def _diff_lang(pid, lang):
        # Mirror the real lookup: it compares the id against 10000, which
        # raises TypeError for a str id — display_name must int-cast first.
        if pid >= 10000:
            return "Some Form"
        return "Pikachu"

    _install_pokedex_stub(
        get_pokemon_diff_lang_name=_diff_lang,
        get_pretty_name_for_name=lambda name: "UntranslatedFallback",
    )
    pkm = _make_pokemon(PokemonObject, name="pikachu", nickname="", id="25")
    # A string id must reach the translated lookup, not crash into the fallback.
    assert pkm.display_name == "Pikachu"


def test_pokedex_id_fallback_returns_int_for_garbage_id():
    services, _AnkimonDB, PokemonObject = _load_seam_and_pokemon_obj()
    services.reset()
    _install_pokedex_stub(
        search_pokedex_by_id=lambda i: "Pokémon not found",
        search_pokedex=lambda name, var: None,
        safe_int=lambda v, default=0: int(v) if str(v).isdigit() else default,
    )
    # Numeric string: normal path int-casts and returns the species id.
    assert _make_pokemon(PokemonObject, id="25").pokedex_id == 25
    # Unparseable id: the except-fallback must still honour the -> int contract.
    assert _make_pokemon(PokemonObject, id="not-a-number").pokedex_id == 1


def test_generation_emergency_fallback_int_casts_string_id():
    services, _AnkimonDB, PokemonObject = _load_seam_and_pokemon_obj()
    services.reset()
    # Empty stub module: the property's imports fail -> emergency ID-range path.
    _install_pokedex_stub()
    # A numeric-string id must be int-cast inside the fallback, not crash it.
    assert _make_pokemon(PokemonObject, id="100").generation == 1
    assert _make_pokemon(PokemonObject, id="905").generation == 8
    # An unparseable id degrades to the gen-1 default instead of raising.
    assert _make_pokemon(PokemonObject, id="fossil").generation == 1


# --------------------------------------------------------------------------- #
# save_fossil_pokemon: tier="Fossil" restored + safe_int HP                     #
# --------------------------------------------------------------------------- #


def _load_pokemon_functions_with_stubs(base_stats):
    _stub_aqt()
    _register_packages()
    sys.modules["Ankimon.resources"] = _MockResources()

    pdx = types.ModuleType("Ankimon.functions.pokedex_functions")
    pdx.get_base_experience = lambda *a, **k: 50
    pdx.get_growth_rate = lambda *a, **k: "medium-fast"
    pdx.search_pokedex = lambda name, var: {
        "baseStats": base_stats,
        "abilities": {"0": "swift-swim"},
        "types": ["Rock", "Water"],
        "actual_id": 140,
    }.get(var)
    pdx.search_pokedex_by_id = lambda i: "kabuto"
    pdx.safe_int = lambda v, default=0: (
        int(v) if str(v).lstrip("-").isdigit() else default
    )
    pdx._load_pokedex_cache = lambda: {}
    sys.modules["Ankimon.functions.pokedex_functions"] = pdx
    setattr(sys.modules["Ankimon.functions"], "pokedex_functions", pdx)

    bf = types.ModuleType("Ankimon.functions.battle_functions")
    bf.calculate_hp = lambda base_hp, level, ev, iv: 10 + int(base_hp)
    sys.modules["Ankimon.functions.battle_functions"] = bf
    setattr(sys.modules["Ankimon.functions"], "battle_functions", bf)

    lr = types.ModuleType("Ankimon.functions.learnset_retrieval")
    lr.get_random_moves_for_pokemon = lambda name, level: ["Tackle"]
    lr.get_levelup_move_for_pokemon = lambda *a, **k: []
    sys.modules["Ankimon.functions.learnset_retrieval"] = lr
    setattr(sys.modules["Ankimon.functions"], "learnset_retrieval", lr)

    services = _force_load(
        "Ankimon.services", _src / "Ankimon" / "services.py"
    ).services
    services.reset()
    captured = {}
    fake_db = MagicMock()
    fake_db.save_pokemon.side_effect = lambda d: captured.update(d)
    services.populate(db=fake_db)

    pf = _force_load(
        "Ankimon.functions.pokemon_functions",
        _src / "Ankimon" / "functions" / "pokemon_functions.py",
    )
    return pf, services, captured


def test_save_fossil_tags_fossil_tier():
    pf, services, captured = _load_pokemon_functions_with_stubs(
        base_stats={"hp": 30, "atk": 20, "def": 40, "spa": 20, "spd": 40, "spe": 20}
    )
    try:
        pf.save_fossil_pokemon(140)
        assert captured["tier"] == "Fossil"
        assert captured["current_hp"] == 40  # calculate_hp(30, ...) via stub
    finally:
        services.reset()


def test_save_fossil_safe_int_hp_no_crash_on_missing_hp():
    # Old code did int(stats["hp"]) -> KeyError; safe_int(stats.get("hp", 0)) must not crash.
    pf, services, captured = _load_pokemon_functions_with_stubs(base_stats={})
    try:
        pf.save_fossil_pokemon(140)
        assert captured["tier"] == "Fossil"
        assert captured["current_hp"] == 10  # calculate_hp(0, ...) via stub
    finally:
        services.reset()


# --------------------------------------------------------------------------- #
# update_main_pokemon: stored-name preference                                  #
# --------------------------------------------------------------------------- #


def _load_update_main_pokemon(main_record, name_lookup, *, migrated=True):
    _stub_aqt()
    _register_packages()
    sys.modules["Ankimon.resources"] = _MockResources()

    calls = {"by_id": 0}

    pdx = types.ModuleType("Ankimon.functions.pokedex_functions")

    def _by_id(i):
        calls["by_id"] += 1
        return name_lookup

    pdx.search_pokedex_by_id = _by_id
    pdx.search_pokedex = lambda name, var: {
        "hp": 48,
        "atk": 48,
        "def": 48,
        "spa": 48,
        "spd": 48,
        "spe": 48,
    }
    sys.modules["Ankimon.functions.pokedex_functions"] = pdx
    setattr(sys.modules["Ankimon.functions"], "pokedex_functions", pdx)

    # Real seam + real PokemonObject (build_default path exercises update_stats).
    services = _force_load(
        "Ankimon.services", _src / "Ankimon" / "services.py"
    ).services
    _force_load("Ankimon.events", _src / "Ankimon" / "events.py")
    _force_load(
        "Ankimon.pyobj.pokemon_obj", _src / "Ankimon" / "pyobj" / "pokemon_obj.py"
    )
    services.reset()

    fake_db = MagicMock()
    fake_db.is_migrated.return_value = migrated
    fake_db.get_main_pokemon.return_value = dict(main_record)
    services.populate(db=fake_db)
    calls["db"] = fake_db

    ump = _force_load(
        "Ankimon.functions.update_main_pokemon",
        _src / "Ankimon" / "functions" / "update_main_pokemon.py",
    )
    return ump, services, calls


def test_update_main_pokemon_prefers_stored_name():
    record = {
        "id": 25,
        "name": "pikachu",
        "level": 5,
        "ev": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
        "iv": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
        "current_hp": 20,
    }
    ump, services, calls = _load_update_main_pokemon(record, name_lookup="wrong-name")
    try:
        ump.update_main_pokemon()
        # Stored name present -> no id-based lookup performed.
        assert calls["by_id"] == 0
    finally:
        services.reset()


def test_update_main_pokemon_falls_back_to_id_lookup():
    record = {
        "id": 25,
        "level": 5,  # no "name" -> legacy record
        "ev": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
        "iv": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
        "current_hp": 20,
    }
    ump, services, calls = _load_update_main_pokemon(record, name_lookup="pikachu")
    try:
        ump.update_main_pokemon()
        assert calls["by_id"] == 1  # id-based lookup used as fallback
    finally:
        services.reset()


def test_update_main_pokemon_repairs_none_hp_values():
    record = {
        "id": 25,
        "name": "pikachu",
        "level": 5,
        "ev": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
        "iv": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
        "hp": None,
        "current_hp": None,
        "max_hp": None,
    }
    ump, services, calls = _load_update_main_pokemon(record, name_lookup="pikachu")
    try:
        pokemon, is_empty = ump.update_main_pokemon()

        assert is_empty is False
        assert pokemon.hp == pokemon.max_hp
        assert pokemon.current_hp == pokemon.max_hp
        saved = calls["db"].save_main_pokemon.call_args.args[0]
        assert saved["hp"] == pokemon.max_hp
        assert saved["current_hp"] == pokemon.max_hp
    finally:
        services.reset()


def test_update_main_pokemon_preserves_zero_hp():
    record = {
        "id": 25,
        "name": "pikachu",
        "level": 5,
        "ev": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
        "iv": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
        "hp": 0,
        "current_hp": 0,
    }
    ump, services, _calls = _load_update_main_pokemon(record, name_lookup="pikachu")
    try:
        pokemon, _is_empty = ump.update_main_pokemon()
        assert pokemon.hp == 0
        assert pokemon.current_hp == 0
    finally:
        services.reset()


def test_legacy_json_hp_is_normalized_before_database_save(tmp_path):
    record = {
        "id": 25,
        "name": "pikachu",
        "level": 5,
        "ev": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
        "iv": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
        "stats": {"hp": 35},
        "hp": None,
        "current_hp": None,
    }
    ump, services, calls = _load_update_main_pokemon(
        {}, name_lookup="pikachu", migrated=False
    )
    legacy_path = tmp_path / "mainpokemon.json"
    legacy_path.write_text(json.dumps([record]), encoding="utf-8")
    ump.mainpokemon_path = legacy_path

    try:
        pokemon, is_empty = ump.update_main_pokemon()

        assert is_empty is False
        assert pokemon.hp == pokemon.max_hp
        assert pokemon.current_hp == pokemon.max_hp
        saved = calls["db"].save_main_pokemon.call_args.args[0]
        assert saved["hp"] == pokemon.max_hp
        assert saved["current_hp"] == pokemon.max_hp
    finally:
        services.reset()
