"""Distinguish wrong-time item evolutions from wrong-item selections."""

import pytest
from unittest.mock import patch

from conftest import isolated_modules


@pytest.fixture
def pokedex_functions():
    with isolated_modules("aqt", extra=("Ankimon.functions.pokedex_functions",)):
        import importlib
        import sys

        sys.modules["aqt"] = None
        yield importlib.import_module("Ankimon.functions.pokedex_functions")


HAPPINY = 440
CHANSEY = 113
OVAL_STONE = 110
LINKING_CORD = 2160
PIKACHU = 25


def test_the_oval_stone_is_offered_by_day(pokedex_functions):
    with patch.object(pokedex_functions, "get_time_of_day", return_value="day"):
        assert pokedex_functions.check_evolution_by_item(HAPPINY, OVAL_STONE) == CHANSEY
        assert (
            pokedex_functions.item_evolution_time_requirement(HAPPINY, OVAL_STONE)
            is None
        )


def test_at_night_the_requirement_is_reported_rather_than_denied(pokedex_functions):
    with patch.object(pokedex_functions, "get_time_of_day", return_value="night"):
        assert pokedex_functions.check_evolution_by_item(HAPPINY, OVAL_STONE) is None
        assert (
            pokedex_functions.item_evolution_time_requirement(HAPPINY, OVAL_STONE)
            == "day"
        )


@pytest.mark.parametrize("time_of_day", ["day", "night"])
def test_a_genuinely_wrong_item_reports_no_time_requirement(
    pokedex_functions, time_of_day
):
    with patch.object(pokedex_functions, "get_time_of_day", return_value=time_of_day):
        assert (
            pokedex_functions.item_evolution_time_requirement(HAPPINY, LINKING_CORD)
            is None
        )
        assert (
            pokedex_functions.item_evolution_time_requirement(PIKACHU, OVAL_STONE)
            is None
        )


@pytest.mark.parametrize("time_of_day", ["day", "night"])
def test_an_ungated_item_evolution_never_reports_a_time(pokedex_functions, time_of_day):
    with patch.object(pokedex_functions, "get_time_of_day", return_value=time_of_day):
        assert pokedex_functions.check_evolution_by_item(64, LINKING_CORD) == 65
        assert (
            pokedex_functions.item_evolution_time_requirement(64, LINKING_CORD) is None
        )


def test_ignore_time_never_leaks_into_the_evolving_path(pokedex_functions):
    with patch.object(pokedex_functions, "get_time_of_day", return_value="night"):
        assert pokedex_functions.check_evolution_by_item(HAPPINY, OVAL_STONE) is None
        assert (
            pokedex_functions.check_evolution_by_item(
                HAPPINY, OVAL_STONE, ignore_time=True
            )
            == CHANSEY
        )
