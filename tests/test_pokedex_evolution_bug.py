"""F17 — pokedex evolution + PC-box caught-state regression tests.

Two independent concerns are validated against the *real* code and data:

1. **Caught-state persistence across manual PC-box evolutions**
   (Bulbasaur -> Ivysaur -> Venusaur). This exercises the genuine ``AnkimonDB``
   SQLite layer (``save_pokemon`` / ``get_pokemon`` / ``execute`` and the
   ``user_data`` primitives) so that a pre-evolution's caught state survives an
   in-place evolution that overwrites the captured-pokemon row.

   Note (re-fit): the persistent pokedex-caught/seen accessors
   (``mark_as_caught`` / ``get_caught_ids`` / ``get_seen_ids``) and the
   auto-register hook inside ``save_pokemon`` are owned by the Ankidex leaf
   (inventory row **F16**) and are **not yet on the integration tip**. The
   helpers below therefore *prefer the real ``AnkimonDB`` accessors when present*
   and otherwise shim them over the real ``get_user_data`` / ``set_user_data``
   primitives — so the persistence *semantics* are validated against the real DB
   either way, and this test starts using the production accessors automatically
   once F16 lands. Because main's ``save_pokemon`` has no auto-register hook yet,
   the test explicitly registers each saved stage (simulating the evolution/catch
   flow that the app performs).

2. **NR-21 regression**: ``get_growth_rate`` must never raise on 10xxx
   alternate-form ids (megas/regionals) and should resolve such ids to their base
   species' growth rate. It is loaded against the *real* ``pokedex.json`` and
   ``poke_species.csv``.
"""

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from conftest import isolated_modules

_SRC = Path(__file__).parent.parent / "src"


# --------------------------------------------------------------------------- #
# Loader 1: the real AnkimonDB (SQLite) layer.
# --------------------------------------------------------------------------- #
def _load_ankimon_db():
    """Dynamically load the real ``database_manager`` against stubbed deps.

    Only ``aqt``/``anki`` and ``Ankimon.resources`` are stubbed; the ``Ankimon``
    / ``Ankimon.pyobj`` package stubs installed by ``conftest.py`` (real
    ``__path__``) are left intact so the second loader can still import
    ``Ankimon.services`` / ``Ankimon.pyobj.pokemon_obj`` for real.
    """
    with isolated_modules(
        "aqt",
        "anki",
        extra=("Ankimon.resources", "Ankimon.pyobj.database_manager"),
    ):
        for name in [
            "aqt",
            "aqt.qt",
            "aqt.utils",
            "aqt.gui_hooks",
            "aqt.operations",
            "aqt.reviewer",
            "aqt.webview",
            "aqt.main",
            "anki",
            "anki.hooks",
            "anki.collection",
            "anki.models",
            "anki.notes",
            "anki.template",
            "anki.buildinfo",
        ]:
            sys.modules[name] = MagicMock()

        class MockResources(types.ModuleType):
            user_path = Path("/tmp")

            def __getattr__(self, name):
                return Path("/tmp") / name

        sys.modules["Ankimon.resources"] = MockResources("Ankimon.resources")

        spec = importlib.util.spec_from_file_location(
            "Ankimon.pyobj.database_manager",
            _SRC / "Ankimon" / "pyobj" / "database_manager.py",
        )
        db_mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = db_mod
        spec.loader.exec_module(db_mod)
    return db_mod


_DB_MOD = _load_ankimon_db()
AnkimonDB = _DB_MOD.AnkimonDB


class _MockLogger:
    def log(self, level, msg):
        pass

    def log_and_showinfo(self, level, msg):
        pass

    def _log(self, level, msg):
        pass


@pytest.fixture
def temp_db(tmp_path):
    """A real AnkimonDB backed by a fresh temporary SQLite file."""
    with patch.object(_DB_MOD, "user_path", tmp_path):
        db = AnkimonDB(_MockLogger())
        yield db


# --- caught/seen helpers: prefer the real (F16) accessors, else shim ------- #
def _mark_caught(db, pokedex_id):
    """Register ``pokedex_id`` as caught (and seen), via the real accessor when
    it exists, otherwise over the real ``user_data`` primitives."""
    pokedex_id = int(pokedex_id)
    real = getattr(db, "mark_as_caught", None)
    if callable(real):
        real(pokedex_id)
        return
    caught = set(db.get_user_data("pokedex_caught", []) or [])
    seen = set(db.get_user_data("pokedex_seen", []) or [])
    caught.add(pokedex_id)
    seen.add(pokedex_id)
    db.set_user_data("pokedex_caught", list(caught))
    db.set_user_data("pokedex_seen", list(seen))


def _caught_ids(db):
    real = getattr(db, "get_caught_ids", None)
    if callable(real):
        return set(real())
    data = db.get_user_data("pokedex_caught", [])
    return set(data) if isinstance(data, list) else set()


def _seen_ids(db):
    real = getattr(db, "get_seen_ids", None)
    if callable(real):
        return set(real())
    data = db.get_user_data("pokedex_seen", [])
    return set(data) if isinstance(data, list) else set()


def _pokedex_caught_ids(db):
    """Mirror the Ankidex ``get_ankidex_data`` union: currently-owned pokemon
    (``captured_pokemon``) + released history + the persistent caught set."""
    cursor = db.execute(
        "SELECT pokedex_id FROM captured_pokemon WHERE pokedex_id IS NOT NULL"
    )
    caught = {row[0] for row in cursor.fetchall()}

    cursor = db.execute(
        "SELECT DISTINCT json_extract(data, '$.id') FROM pokemon_history"
    )
    for row in cursor.fetchall():
        if row[0]:
            caught.add(int(row[0]))

    caught.update(_caught_ids(db))
    return caught


def test_pokedex_evolution_caught_state_persists(temp_db):
    """Bulbasaur -> Ivysaur -> Venusaur in the PC box keeps all three caught."""
    db = temp_db

    bulbasaur_data = {
        "individual_id": "bulba-uuid",
        "id": 1,
        "name": "Bulbasaur",
        "level": 90,
        "xp": 0,
        "shiny": False,
        "attacks": ["Tackle"],
        "base_stats": {"hp": 45, "atk": 49, "def": 49, "spa": 65, "spd": 65, "spe": 45},
        "stats": {"hp": 45, "atk": 49, "def": 49, "spa": 65, "spd": 65, "spe": 45},
        "ev": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
        "iv": {"hp": 15, "atk": 15, "def": 15, "spa": 15, "spd": 15, "spe": 15},
        "ability": "Overgrow",
        "growth_rate": "medium",
        "base_experience": 64,
        "gender": "M",
    }

    db.save_pokemon(bulbasaur_data)
    _mark_caught(db, 1)  # catch registration (real save-hook lands with F16)

    # Bulbasaur is the only caught id.
    assert 1 in _pokedex_caught_ids(db)
    assert len(_pokedex_caught_ids(db)) == 1

    # Manual evolution in the PC box: Bulbasaur (1) -> Ivysaur (2).
    pokemon = db.get_pokemon("bulba-uuid")
    assert pokemon is not None
    assert pokemon["id"] == 1

    _mark_caught(db, 1)  # pre-evolution caught registration (the actual fix)

    pokemon["name"] = "Ivysaur"
    pokemon["id"] = 2
    pokemon["base_stats"] = {
        "hp": 60,
        "atk": 62,
        "def": 63,
        "spa": 80,
        "spd": 80,
        "spe": 60,
    }
    pokemon["stats"] = pokemon["base_stats"]
    db.save_pokemon(pokemon)
    _mark_caught(db, 2)

    caught_after_evo1 = _pokedex_caught_ids(db)
    assert 1 in caught_after_evo1  # pre-evolution preserved
    assert 2 in caught_after_evo1
    assert len(caught_after_evo1) == 2

    # Second manual evolution: Ivysaur (2) -> Venusaur (3).
    pokemon = db.get_pokemon("bulba-uuid")
    assert pokemon is not None
    assert pokemon["id"] == 2

    _mark_caught(db, 2)

    pokemon["name"] = "Venusaur"
    pokemon["id"] = 3
    pokemon["base_stats"] = {
        "hp": 80,
        "atk": 82,
        "def": 83,
        "spa": 100,
        "spd": 100,
        "spe": 80,
    }
    pokemon["stats"] = pokemon["base_stats"]
    db.save_pokemon(pokemon)
    _mark_caught(db, 3)

    final_caught = _pokedex_caught_ids(db)
    assert 1 in final_caught
    assert 2 in final_caught
    assert 3 in final_caught
    assert len(final_caught) == 3

    seen_ids = _seen_ids(db)
    assert 1 in seen_ids
    assert 2 in seen_ids
    assert 3 in seen_ids


# --------------------------------------------------------------------------- #
# Loader 2: the real pokedex_functions module (for the NR-21 growth-rate fix).
# --------------------------------------------------------------------------- #
def _load_pokedex_functions():
    """Load ``pokedex_functions`` against the real resources + data files."""
    with isolated_modules(
        "aqt",
        extra=(
            "Ankimon.resources",
            "Ankimon.pyobj.error_handler",
            "Ankimon.functions.pokedex_functions",
        ),
    ):
        sys.modules["aqt"] = MagicMock()
        sys.modules["aqt.qt"] = MagicMock()
        sys.modules["aqt.utils"] = MagicMock()
        sys.modules["Ankimon.pyobj.error_handler"] = MagicMock()

        res_spec = importlib.util.spec_from_file_location(
            "Ankimon.resources", _SRC / "Ankimon" / "resources.py"
        )
        resources = importlib.util.module_from_spec(res_spec)
        sys.modules["Ankimon.resources"] = resources
        res_spec.loader.exec_module(resources)

        pf_spec = importlib.util.spec_from_file_location(
            "Ankimon.functions.pokedex_functions",
            _SRC / "Ankimon" / "functions" / "pokedex_functions.py",
        )
        pf = importlib.util.module_from_spec(pf_spec)
        sys.modules["Ankimon.functions.pokedex_functions"] = pf
        pf_spec.loader.exec_module(pf)
    return pf


_PF = _load_pokedex_functions()


@pytest.fixture(autouse=True)
def _fresh_pokedex_caches():
    """Reset the in-memory pokedex caches so form enrichment is deterministic."""
    _PF.clear_pokedex_caches()
    yield


def test_get_growth_rate_base_species_ok():
    """A plain base species returns the CSV-derived growth rate (not the fallback)."""
    cache = _PF._load_poke_species_cache()
    row = cache.get(1)
    assert row is not None
    expected = _PF.GROWTH_RATES[int(row["growth_rate_id"])]
    assert _PF.get_growth_rate(1) == expected


def test_get_growth_rate_form_id_does_not_raise():
    """NR-21: 10xxx alternate-form ids must not raise (fuzz-found crash)."""
    # Darkrai-Mega actual_id 10312 -> resolves to Darkrai (species 491).
    for form_id in (10312, 10191, 10099):
        result = _PF.get_growth_rate(form_id)
        assert isinstance(result, str)
        assert result in _PF.GROWTH_RATES.values()


def test_get_growth_rate_form_resolves_to_base_species():
    """A form id resolves to exactly its base species' growth rate."""
    pokedex = _PF._load_pokedex_cache()
    darkraimega = pokedex.get("darkraimega", {})
    base_species_id = darkraimega.get("species_id")
    assert base_species_id, "expected darkraimega form data present"
    assert _PF.get_growth_rate(10312) == _PF.get_growth_rate(base_species_id)


def test_get_growth_rate_unknown_and_nonnumeric_fall_back_to_medium():
    """Genuinely unknown / non-numeric ids fall back to 'medium', never raise."""
    assert _PF.get_growth_rate(9_999_999) == "medium"
    assert _PF.get_growth_rate("not-a-number") == "medium"
    assert _PF.get_growth_rate(None) == "medium"


# --------------------------------------------------------------------------- #
# Robustness regressions for the Gemini review (PR #554) on pokedex_functions.
# --------------------------------------------------------------------------- #
class _SettingsStub:
    """Minimal settings seam whose ``get`` always returns a fixed value."""

    def __init__(self, value):
        self._value = value

    def get(self, key, default=None):
        return self._value


def _install_synthetic_pokedex(monkeypatch, cache):
    """Point the module caches at a controlled synthetic pokedex and rebuild
    the id index from it (deterministic; no enrichment)."""
    _PF.clear_pokedex_caches()
    monkeypatch.setattr(_PF, "_pokedex_cache", cache, raising=False)
    monkeypatch.setattr(_PF, "_pokedex_id_index", None, raising=False)


def test_get_active_region_non_string_is_none(monkeypatch):
    """A non-string settings scalar (int/None/list) must normalize to None
    without raising ``AttributeError`` on ``.strip()`` (Gemini :752)."""
    monkeypatch.setattr(_PF.services, "settings", _SettingsStub(5), raising=False)
    assert _PF._get_active_region() is None

    monkeypatch.setattr(
        _PF.services, "settings", _SettingsStub(["Kanto"]), raising=False
    )
    assert _PF._get_active_region() is None

    monkeypatch.setattr(_PF.services, "settings", None, raising=False)
    assert _PF._get_active_region() is None


def test_get_active_region_string_values(monkeypatch):
    """Sentinels normalize to None; a real region is trimmed and returned."""
    monkeypatch.setattr(
        _PF.services, "settings", _SettingsStub("No Region"), raising=False
    )
    assert _PF._get_active_region() is None

    monkeypatch.setattr(_PF.services, "settings", _SettingsStub("   "), raising=False)
    assert _PF._get_active_region() is None

    monkeypatch.setattr(
        _PF.services, "settings", _SettingsStub("  Alola  "), raising=False
    )
    assert _PF._get_active_region() == "Alola"


def test_return_identifier_for_item_id_real_and_none():
    """King's Rock (id 198) resolves to its apostrophe-free items.csv identifier,
    and a None/invalid id never matches a malformed row (Gemini :765)."""
    assert _PF.return_identifier_for_item_id(198) == "kings-rock"
    assert _PF.return_identifier_for_item_id(None) is None
    assert _PF.return_identifier_for_item_id("not-a-number") is None


def test_check_evolution_by_item_apostrophe_item(monkeypatch):
    """A useItem evolution whose ``evoItem`` carries an apostrophe (e.g.
    "King's Rock") matches the apostrophe-free items.csv identifier
    ("kings-rock") after normalization (Gemini :819)."""
    monkeypatch.setattr(_PF.services, "settings", None, raising=False)
    _install_synthetic_pokedex(
        monkeypatch,
        {
            "apostrophemon": {
                "species_id": 90001,
                "actual_id": 90001,
                "evos": ["ApostropheKing"],
            },
            "apostropheking": {
                "species_id": 90002,
                "actual_id": 90002,
                "evoType": "useItem",
                "evoItem": "King's Rock",
            },
        },
    )
    # item id 198 == "King's Rock", identifier "kings-rock" in the real items.csv.
    assert _PF.check_evolution_by_item(90001, 198) == 90002
    # A wrong item id must not trigger the evolution.
    assert _PF.check_evolution_by_item(90001, 84) is None


def test_load_pokedex_id_index_base_owns_species_id(monkeypatch):
    """A base form always owns its species_id (never shadowed by a mega), and
    rows with missing ids never collapse onto key 0 (Gemini :144)."""
    _install_synthetic_pokedex(
        monkeypatch,
        {
            # Mega precedes the base in iteration order.
            "basemega": {
                "species_id": 700,
                "actual_id": 10700,
                "baseSpecies": "Basemon",
            },
            "basemon": {"species_id": 700, "actual_id": 700},
            "noid": {"species_id": None, "actual_id": None},
        },
    )
    idx = _PF._load_pokedex_id_index()
    assert idx.get(700) == "basemon"
    assert idx.get(10700) == "basemega"
    assert 0 not in idx


def test_move_based_evolution_none_move_safe(monkeypatch):
    """A ``levelMove`` evolution must not crash when the moveset contains
    non-string entries (None/int), and a matching move still triggers it
    (Gemini :1059)."""
    monkeypatch.setattr(_PF.services, "settings", None, raising=False)
    _install_synthetic_pokedex(
        monkeypatch,
        {
            "movemon": {
                "species_id": 90101,
                "actual_id": 90101,
                "evos": ["MoveKing"],
            },
            # No evoLevel -> the move requirement genuinely gates (mirrors the
            # real Mr. Mime / Tangrowth levelMove rows).
            "moveking": {
                "species_id": 90102,
                "actual_id": 90102,
                "evoType": "levelMove",
                "evoMove": "Psyshield Bash",
            },
        },
    )

    class _EvoWin:
        def __init__(self):
            self.called = None

        def ask_pokemon_evo(self, individual_id, pokemon_id, evo_id):
            self.called = (individual_id, pokemon_id, evo_id)

    class _DB:
        def __init__(self, attacks):
            self._attacks = attacks

        def get_pokemon(self, individual_id):
            return {"attacks": self._attacks}

    # Matching move present alongside a None + int -> evolves, no crash.
    monkeypatch.setattr(
        _PF.services, "db", _DB([None, "Psyshield Bash", 123]), raising=False
    )
    win = _EvoWin()
    assert _PF.check_evolution_for_pokemon("iid-1", 90101, 20, win) == 90102
    assert win.called == ("iid-1", 90101, 90102)

    # Only non-matching entries (incl. None) -> no evolution, still no crash.
    monkeypatch.setattr(_PF.services, "db", _DB([None, "Tackle", 7]), raising=False)
    win2 = _EvoWin()
    assert _PF.check_evolution_for_pokemon("iid-2", 90101, 20, win2) is None
    assert win2.called is None


def test_check_evolution_by_item_trade_species_real_data(monkeypatch):
    """Trade-with-held-item species (``evoType == "trade"``) are evolvable by
    applying the held item directly — Ankimon has no trading. Exercised against
    the REAL pokedex.json + items.csv (not a synthetic ``useItem`` fixture), so
    a regression that only accepts ``useItem`` fails here."""
    monkeypatch.setattr(_PF.services, "settings", None, raising=False)  # No Region
    # (pre_evo_id, item_id) -> evolved_id, all from the real bundled data:
    #   Onix 95      + Metal Coat 210   -> Steelix 208
    #   Scyther 123  + Metal Coat 210   -> Scizor 212
    #   Seadra 117   + Dragon Scale 212 -> Kingdra 230
    #   Poliwhirl 61 + King's Rock 198  -> Politoed 186
    #   Slowpoke 79  + King's Rock 198  -> Slowking 199
    assert _PF.check_evolution_by_item(95, 210) == 208
    assert _PF.check_evolution_by_item(123, 210) == 212
    assert _PF.check_evolution_by_item(117, 212) == 230
    assert _PF.check_evolution_by_item(61, 198) == 186
    assert _PF.check_evolution_by_item(79, 198) == 199
    # Wrong item must not trigger: Onix needs Metal Coat, not King's Rock.
    assert _PF.check_evolution_by_item(95, 198) is None


def test_move_based_evolution_fails_closed_when_data_unavailable(monkeypatch):
    """A ``levelMove`` evolution must NOT fire when the moveset can't be
    confirmed (DB record lacks ``attacks`` or ``get_pokemon`` raises) — it fails
    closed, mirroring friendship_evolution.py, instead of the old fail-open
    default that could evolve on unconfirmed data (Gemini :1087)."""
    monkeypatch.setattr(_PF.services, "settings", None, raising=False)
    _install_synthetic_pokedex(
        monkeypatch,
        {
            "movemon": {
                "species_id": 90301,
                "actual_id": 90301,
                "evos": ["MoveKing"],
            },
            "moveking": {
                "species_id": 90302,
                "actual_id": 90302,
                "evoType": "levelMove",
                "evoMove": "Psyshield Bash",
            },
        },
    )

    class _EvoWin:
        def __init__(self):
            self.called = None

        def ask_pokemon_evo(self, individual_id, pokemon_id, evo_id):
            self.called = (individual_id, pokemon_id, evo_id)

    # DB record without an "attacks" key -> move unconfirmed -> no evolution.
    class _DBNoAttacks:
        def get_pokemon(self, individual_id):
            return {"level": 20}

    monkeypatch.setattr(_PF.services, "db", _DBNoAttacks(), raising=False)
    win = _EvoWin()
    assert _PF.check_evolution_for_pokemon("iid", 90301, 20, win) is None
    assert win.called is None

    # DB unavailable (raises) -> also fails closed, no crash.
    class _DBRaises:
        def get_pokemon(self, individual_id):
            raise RuntimeError("db down")

    monkeypatch.setattr(_PF.services, "db", _DBRaises(), raising=False)
    win2 = _EvoWin()
    assert _PF.check_evolution_for_pokemon("iid", 90301, 20, win2) is None
    assert win2.called is None


def test_check_evolution_for_pokemon_none_window_skips_cleanly(monkeypatch):
    """A ``None`` evo_window (F31 lazy singletons can leave services.evo_window
    None) must skip the offer and return None WITHOUT reaching
    ``evo_window.ask_pokemon_evo(...)`` — so the caller's defeat-persistence path
    is never aborted by an AttributeError (the old broad-except only turned that
    crash into a scary warning dialog)."""
    monkeypatch.setattr(_PF.services, "settings", None, raising=False)
    _install_synthetic_pokedex(
        monkeypatch,
        {
            "levelmon": {
                "species_id": 90401,
                "actual_id": 90401,
                "evos": ["LevelKing"],
            },
            "levelking": {
                "species_id": 90402,
                "actual_id": 90402,
                "evoLevel": 16,
            },
        },
    )
    _PF.show_warning_with_traceback.reset_mock()
    # Level 20 >= evoLevel 16 would normally prompt; a None window degrades to
    # "no offer" without crashing or warning.
    assert _PF.check_evolution_for_pokemon("iid", 90401, 20, None) is None
    _PF.show_warning_with_traceback.assert_not_called()
