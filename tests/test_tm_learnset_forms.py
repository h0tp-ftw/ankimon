import importlib.util
import json
import re
import sys
import types
from pathlib import Path
from unittest.mock import mock_open

import pytest

_SRC = Path(__file__).parent.parent / "src"
_MODULE_PATH = _SRC / "Ankimon" / "functions" / "tm_learnset.py"

POKEDEX_META = {
    "aegislash": {"baseForme": "Shield"},
    "deoxys": {"baseForme": "Normal"},
    "meowstic": {"baseForme": "M"},
    "meowsticf": {"baseSpecies": "Meowstic", "forme": "F"},
    "indeedee": {"baseForme": "M"},
    "indeedeef": {"baseSpecies": "Indeedee", "forme": "F"},
    "maushold": {"baseForme": "Three"},
    "mausholdfour": {"baseSpecies": "Maushold", "forme": "Four"},
    "squawkabilly": {"baseForme": "Green"},
    "squawkabillyblue": {"baseSpecies": "Squawkabilly", "forme": "Blue"},
    "squawkabillyyellow": {"baseSpecies": "Squawkabilly", "forme": "Yellow"},
    "squawkabillywhite": {"baseSpecies": "Squawkabilly", "forme": "White"},
    "venusaurmega": {"baseSpecies": "Venusaur", "forme": "Mega"},
}

TM_DATA = {
    "charizard": ["flamethrower"],
    "aegislashshield": ["ironhead"],
    "deoxysnormal": ["psychic"],
    "meowsticmale": ["psychicnoise"],
    "meowsticfemale": ["shadowball"],
    "indeedeemale": ["hypervoice"],
    "indeedeefemale": ["dazzlinggleam"],
    "mausholdfamilyofthree": ["populationbomb"],
    "mausholdfamilyoffour": ["playrough"],
    "squawkabillygreenplumage": ["bravebird"],
    "squawkabillyblueplumage": ["doubleedge"],
    "squawkabillyyellowplumage": ["heatwave"],
    "squawkabillywhiteplumage": ["partingshot"],
    "venusaur": ["solarbeam"],
}


def _normalise(value):
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _ensure_package(monkeypatch, name, path):
    mod = types.ModuleType(name)
    mod.__path__ = [str(path)]
    mod.__package__ = name
    monkeypatch.setitem(sys.modules, name, mod)


@pytest.fixture
def tm_module(monkeypatch):
    _ensure_package(monkeypatch, "Ankimon", _SRC / "Ankimon")
    _ensure_package(monkeypatch, "Ankimon.functions", _SRC / "Ankimon" / "functions")

    resources = types.ModuleType("Ankimon.resources")
    resources.pokemon_tm_learnset_path = "/fake/pokemon_tm_learnset.json"
    monkeypatch.setitem(sys.modules, "Ankimon.resources", resources)

    pokedex = types.ModuleType("Ankimon.functions.pokedex_functions")

    def search_pokedex(name, variable):
        return POKEDEX_META.get(_normalise(name), {}).get(variable, [])

    pokedex.search_pokedex = search_pokedex
    monkeypatch.setitem(sys.modules, "Ankimon.functions.pokedex_functions", pokedex)

    opener = mock_open(read_data=json.dumps(TM_DATA))
    monkeypatch.setattr("builtins.open", opener)

    spec = importlib.util.spec_from_file_location(
        "Ankimon.functions.tm_learnset", _MODULE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module, opener


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Charizard", ["flamethrower"]),
        ("Aegislash", ["ironhead"]),
        ("Deoxys", ["psychic"]),
        ("Meowstic", ["psychicnoise"]),
        ("Meowstic-F", ["shadowball"]),
        ("Indeedee", ["hypervoice"]),
        ("Indeedee-F", ["dazzlinggleam"]),
        ("Maushold", ["populationbomb"]),
        ("Maushold-Four", ["playrough"]),
        ("Squawkabilly", ["bravebird"]),
        ("Squawkabilly-Blue", ["doubleedge"]),
        ("Squawkabilly-Yellow", ["heatwave"]),
        ("Squawkabilly-White", ["partingshot"]),
    ],
)
def test_resolves_direct_default_and_explicit_form_keys(tm_module, name, expected):
    module, _ = tm_module
    assert module.get_tm_learnset(name) == expected


def test_explicit_form_falls_back_to_base_species_when_tm_data_has_no_form_key(tm_module):
    module, _ = tm_module
    assert module.get_tm_learnset("Venusaur-Mega") == ["solarbeam"]


def test_unknown_or_blank_names_return_no_moves(tm_module):
    module, _ = tm_module
    assert module.get_tm_learnset("MissingNo") == []
    assert module.get_tm_learnset("") == []
    assert module.get_tm_learnset(None) == []


def test_tm_json_is_loaded_once_and_reused(tm_module):
    module, opener = tm_module

    assert module.get_tm_learnset("Aegislash") == ["ironhead"]
    assert module.get_tm_learnset("Charizard") == ["flamethrower"]
    assert module.warm_tm_learnset_cache() == len(TM_DATA)

    assert opener.call_count == 1


def test_failed_warm_does_not_poison_cache_and_later_access_retries(tm_module, monkeypatch):
    module, _ = tm_module
    failing = mock_open()
    failing.side_effect = OSError("temporarily unreadable")
    monkeypatch.setattr("builtins.open", failing)

    with pytest.raises(OSError, match="temporarily unreadable"):
        module.warm_tm_learnset_cache()

    assert module._tm_learnsets_cache is None

    succeeding = mock_open(read_data=json.dumps(TM_DATA))
    monkeypatch.setattr("builtins.open", succeeding)
    assert module.get_tm_learnset("Deoxys") == ["psychic"]
    assert succeeding.call_count == 1


def test_non_mapping_tm_file_is_rejected_and_retryable(tm_module, monkeypatch):
    module, _ = tm_module
    opener = mock_open(read_data='["not", "a", "mapping"]')
    monkeypatch.setattr("builtins.open", opener)

    with pytest.raises(ValueError, match="JSON object"):
        module.warm_tm_learnset_cache()

    assert module._tm_learnsets_cache is None
