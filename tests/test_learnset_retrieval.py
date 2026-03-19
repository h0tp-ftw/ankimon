import json
import importlib
import sys
import types
from pathlib import Path
from unittest.mock import mock_open, patch

import pytest

# We need to import learnset_retrieval without triggering Ankimon/__init__.py
# (which depends on Anki internals). We do this by:
# 1. Creating stub parent packages in sys.modules
# 2. Loading only the target module from its file path.

_src = Path(__file__).parent.parent / "src"

# Stub parent packages so relative imports resolve without loading __init__.py
for _pkg in ("Ankimon", "Ankimon.functions"):
    if _pkg not in sys.modules:
        _mod = types.ModuleType(_pkg)
        _mod.__path__ = [str(_src / _pkg.replace(".", "/"))]
        _mod.__package__ = _pkg
        sys.modules[_pkg] = _mod

# Stub the resources module with a fake learnset_path
_resources = types.ModuleType("Ankimon.resources")
_resources.learnset_path = "/fake/learnsets.json"
sys.modules["Ankimon.resources"] = _resources

# Now load learnset_retrieval from its file
_spec = importlib.util.spec_from_file_location(
    "Ankimon.functions.learnset_retrieval",
    _src / "Ankimon" / "functions" / "learnset_retrieval.py",
)
_lr = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _lr
_spec.loader.exec_module(_lr)

_get_learnset_moves = _lr._get_learnset_moves
get_all_pokemon_moves = _lr.get_all_pokemon_moves
get_levelup_move_for_pokemon = _lr.get_levelup_move_for_pokemon
get_random_moves_for_pokemon = _lr.get_random_moves_for_pokemon


FAKE_LEARNSET = {
    "slowpoke": {
        "learnset": {
            "tackle": ["9L1", "3L1"],
            "confusion": ["9L12", "3L17"],
            "psychic": ["9L33", "3L40"],
            "yawn": ["9L15"],
            "surf": ["9M"],  # TM, no level entry
        }
    }
}

_FAKE_JSON = json.dumps(FAKE_LEARNSET)


@pytest.fixture(autouse=True)
def _mock_learnset_file():
    with patch("builtins.open", mock_open(read_data=_FAKE_JSON)):
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
