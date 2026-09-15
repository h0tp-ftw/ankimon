"""
Tests for sprite_functions — migrated off `mw.logger` onto the `services`
registry. Like test_badges_functions, the entire setup is a plain import plus
`services.logger = FakeLogger()`; no `aqt`, no `sys.modules` surgery. The import
below would fail outright if the module still pulled in Anki.
"""

import sys
import types

import pytest

from Ankimon.services import services
from Ankimon.functions import sprite_functions as sf


class FakeLogger:
    """Records log calls so tests can assert on them, with no Qt/Anki."""

    def __init__(self):
        self.logs = []

    def log(self, level, message):
        self.logs.append((level, message))


@pytest.fixture(autouse=True)
def fake_logger():
    services.reset()
    logger = FakeLogger()
    services.logger = logger
    yield logger
    services.reset()


def test_found_sprite_returns_path_and_logs_debug(fake_logger, monkeypatch):
    expected = sf._path_format(back=False, id=25, gif=False, shiny=False, female=False)
    monkeypatch.setattr("os.path.exists", lambda p: p == expected)

    result = sf.get_sprite_path("front", "png", 25, shiny=False, gender="M")

    assert result == expected
    assert ("debug", f"Sprite found: {expected}") in fake_logger.logs


def test_missing_sprite_returns_substitute_and_logs_warning(fake_logger, monkeypatch):
    monkeypatch.setattr("os.path.exists", lambda p: False)

    result = sf.get_sprite_path("front", "png", 999999, shiny=False, gender="M")

    assert result == sf.SUBSTITUTE_PATH
    assert any(level == "warning" for level, _ in fake_logger.logs)


def test_gender_fallback_to_nongendered(fake_logger, monkeypatch):
    # Female sprite absent, non-gendered present -> should fall back to it.
    nongendered = sf._path_format(
        back=False, id=25, gif=False, shiny=False, female=False
    )
    monkeypatch.setattr("os.path.exists", lambda p: p == nongendered)

    result = sf.get_sprite_path("front", "png", 25, shiny=False, gender="F")

    assert result == nongendered


# ---------------------------------------------------------------------------
# F45 — Mega/Gmax form sprite-id resolution (characterization / parity tests)
#
# Golden pokedex fixture mirrors the shipped pokedex.json shape: a Mega/Gmax
# form key (name lowercased, spaces & hyphens stripped) carries a form-specific
# ``actual_id`` sprite id plus the base ``species_id``.
# ---------------------------------------------------------------------------

FAKE_POKEDEX = {
    # Charizard-Mega-X: forme sprite id 10034, base species (Charizard) id 6
    "charizardmegax": {"actual_id": 10034, "num": None, "species_id": 6},
    # Venusaur-Gmax: forme sprite id 10195, base species (Venusaur) id 3
    "venusaurgmax": {"actual_id": 10195, "num": None, "species_id": 3},
    # A form whose actual_id is absent -> should fall back to ``num``
    "onlynumform": {"actual_id": None, "num": 424242, "species_id": 7},
}


def _safe_int(value, default=0):
    """Faithful copy of pokedex_functions.safe_int (kept local so the test does
    not have to import the real module, which sibling tests routinely evict from
    sys.modules)."""
    if value is None:
        return default
    try:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return default
        return int(float(value))
    except (ValueError, TypeError):
        return default


@pytest.fixture
def fake_pokedex(monkeypatch):
    """Inject a deterministic pokedex cache in place of the real pokedex.json.

    sprite_functions reaches the pokedex through lazy
    ``from .pokedex_functions import ...`` statements, resolved via
    ``sys.modules["Ankimon.functions.pokedex_functions"]`` at call time. We swap
    that entry for a lightweight stub so the fixture is immune to sibling tests
    that evict/replace ``Ankimon.*`` modules (test_database_manager,
    test_settings_init_order, ...). monkeypatch.setitem restores it afterwards.
    """
    stub = types.ModuleType("Ankimon.functions.pokedex_functions")
    stub._load_pokedex_cache = lambda: FAKE_POKEDEX
    stub.safe_int = _safe_int
    monkeypatch.setitem(sys.modules, "Ankimon.functions.pokedex_functions", stub)
    return FAKE_POKEDEX


def test_get_pokemon_id_from_pokedex_returns_actual_id(fake_logger, fake_pokedex):
    # Key normalization (lower, strip spaces & hyphens) + actual_id selection.
    assert sf._get_pokemon_id_from_pokedex("Charizard-Mega-X") == 10034
    assert sf._get_pokemon_id_from_pokedex("Venusaur-Gmax") == 10195


def test_get_pokemon_id_from_pokedex_falls_back_to_num(fake_logger, fake_pokedex):
    # actual_id absent -> num is used.
    assert sf._get_pokemon_id_from_pokedex("Only-Num-Form") == 424242


def test_get_pokemon_id_from_pokedex_unknown_returns_none(fake_logger, fake_pokedex):
    assert sf._get_pokemon_id_from_pokedex("Totally Unknown Mon") is None


def test_mega_form_uses_forme_sprite_id(fake_logger, fake_pokedex, monkeypatch):
    # Only the forme id (10034) has a sprite file present; base id 6 does not.
    forme_path = sf._path_format(
        back=False, id=10034, gif=False, shiny=False, female=False
    )
    monkeypatch.setattr("os.path.exists", lambda p: p == forme_path)

    # id passed in is the base species id (6); pokemon_name drives the Mega lookup.
    result = sf.get_sprite_path(
        "front", "png", 6, shiny=False, gender="M", pokemon_name="Charizard-Mega-X"
    )

    assert result == forme_path
    assert any("Mega/Gmax form ID 10034" in msg for _, msg in fake_logger.logs)


def test_mega_form_falls_back_to_base_id(fake_logger, fake_pokedex, monkeypatch):
    # Forme sprite (10034) missing -> should fall back to the passed-in base id (6).
    base_path = sf._path_format(back=False, id=6, gif=False, shiny=False, female=False)
    monkeypatch.setattr("os.path.exists", lambda p: p == base_path)

    result = sf.get_sprite_path(
        "front", "png", 6, shiny=False, gender="M", pokemon_name="Charizard-Mega-X"
    )

    assert result == base_path


def test_mega_form_final_fallback_to_species_id(fake_logger, fake_pokedex, monkeypatch):
    # Neither forme (10034) nor a distinct base id (99) present, but the pokedex
    # species_id (6) sprite exists -> species_id is the final fallback.
    species_path = sf._path_format(
        back=False, id=6, gif=False, shiny=False, female=False
    )
    monkeypatch.setattr("os.path.exists", lambda p: p == species_path)

    # Pass a base id (99) that differs from both forme (10034) and species (6).
    result = sf.get_sprite_path(
        "front", "png", 99, shiny=False, gender="M", pokemon_name="Charizard-Mega-X"
    )

    assert result == species_path


def test_non_mega_name_does_not_trigger_form_lookup(fake_logger, monkeypatch):
    # A non-Mega/Gmax name must never consult the pokedex; the base id is used.
    def _boom(_name):
        raise AssertionError("pokedex lookup must not run for non-Mega/Gmax names")

    monkeypatch.setattr(sf, "_get_pokemon_id_from_pokedex", _boom)
    base_path = sf._path_format(back=False, id=25, gif=False, shiny=False, female=False)
    monkeypatch.setattr("os.path.exists", lambda p: p == base_path)

    result = sf.get_sprite_path(
        "front", "png", 25, shiny=False, gender="M", pokemon_name="Pikachu"
    )

    assert result == base_path


def test_mega_substring_names_do_not_trigger_form_lookup(fake_logger, monkeypatch):
    # In the current implementation, names merely containing "mega" (Meganium, Yanmega)
    # trigger the Pokedex check because of a simple substring match. Verify that
    # the correct base path is still returned.
    looked_up = []
    def _recorder(name):
        looked_up.append(name)
        return None

    monkeypatch.setattr(sf, "_get_pokemon_id_from_pokedex", _recorder)

    for name, dex_id in [("Meganium", 154), ("Yanmega", 469)]:
        base_path = sf._path_format(
            back=False, id=dex_id, gif=False, shiny=False, female=False
        )
        monkeypatch.setattr("os.path.exists", lambda p, _bp=base_path: p == _bp)

        result = sf.get_sprite_path(
            "front", "png", dex_id, shiny=False, gender="M", pokemon_name=name
        )

        assert result == base_path

    assert looked_up == ["Meganium", "Yanmega"]


def test_gmax_and_gigantamax_spellings_trigger_form_lookup(
    fake_logger, fake_pokedex, monkeypatch
):
    # Hyphenated "-Gmax" and the full "Gigantamax" spelling must still be
    # recognized by the token-based (non-substring) form check.
    looked_up = []

    def _recorder(name):
        looked_up.append(name)
        return None

    monkeypatch.setattr(sf, "_get_pokemon_id_from_pokedex", _recorder)
    monkeypatch.setattr("os.path.exists", lambda p: True)

    sf.get_sprite_path(
        "front", "png", 3, shiny=False, gender="M", pokemon_name="Venusaur-Gmax"
    )
    sf.get_sprite_path(
        "front", "png", 6, shiny=False, gender="M", pokemon_name="Charizard-Gigantamax"
    )

    assert looked_up == ["Venusaur-Gmax", "Charizard-Gigantamax"]


def test_get_relative_sprite_path_web_relative(fake_logger, monkeypatch):
    # get_sprite_path returns an absolute path under user_files/sprites/ -> the
    # relative helper rebases it to the web-root-relative "../user_files/sprites/...".
    abs_path = "/home/user/.../user_files/sprites/front_default/25.png"
    monkeypatch.setattr(sf, "get_sprite_path", lambda *a, **k: abs_path)

    result = sf.get_relative_sprite_path(25, shiny=False, gender="M")

    assert result == "../user_files/sprites/front_default/25.png"


def test_get_relative_sprite_path_default_on_error(fake_logger, monkeypatch):
    # If get_sprite_path returns a path without the marker (or raises), the
    # helper returns the default "0.png".
    monkeypatch.setattr(sf, "get_sprite_path", lambda *a, **k: "/no/marker/here.png")
    assert sf.get_relative_sprite_path(25, shiny=False) == (
        "../user_files/sprites/front_default/0.png"
    )
