"""Unit tests for the CP formula and related business functions."""
import sys
import types
from pathlib import Path

import pytest

# Stub Ankimon packages so we can import business.py without triggering __init__.py
_src = Path(__file__).parent.parent / "src"
for _pkg in ("Ankimon", "Ankimon.functions", "Ankimon.pyobj"):
    if _pkg not in sys.modules:
        _mod = types.ModuleType(_pkg)
        _mod.__path__ = [str(_src / _pkg.replace(".", "/"))]
        _mod.__package__ = _pkg
        sys.modules[_pkg] = _mod

# Stub modules that business.py imports at module level, but give
# resources the real effectiveness_chart_file_path so type chart tests work.
from unittest.mock import MagicMock

_resources_mock = MagicMock()
_resources_mock.effectiveness_chart_file_path = (
    _src / "Ankimon" / "addon_files" / "eff_chart.json"
)
for _dep in ("Ankimon.resources", "Ankimon.const"):
    if _dep not in sys.modules:
        sys.modules[_dep] = (
            _resources_mock if _dep == "Ankimon.resources" else MagicMock()
        )

# Now import the functions under test
from Ankimon.business import (
    calculate_cpm,
    calculate_pokemon_go_cp,
    calculate_present_power,
    format_compact_number,
    pokemon_go_raw_stats,
    type_compatibility_multiplier,
    calculate_cp_from_dict,
    cp_breakdown_tooltip,
    pokemon_cp_is_stale,
    _load_type_chart,
)


class TestCalculateCPM:
    def test_level_1_is_small(self):
        cpm = calculate_cpm(1)
        assert 0 < cpm < 0.1

    def test_level_100_near_cap(self):
        cpm = calculate_cpm(100)
        assert 2.4 < cpm <= 2.45

    def test_monotonically_increasing(self):
        values = [calculate_cpm(lv) for lv in range(1, 101)]
        for a, b in zip(values, values[1:]):
            assert b > a

    def test_level_0_treated_as_1(self):
        assert calculate_cpm(0) == calculate_cpm(1)


class TestPokemonGoRawStats:
    def test_basic(self):
        base = {"hp": 50, "atk": 80, "def": 60, "spa": 100, "spd": 70, "spe": 90}
        iv = {"hp": 10, "atk": 15, "def": 10, "spa": 15, "spd": 10, "spe": 15}
        ev = {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0}
        attack, defense, stamina = pokemon_go_raw_stats(base, iv, ev)
        # attack = ((80+15) + (100+15)) / 2 = 105
        assert attack == 105.0
        # defense = ((60+10) + (70+10)) / 2 = 75
        assert defense == 75.0
        # stamina = 50 + 10 = 60
        assert stamina == 60

    def test_ev_contribution(self):
        base = {"hp": 50, "atk": 80, "def": 60, "spa": 100, "spd": 70, "spe": 90}
        iv = {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0}
        ev = {"hp": 252, "atk": 252, "def": 0, "spa": 0, "spd": 0, "spe": 0}
        attack, defense, stamina = pokemon_go_raw_stats(base, iv, ev)
        # EV/4: atk gets 63, hp gets 63
        assert attack == (80 + 63 + 100) / 2  # 121.5
        assert stamina == 50 + 63  # 113

    def test_missing_keys_default_to_1_and_0(self):
        attack, defense, stamina = pokemon_go_raw_stats({}, {}, {})
        # base defaults to 1, iv/ev to 0 → raw = 1 each
        assert attack == 1.0
        assert defense == 1.0
        assert stamina == 1


class TestCalculatePokemonGoCP:
    def test_minimum_clamp(self):
        # Very low stats should clamp to 10
        cp = calculate_pokemon_go_cp(1, 1, 1, 1)
        assert cp == 10

    def test_reasonable_mid_level(self):
        # Moderate stats at level 50 should give a sensible number
        cp = calculate_pokemon_go_cp(100, 80, 70, 50)
        assert cp > 10
        assert cp < 10000

    def test_higher_level_gives_higher_cp(self):
        cp_low = calculate_pokemon_go_cp(100, 80, 70, 10)
        cp_high = calculate_pokemon_go_cp(100, 80, 70, 50)
        assert cp_high > cp_low

    def test_higher_attack_gives_higher_cp(self):
        cp_weak = calculate_pokemon_go_cp(50, 80, 70, 50)
        cp_strong = calculate_pokemon_go_cp(150, 80, 70, 50)
        assert cp_strong > cp_weak

    def test_low_level_pokemon_not_all_same(self):
        # With raw stats, even level-5 Pokemon should differentiate
        cp_weak = calculate_pokemon_go_cp(30, 30, 30, 5)
        cp_strong = calculate_pokemon_go_cp(120, 100, 100, 5)
        assert cp_strong > cp_weak


class TestTypeCompatibilityMultiplier:
    def test_neutral_matchup(self):
        assert type_compatibility_multiplier("Normal", "Normal") == 1.0

    def test_super_effective(self):
        assert type_compatibility_multiplier("Fire", "Grass") == 1.5

    def test_not_very_effective(self):
        assert type_compatibility_multiplier("Grass", "Fire") == 0.8

    def test_immunity_returns_low(self):
        # Ghost vs Normal is immune (0x) → should return 0.2, not 0.8
        mult = type_compatibility_multiplier("Normal", "Ghost")
        assert mult == 0.2

    def test_unknown_types_return_neutral(self):
        assert type_compatibility_multiplier("FakeType", "Water") == 1.0

    def test_empty_types_return_neutral(self):
        assert type_compatibility_multiplier([], ["Fire"]) == 1.0
        assert type_compatibility_multiplier(None, None) == 1.0

    def test_accepts_list_of_types(self):
        # Fire/Flying vs Grass — at least one pair is super-effective
        mult = type_compatibility_multiplier(["Fire", "Flying"], ["Grass"])
        assert mult == 1.5


class TestCalculatePresentPower:
    def test_full_hp_neutral_equals_cp(self):
        # BP is on the CP scale: full HP + neutral matchup → BP == CP
        assert calculate_present_power(100, 50, 50, 1.0) == 100

    def test_half_hp_halves_bp(self):
        assert calculate_present_power(100, 25, 50, 1.0) == 50

    def test_with_multiplier(self):
        pp = calculate_present_power(100, 50, 50, 1.5)
        assert pp == 150

    def test_zero_hp(self):
        pp = calculate_present_power(100, 0, 50, 1.5)
        assert pp == 0

    def test_negative_hp_clamps_to_zero(self):
        # The current_hp clamp must run BEFORE the max_hp<=0 liveness check:
        # a negative current_hp is dead, never "living = full health".
        assert calculate_present_power(100, -5, 50, 1.0) == 0
        assert calculate_present_power(100, -5, 0, 1.0) == 0

    def test_none_values(self):
        pp = calculate_present_power(None, None, None, None)
        assert pp == 0

    def test_unknown_max_hp_treats_living_as_unhurt(self):
        # max_hp 0/None (stub data) → living Pokemon counts as full health
        assert calculate_present_power(100, 50, 0, 1.0) == 100
        assert calculate_present_power(100, 50, None, 1.0) == 100

    def test_overheal_clamps_to_full(self):
        # current_hp above max_hp must not push BP past CP
        assert calculate_present_power(100, 80, 50, 1.0) == 100

    def test_atk_boost_increases_bp(self):
        # +2 stage = 5/3, BP = 100 × 1 × 1 × 5/3 = 166.66 → floor 166
        assert calculate_present_power(100, 50, 50, 1.0, atk_stage=2, spa_stage=2) == 166

    def test_atk_drop_decreases_bp(self):
        # -2 stage = 3/5 = 0.6, BP = 100 × 1 × 1 × 0.6 = 60
        assert calculate_present_power(100, 50, 50, 1.0, atk_stage=-2, spa_stage=-2) == 60

    def test_mixed_atk_spa_stages_averaged(self):
        # atk +2 (5/3), spa -2 (3/5), avg = 17/15, BP = 100 × 17/15 = 113.33 → floor 113
        assert calculate_present_power(100, 50, 50, 1.0, atk_stage=2, spa_stage=-2) == 113

    def test_out_of_range_stage_neutral(self):
        # Stage 7 is invalid per get_multiplier_stats — should fall back to 1.0
        assert calculate_present_power(100, 50, 50, 1.0, atk_stage=7, spa_stage=7) == 100

    def test_none_stage_neutral(self):
        assert calculate_present_power(100, 50, 50, 1.0, atk_stage=None, spa_stage=None) == 100


class TestCalculateCPFromDict:
    """Tests for the dict-based CP entry point used by collection/PC box."""

    BASE = {"hp": 35, "atk": 55, "def": 40, "spa": 50, "spd": 50, "spe": 90}

    def test_with_base_stats_key(self):
        pokemon = {
            "base_stats": self.BASE,
            "stats": {k: v * 3 for k, v in self.BASE.items()},  # inflated
            "level": 50,
            "iv": {"hp": 15, "atk": 15, "def": 15, "spa": 15, "spd": 15, "spe": 15},
            "ev": {},
        }
        cp = calculate_cp_from_dict(pokemon)
        # Should use base_stats, not the inflated stats
        expected = calculate_pokemon_go_cp(
            *pokemon_go_raw_stats(self.BASE, pokemon["iv"], {}), 50
        )
        assert cp == expected

    def test_falls_back_to_stats_key(self):
        pokemon = {
            "stats": self.BASE,
            "level": 25,
            "iv": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
            "ev": {},
        }
        cp = calculate_cp_from_dict(pokemon)
        expected = calculate_pokemon_go_cp(
            *pokemon_go_raw_stats(self.BASE, pokemon["iv"], {}), 25
        )
        assert cp == expected

    def test_missing_iv_ev_default_to_empty(self):
        pokemon = {"base_stats": self.BASE, "level": 10}
        cp = calculate_cp_from_dict(pokemon)
        assert cp >= 10  # minimum clamp

    def test_none_iv_ev_coerced(self):
        pokemon = {"base_stats": self.BASE, "level": 10, "iv": None, "ev": None}
        cp = calculate_cp_from_dict(pokemon)
        assert cp >= 10

    def test_empty_stats_returns_minimum(self):
        pokemon = {"stats": {}, "level": 1}
        cp = calculate_cp_from_dict(pokemon)
        assert cp == 10  # minimum clamp with all defaults


class TestDictShapeEquivalence:
    """Sentinel: caught-Pokemon dicts ('stats' = base_stats, no 'base_stats')
    and to_dict dicts ('base_stats' = bases, 'stats' = level-scaled) must
    produce the same CP. Regression guard for the pokemon-persistence fix
    that routed save_caught_pokemon through PokemonObject.to_dict().
    """

    BASE = {"hp": 100, "atk": 80, "def": 90, "spa": 70, "spd": 85, "spe": 95}
    LEVEL = 50
    IV = {"hp": 20, "atk": 25, "def": 30, "spa": 15, "spd": 28, "spe": 31}
    EV = {"hp": 100, "atk": 200, "def": 150, "spa": 80, "spd": 100, "spe": 80}

    def _caught_shape(self):
        return {
            "level": self.LEVEL,
            "stats": self.BASE,
            "iv": self.IV,
            "ev": self.EV,
        }

    def _to_dict_shape(self):
        return {
            "level": self.LEVEL,
            "base_stats": self.BASE,
            "stats": {k: 999 for k in self.BASE},
            "iv": self.IV,
            "ev": self.EV,
            "cp": 42,
            "nature": "adamant",
        }

    def test_both_shapes_give_same_cp(self):
        assert calculate_cp_from_dict(self._caught_shape()) == calculate_cp_from_dict(
            self._to_dict_shape()
        )

    def test_to_dict_shape_ignores_level_scaled_stats(self):
        direct = calculate_pokemon_go_cp(
            *pokemon_go_raw_stats(self.BASE, self.IV, self.EV), self.LEVEL
        )
        assert calculate_cp_from_dict(self._to_dict_shape()) == direct


class TestCPBreakdownTooltip:
    BASE = {"hp": 100, "atk": 80, "def": 90, "spa": 70, "spd": 85, "spe": 95}

    def test_contains_formula(self):
        tip = cp_breakdown_tooltip(
            {"base_stats": self.BASE, "iv": {}, "ev": {}, "level": 50}
        )
        assert "CP = floor(" in tip
        assert "CPM²" in tip or "CPM^2" in tip or "CPM" in tip

    def test_contains_level_100_projection(self):
        tip = cp_breakdown_tooltip(
            {"base_stats": self.BASE, "iv": {}, "ev": {}, "level": 50}
        )
        expected_max = calculate_pokemon_go_cp(
            *pokemon_go_raw_stats(self.BASE, {}, {}), 100
        )
        assert f"{expected_max:,}" in tip

    def test_handles_caught_shape(self):
        # caught dicts have "stats"=base_stats, no "base_stats"
        tip = cp_breakdown_tooltip(
            {"stats": self.BASE, "iv": {}, "ev": {}, "level": 25}
        )
        assert "CP at Level 100" in tip

    def test_handles_missing_level(self):
        tip = cp_breakdown_tooltip({"base_stats": self.BASE})
        assert "CP at Level 100" in tip


class TestFormatCompactNumber:
    """format_compact_number keeps battle-window CP/BP inside their boxes."""

    def test_small_values_keep_thousands_separator(self):
        assert format_compact_number(0) == "0"
        assert format_compact_number(10) == "10"
        assert format_compact_number(2564) == "2,564"
        assert format_compact_number(9999) == "9,999"

    def test_thousands_compact(self):
        assert format_compact_number(10_000) == "10K"
        assert format_compact_number(54_120) == "54.1K"
        assert format_compact_number(301_344) == "301K"
        assert format_compact_number(366_652) == "367K"
        assert format_compact_number(999_400) == "999K"

    def test_millions_compact(self):
        assert format_compact_number(1_000_000) == "1M"
        assert format_compact_number(4_323_910) == "4.3M"
        assert format_compact_number(43_000_000) == "43M"

    def test_tier_boundary_promotes_instead_of_1000K(self):
        assert format_compact_number(999_500) == "1M"
        assert format_compact_number(999_950) == "1M"
        assert format_compact_number(999_500_000) == "1B"

    def test_garbage_and_negatives_render_zero(self):
        assert format_compact_number(None) == "0"
        assert format_compact_number("junk") == "0"
        assert format_compact_number(-5) == "0"
        assert format_compact_number(float("inf")) == "0"
        assert format_compact_number(float("nan")) == "0"

    def test_float_input_truncates_like_int(self):
        assert format_compact_number(2564.9) == "2,564"


class TestPokemonCpIsStale:
    """``cp`` is derived but persisted, so it can drift from the formula."""

    _ROW = {
        "level": 100,
        "stats": {"hp": 106, "atk": 110, "def": 90, "spa": 154, "spd": 90, "spe": 130},
        "iv": {"hp": 31, "atk": 31, "def": 31, "spa": 31, "spd": 31, "spe": 31},
        "ev": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
    }

    def _row(self, **overrides):
        row = {k: (dict(v) if isinstance(v, dict) else v) for k, v in self._ROW.items()}
        row.update(overrides)
        return row

    def test_matching_cp_is_not_stale(self):
        row = self._row()
        row["cp"] = calculate_cp_from_dict(row)
        assert pokemon_cp_is_stale(row) is False

    def test_value_from_superseded_formula_is_stale(self):
        # 1460 is what the pre-retune 0.84/20 CPM produced for this Pokemon.
        assert pokemon_cp_is_stale(self._row(cp=1460)) is True

    def test_missing_cp_is_stale(self):
        assert pokemon_cp_is_stale(self._row()) is True

    def test_uncomputable_row_reports_stale_rather_than_raising(self):
        # Callers repair what they can; the check must never be the thing
        # that takes down a PC-box open.
        assert pokemon_cp_is_stale({"cp": 1, "stats": "not-a-dict"}) is True

    def test_non_dict_is_not_stale(self):
        assert pokemon_cp_is_stale(None) is False


class TestLevelCoercion:
    """Rows restored from JSON can carry ``level`` as a string."""

    _ROW = {
        "stats": {"hp": 50, "atk": 80, "def": 60, "spa": 100, "spd": 70, "spe": 90},
        "iv": {"hp": 10, "atk": 15, "def": 10, "spa": 15, "spd": 10, "spe": 15},
        "ev": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
    }

    def test_string_level_matches_int_level(self):
        as_int = calculate_cp_from_dict({**self._ROW, "level": 50})
        as_str = calculate_cp_from_dict({**self._ROW, "level": "50"})
        assert as_str == as_int

    def test_none_level_is_treated_as_one(self):
        as_none = calculate_cp_from_dict({**self._ROW, "level": None})
        as_one = calculate_cp_from_dict({**self._ROW, "level": 1})
        assert as_none == as_one

    def test_non_numeric_level_falls_back_to_one(self):
        # A level that is not a number at all is meaningless; it must not
        # take down the CP read for every consumer of the row.
        as_garbage = calculate_cp_from_dict({**self._ROW, "level": "???"})
        as_one = calculate_cp_from_dict({**self._ROW, "level": 1})
        assert as_garbage == as_one

    def test_malformed_stats_still_raise(self):
        # Deliberately NOT swallowed: callers that repair rows must be able
        # to tell "cannot compute" from "computed the floor value", or the
        # migration pass would overwrite good CP with the minimum clamp.
        with pytest.raises(Exception):
            calculate_cp_from_dict({**self._ROW, "level": 50, "stats": "corrupt"})
