"""Profile Pokédex-completion count (F18).

Dynamically loads the real ``AnkimonDB`` + ``ProfileData`` (+ the real
``pokedex_functions`` for its dedup helpers) against lightweight stand-ins for
the aqt/anki and sibling Ankimon modules, so the pure data logic can be exercised
without an Anki runtime.

Hermetic: every ``sys.modules`` entry is installed via ``monkeypatch.setitem``
so it is restored at teardown — the module-level stand-ins do NOT leak into the
rest of the Tier-1 suite. ProfileData reads state through the service seam
(``services.db``), not ``aqt.mw``, so the test injects the real temp DB there.
"""

import sys
import types
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_src = Path(__file__).parent.parent / "src"


class MockLogger:
    def log(self, level, msg):
        pass

    def log_and_showinfo(self, level, msg):
        pass

    def _log(self, level, msg):
        pass


def _load_module(name, filepath, monkeypatch):
    """Load a real source file under ``name``, registered (and later restored)
    through monkeypatch so it never leaks into the wider test session."""
    spec = importlib.util.spec_from_file_location(name, filepath)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, mod)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def profile_env(tmp_path, monkeypatch):
    """Hermetic loader for AnkimonDB + ProfileData + pokedex_functions."""

    # --- aqt / anki stand-ins (never actually exercised here) ---
    for name in [
        "aqt", "aqt.qt", "aqt.utils", "aqt.gui_hooks", "aqt.operations",
        "aqt.reviewer", "aqt.webview", "aqt.main",
        "anki", "anki.hooks", "anki.collection", "anki.models",
        "anki.notes", "anki.template", "anki.buildinfo",
    ]:
        monkeypatch.setitem(sys.modules, name, MagicMock())

    class MockResources:
        user_path = tmp_path

        def __getattr__(self, name):
            return tmp_path / name

    # --- Ankimon parent-package stand-ins so relative imports resolve without
    #     pulling the aqt-coupled addon composition root. ---
    monkeypatch.setitem(sys.modules, "Ankimon", types.ModuleType("Ankimon"))
    monkeypatch.setitem(sys.modules, "Ankimon.resources", MockResources())
    monkeypatch.setitem(sys.modules, "Ankimon.singletons", MagicMock())
    monkeypatch.setitem(sys.modules, "Ankimon.utils", MagicMock())
    monkeypatch.setitem(sys.modules, "Ankimon.pyobj", MagicMock())
    monkeypatch.setitem(sys.modules, "Ankimon.functions", types.ModuleType("Ankimon.functions"))
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.ankimon_profile_web",
        types.ModuleType("Ankimon.ankimon_profile_web"),
    )

    # Service seam stand-in: the test injects the real temp DB onto services.db.
    services_mod = types.ModuleType("Ankimon.services")
    services_mod.services = types.SimpleNamespace(db=None, main_pokemon=None, settings=None)
    monkeypatch.setitem(sys.modules, "Ankimon.services", services_mod)

    # pokedex_functions imports these sibling modules at top level; the test
    # path never calls into them, so lightweight mocks satisfy the imports.
    monkeypatch.setitem(sys.modules, "Ankimon.pyobj.error_handler", MagicMock())
    monkeypatch.setitem(sys.modules, "Ankimon.pyobj.pokemon_obj", MagicMock())
    monkeypatch.setitem(sys.modules, "Ankimon.functions.learnset_retrieval", MagicMock())

    # --- load the real modules under test against the stand-ins ---
    db_mod = _load_module(
        "Ankimon.pyobj.database_manager",
        _src / "Ankimon" / "pyobj" / "database_manager.py",
        monkeypatch,
    )
    monkeypatch.setattr(db_mod, "user_path", tmp_path, raising=False)

    pf = _load_module(
        "Ankimon.functions.pokedex_functions",
        _src / "Ankimon" / "functions" / "pokedex_functions.py",
        monkeypatch,
    )
    # profile_data imports the (aqt-free) team seam at module level.
    _load_module(
        "Ankimon.functions.team_functions",
        _src / "Ankimon" / "functions" / "team_functions.py",
        monkeypatch,
    )

    profile_mod = _load_module(
        "Ankimon.ankimon_profile_web.profile_data",
        _src / "Ankimon" / "ankimon_profile_web" / "profile_data.py",
        monkeypatch,
    )

    db = db_mod.AnkimonDB(MockLogger())

    return types.SimpleNamespace(
        db=db,
        ProfileData=profile_mod.ProfileData,
        pf=pf,
        services=services_mod.services,
    )


def test_profile_pokedex_completion(profile_env):
    """
    Verifies that the Profile Pokédex count (ProfileData._collection_stats,
    routed through the service seam ``services.db``):
    1. Deduplicates special forms (Megas, regional variants) to their base
       species_id, so a Mega and its base species count once.
    2. Counts currently-owned box Pokémon.

    NOTE: exp's version also folded in released-history and explicitly-marked
    caught IDs (the ``get_all_pokemon_ids`` history/caught merge +
    ``mark_as_caught``). That merge is a separate, later pokedex-completion DB
    leaf that is not part of this web-shell unit, so this test exercises the
    dedup path against currently-owned Pokémon only.
    """
    db = profile_env.db
    pf = profile_env.pf
    ProfileData = profile_env.ProfileData

    # Region-returning settings stub + inject the real DB onto the seam.
    settings_obj = MagicMock()
    settings_obj.get.return_value = "red"
    profile_env.services.db = db
    profile_env.services.settings = settings_obj

    # Miniature in-memory Pokédex caches (bypass the JSON file load).
    pf._pokedex_cache = {
        "charizard": {"species_id": 6, "actual_id": 3, "name": "Charizard"},
        "charizardmega": {"species_id": 6, "actual_id": 10091, "name": "Charizard-Mega-X"},
        "bulbasaur": {"species_id": 1, "actual_id": 1, "name": "Bulbasaur"},
        "vulpix": {"species_id": 37, "actual_id": 37, "name": "Vulpix"},
        "vulpixalola": {"species_id": 37, "actual_id": 10100, "name": "Vulpix-Alola"},
        "oddish": {"species_id": 43, "actual_id": 43, "name": "Oddish"},
    }
    pf._pokedex_id_index = {
        3: "charizard",
        10091: "charizardmega",
        1: "bulbasaur",
        37: "vulpix",
        10100: "vulpixalola",
        43: "oddish",
    }

    # Currently-owned box Pokémon spanning a base species, its Mega, and a
    # regional form of a different species. The Mega must dedupe onto the base
    # species, so the dex count treats 4 owned Pokémon as 3 species.
    db.save_pokemon({"individual_id": "c1", "id": 3, "name": "Charizard", "level": 50, "shiny": False})
    # Mega Charizard X (species_id 6) -> deduplicated onto base Charizard.
    db.save_pokemon({"individual_id": "c2", "id": 10091, "name": "Charizard-Mega-X", "level": 60, "shiny": False})
    db.save_pokemon({"individual_id": "b1", "id": 1, "name": "Bulbasaur", "level": 15, "shiny": False})
    # Alolan Vulpix (species_id 37) — regional form of Vulpix.
    db.save_pokemon({"individual_id": "v1", "id": 10100, "name": "Vulpix-Alola", "level": 20, "shiny": False})

    # Oddish (species_id 43) is in history (released/no longer owned).
    # This should be included in the unique Pokedex count (dex_seen), but not in the currently owned box count (caught).
    db.add_to_history({"individual_id": "odd1", "id": 43, "name": "Oddish", "level": 12, "shiny": False})

    trainer_card = MagicMock()
    trainer_card.highest_pokemon_level.return_value = 60
    profile = ProfileData(
        addon_dir=Path("/tmp"),
        trainer_card=trainer_card,
        settings_obj=settings_obj,
        logger=MagicMock(),
    )

    stats = profile._collection_stats()

    # Caught currently in box = 4 (2 Charizards + Bulbasaur + Alolan Vulpix).
    assert stats["caught"] == 4
    # Dex unique base species caught = 4 (species 6 + 1 + 37 + 43); Mega Charizard X
    # dedupes onto base Charizard (species 6), and Oddish from history is included.
    assert stats["dex_seen"] == 4
    assert stats["shinies"] == 0
    assert stats["highest_level"] == 60
