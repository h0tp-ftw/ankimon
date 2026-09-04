import json
import importlib
import sys

_orig_modules = {
    name: sys.modules.get(name)
    for name in [
        "Ankimon.resources",
        "Ankimon.pyobj.error_handler",
        "aqt",
        "aqt.utils",
        "aqt.qt",
    ]
}
from pathlib import Path
from unittest.mock import mock_open, patch, MagicMock

import pytest

# Mock aqt modules
mock_aqt = MagicMock()
sys.modules["aqt"] = mock_aqt
sys.modules["aqt.utils"] = mock_aqt.utils
sys.modules["aqt.qt"] = MagicMock()

# Mock error_handler and pyobj to avoid loading PyQt/Anki dependencies
sys.modules["Ankimon.pyobj.error_handler"] = MagicMock()

# Stub resources module with a fake learnset_path and fallback attributes
_src = Path(__file__).parent.parent / "src"
actual_pokedex_path = _src / "Ankimon" / "data_files" / "pokedex.json"


class MockResources:
    learnset_path = "/fake/learnsets.json"
    pokedex_path = str(actual_pokedex_path)

    def __getattr__(self, name):
        return "/fake/dummy"


sys.modules["Ankimon.resources"] = MockResources()

# Now load learnset_retrieval from its file
_src = Path(__file__).parent.parent / "src"
_spec = importlib.util.spec_from_file_location(
    "Ankimon.functions.learnset_retrieval",
    _src / "Ankimon" / "functions" / "learnset_retrieval.py",
)
_lr = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _lr
_spec.loader.exec_module(_lr)

from Ankimon.functions.learnset_retrieval import (
    _get_learnset_moves,
    get_all_pokemon_moves,
    get_levelup_move_for_pokemon,
    get_random_moves_for_pokemon,
)

# Restore original modules to prevent global mock contamination during test collection
for name, orig in _orig_modules.items():
    if orig is None:
        sys.modules.pop(name, None)
    else:
        sys.modules[name] = orig


FAKE_LEARNSET = {
    "slowpoke": {
        "learnset": {
            "tackle": ["9L1", "3L1"],
            "confusion": ["9L12", "3L17"],
            "psychic": ["9L33", "3L40"],
            "yawn": ["9L15"],
            "surf": ["9M"],  # TM, no level entry
        }
    },
    "eternatus": {
        "learnset": {
            "dynamaxcannon": ["9L56"],
        }
    },
    "necrozma": {
        "learnset": {
            "photongeyser": ["9L1"],
        }
    },
    "necrozmaultra": {
        "learnset": {
            "moongeistbeam": ["9R"],
            "sunsteelstrike": ["9R"],
        }
    },
    "deoxys": {
        "learnset": {
            "spikes": ["9L20"],
            "superpower": ["9L37"],
            "extremespeed": ["9L73"],
            "cosmicpower": ["9L35"],
            "recover": ["9L40"],
            "teleport": ["9L1"],
            "zapcannon": ["9L61"],
        }
    },
    # Event 'S' codes carry a distribution INDEX, not a level (mirrors the real
    # learnsets.json). Mewtwo "9S8" / Charizard "9S11" must NOT leak Psystrike /
    # Flare Blitz into ordinary low-level movesets — only the real "9L" applies.
    "mewtwo": {
        "learnset": {
            "confusion": ["9L1"],
            "psystrike": ["9L72", "9S8"],
        }
    },
    "charizard": {
        "learnset": {
            "ember": ["9L1"],
            "flareblitz": ["9L62", "9S11"],
        }
    },
}

_FAKE_JSON = json.dumps(FAKE_LEARNSET)

original_open = open


@pytest.fixture(autouse=True)
def _mock_learnset_file():
    res = sys.modules.get("Ankimon.resources")
    if res is not None:
        res.learnset_path = "/fake/learnsets.json"
        res.pokedex_path = str(actual_pokedex_path)

    # Clear pokedex functions cache if it was already loaded to avoid test pollution
    pokedex_funcs = sys.modules.get("Ankimon.functions.pokedex_functions")
    if pokedex_funcs is not None:
        pokedex_funcs.pokedex_path = str(actual_pokedex_path)
        try:
            pokedex_funcs.clear_pokedex_caches()
        except AttributeError:
            pass

    m = mock_open(read_data=_FAKE_JSON)

    def side_effect(file, *args, **kwargs):
        if "learnsets.json" in str(file) or "fake" in str(file):
            return m(file, *args, **kwargs)
        return original_open(file, *args, **kwargs)

    with patch("builtins.open", side_effect):
        yield


# ---------- _get_learnset_moves ----------


class TestGetLearnsetMoves:
    def test_cross_gen_bug_fix(self):
        """Gen 3's L17 must not block gen 9's L12."""
        moves = _get_learnset_moves("slowpoke", 12, 9)
        assert "confusion" in moves
        assert moves["confusion"] == 12

    def test_gen3_filtering(self):
        """Level 12 in gen 3 should NOT include confusion (gen 3 learns it at 17)."""
        moves = _get_learnset_moves("slowpoke", 12, 3)
        assert "confusion" not in moves

    def test_level_boundary(self):
        """Level 11 in gen 9 should NOT include confusion (learned at 12)."""
        moves = _get_learnset_moves("slowpoke", 11, 9)
        assert "confusion" not in moves

    def test_picks_highest_valid(self):
        """At level 33, both confusion (12) and psychic (33) should be present."""
        moves = _get_learnset_moves("slowpoke", 33, 9)
        assert moves["confusion"] == 12
        assert moves["psychic"] == 33

    def test_tm_entries_ignored(self):
        """TM entries like '9M' must not appear as level-up moves."""
        moves = _get_learnset_moves("slowpoke", 100, 9)
        assert "surf" not in moves

    def test_case_insensitive(self):
        """Uppercase name should be normalized."""
        moves = _get_learnset_moves("Slowpoke", 12, 9)
        assert "confusion" in moves

    def test_unknown_pokemon(self):
        """Unknown pokemon should return an empty dict."""
        assert _get_learnset_moves("missingno2", 50, 9) == {}


# ---------- public wrappers ----------


class TestGetAllPokemonMoves:
    def test_returns_list_of_moves(self):
        result = get_all_pokemon_moves("slowpoke", 15, 9)
        assert isinstance(result, list)
        assert set(result) == {"tackle", "confusion", "yawn"}


class TestGetRandomMoves:
    def test_cap_at_four(self):
        result = get_random_moves_for_pokemon("slowpoke", 100, 9)
        assert len(result) <= 4
        assert all(isinstance(m, str) for m in result)

    def test_fewer_than_four(self):
        result = get_random_moves_for_pokemon("slowpoke", 1, 9)
        assert result == ["tackle"]


class TestGetLevelupMove:
    def test_exact_level_match(self):
        result = get_levelup_move_for_pokemon("slowpoke", 12, 9)
        assert result == ["confusion"]

    def test_no_match_returns_empty_list(self):
        result = get_levelup_move_for_pokemon("slowpoke", 13, 9)
        assert result == []


class TestEventSCodesExcluded:
    """'S' method codes encode an event-distribution index, not a level, so they
    must never contribute a level-up move (regression for the S-code fix)."""

    def test_scode_not_leaked_below_real_level(self):
        # "9S8" must NOT be read as level 8 — Psystrike's only legit source here
        # is "9L72", so it is absent everywhere below level 72.
        assert "psystrike" not in _get_learnset_moves("mewtwo", 8, 9)
        assert "psystrike" not in _get_learnset_moves("mewtwo", 71, 9)

    def test_real_level_entry_still_applies(self):
        # The genuine "9L72" entry is unaffected by dropping the S branch.
        assert _get_learnset_moves("mewtwo", 72, 9).get("psystrike") == 72

    def test_charizard_flareblitz_scode_excluded(self):
        # "9S11" must not leak Flare Blitz at level 11; "9L62" still grants it.
        assert "flareblitz" not in _get_learnset_moves("charizard", 11, 9)
        assert _get_learnset_moves("charizard", 62, 9).get("flareblitz") == 62

    def test_scode_absent_from_public_move_pools(self):
        # get_all_pokemon_moves feeds wild/starter movesets; no S-code leakage.
        assert "psystrike" not in get_all_pokemon_moves("mewtwo", 8, 9)


class TestLearnsetMismatches:
    def test_clean_pokeapi_name_suffixes(self):
        from Ankimon.functions.learnset_retrieval import clean_pokeapi_name

        assert clean_pokeapi_name("Darmanitan-galar-standard") == "Darmanitan-galar"
        assert clean_pokeapi_name("meowstic-female") == "meowsticf"
        assert clean_pokeapi_name("giratina-altered") == "giratina"
        assert clean_pokeapi_name("ogerpon-wellspring-mask") == "ogerpon-wellspring"

    def test_get_learnset_moves_with_mismatched_pokeapi_names(self):
        # Even with mocked learnsets, if we pass a name with standard/normal suffix,
        # it should clean it first and successfully find the moves for the base form.
        moves = _get_learnset_moves("slowpoke-standard", 12, 9)
        assert "confusion" in moves

        # Test female suffix mapping
        from Ankimon.functions.learnset_retrieval import _load_learnset_cache

        cache = _load_learnset_cache()
        cache["slowpokef"] = cache["slowpoke"]
        moves_f = _get_learnset_moves("slowpoke-female", 12, 9)
        assert "confusion" in moves_f

    def test_eternamax_learnset_fallback(self):
        # Test that eternatuseternamax successfully falls back to eternatus learnset
        moves = _get_learnset_moves("eternatuseternamax", 60, 9)
        assert "dynamaxcannon" in moves
        assert moves["dynamaxcannon"] == 56

    def test_special_form_learnset_merge(self):
        # Test that necrozmaultra (which only has R/tutor moves) merges necrozma's level-up moves
        # and correctly resolves its own Relearn (R) moves
        moves = _get_learnset_moves("necrozmaultra", 50, 9)
        assert "photongeyser" in moves
        assert "moongeistbeam" in moves
        assert "sunsteelstrike" in moves

    def test_deoxys_form_exclusive_moves(self):
        # 1. Deoxys Normal
        normal_moves = _get_learnset_moves("deoxys", 100, 9)
        assert "cosmicpower" in normal_moves
        assert "recover" in normal_moves
        assert "teleport" in normal_moves
        assert "spikes" not in normal_moves
        assert "superpower" not in normal_moves
        assert "extremespeed" not in normal_moves

        # 2. Deoxys Attack
        attack_moves = _get_learnset_moves("deoxysattack", 100, 9)
        assert "superpower" in attack_moves
        assert "zapcannon" in attack_moves
        assert "teleport" in attack_moves
        assert "recover" not in attack_moves
        assert "spikes" not in attack_moves
        assert "extremespeed" not in attack_moves
        assert "cosmicpower" not in attack_moves

        # 3. Deoxys Defense
        defense_moves = _get_learnset_moves("deoxysdefense", 100, 9)
        assert "spikes" in defense_moves
        assert "recover" in defense_moves
        assert "teleport" in defense_moves
        assert "superpower" not in defense_moves
        assert "extremespeed" not in defense_moves
        assert "cosmicpower" not in defense_moves

        # 4. Deoxys Speed
        speed_moves = _get_learnset_moves("deoxysspeed", 100, 9)
        assert "extremespeed" in speed_moves
        assert "recover" in speed_moves
        assert "teleport" in speed_moves
        assert "spikes" not in speed_moves
        assert "superpower" not in speed_moves
        assert "cosmicpower" not in speed_moves

    def test_deoxys_suffix_cleaned_form_applies_exclusions(self):
        # "deoxys-normal" (PokéAPI name) resolves to "deoxys" via Fallback 1
        # suffix cleaning; the normal-form exclusions must key on the resolved
        # name, not on the stale "deoxysnormal" normalization.
        moves = _get_learnset_moves("deoxys-normal", 100, 9)
        assert "cosmicpower" in moves
        assert "recover" in moves
        assert "teleport" in moves
        assert "spikes" not in moves
        assert "superpower" not in moves
        assert "extremespeed" not in moves
        assert "zapcannon" not in moves

    def test_deoxys_reverse_lookup_applies_exclusions(self):
        # An unmapped form suffix skips Fallback 1 (not in the suffix table)
        # and resolves via Fallback 2's pokedex reverse lookup ("deoxys-unknown"
        # -> actual_id 386 -> canonical key "deoxys"); exclusions must key on
        # the resolved canonical name as well.
        moves = _get_learnset_moves("deoxys-unknown", 100, 9)
        assert "cosmicpower" in moves
        assert "recover" in moves
        assert "teleport" in moves
        assert "spikes" not in moves
        assert "superpower" not in moves
        assert "extremespeed" not in moves
