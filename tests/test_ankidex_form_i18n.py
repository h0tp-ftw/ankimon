"""Form names remain distinguishable when the Ankidex language changes."""

import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest


ANKIDEX_JS = Path(__file__).resolve().parents[1] / "src/Ankimon/ankidex/ankidex.js"


def test_payload_includes_mega_and_gmax_names(monkeypatch):
    from Ankimon.ankidex import ankidex_data

    names = types.ModuleType("Ankimon.functions.pokedex_functions")
    names._load_pokemon_names_csv = lambda: {(6, 5): "Dracaufeu"}
    names._load_pokemon_descriptions_csv = lambda: {}
    names._normalize_language_id = lambda lang: lang
    names.get_pokemon_diff_lang_name = lambda pokemon_id, lang: {
        10034: "Méga-Dracaufeu X",
        10035: "Méga-Dracaufeu Y",
        10196: "Gigamax Dracaufeu",
    }.get(pokemon_id, "No Translation in this language")
    monkeypatch.setitem(sys.modules, names.__name__, names)
    localized = types.ModuleType("Ankimon.localized_text")
    localized.type_name = lambda english, fallback: english
    localized.current_lang_code = lambda: "fr"
    localized._load = lambda kind, code: {}
    monkeypatch.setitem(sys.modules, localized.__name__, localized)
    monkeypatch.setattr(ankidex_data.encounter_data, "MEGA", [10034, 10035])
    monkeypatch.setattr(ankidex_data.encounter_data, "GMAX", [10196])
    monkeypatch.setattr(ankidex_data.encounter_data, "REGIONAL_FORM_REGION", {})

    overlay = ankidex_data._ankidex_i18n(
        types.SimpleNamespace(get=lambda key, default=None: 5)
    )
    assert overlay["names"]["6"] == "Dracaufeu"
    assert overlay["names"]["10034"] == "Méga-Dracaufeu X"
    assert overlay["names"]["10035"] == "Méga-Dracaufeu Y"
    assert overlay["names"]["10196"] == "Gigamax Dracaufeu"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_js_keeps_form_label_when_translation_is_missing():
    # Evaluate the actual overlay function with the exact reported fixture.
    script = r"""
const fs = require('fs');
const assert = require('assert');
const js = fs.readFileSync(process.argv[1], 'utf8');
const source = js.slice(js.indexOf('function applyI18nToSpecies()'),
                        js.indexOf('function localTypeLabel('));
const state = {
  i18n: {names: {'6': 'Dracaufeu'}, types: {}},
  allPokemon: [
    {actual_id: 6, species_id: 6, name: 'Charizard'},
    {actual_id: 10034, species_id: 6, name: 'Charizard-Mega-X'},
    {actual_id: 10035, species_id: 6, name: 'Charizard-Mega-Y'},
    {actual_id: 10196, species_id: 6, name: 'Charizard-Gmax'},
  ],
};
eval(source);
applyI18nToSpecies();
assert.deepStrictEqual(state.allPokemon.map(p => p.name), [
  'Dracaufeu', 'Charizard-Mega-X', 'Charizard-Mega-Y', 'Charizard-Gmax',
]);
state.i18n.names['10034'] = 'Méga-Dracaufeu X';
applyI18nToSpecies();
assert.strictEqual(state.allPokemon[1].name, 'Méga-Dracaufeu X');
"""
    subprocess.run(["node", "-e", script, str(ANKIDEX_JS)], check=True)
