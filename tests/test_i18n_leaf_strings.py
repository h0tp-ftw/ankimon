"""Characterization / parity test for F01 (i18n leaf-feature string additions).

Pins the observable contract of the leaf-feature translation strings ported from
BRRRR_Experimental onto main's ``src/Ankimon/lang/*_text.json`` files:

* every language file still parses as valid JSON (import/construct smoke);
* the seven evolution / friendship / badge leaf keys are present in all eleven
  ``*_text.json`` files with a non-empty value;
* the English reference values match byte-for-byte (the strings other languages
  fall back to via ``Translator.translate``);
* ``evolve_now_button`` honours the ``{evo_name}`` placeholder contract in every
  language (this is exactly what ``Translator.translate(key, evo_name=...)``
  does: ``template.format(**kwargs)``);
* ``nature_chart_button`` is available in every language catalog;
* main's pre-existing guard keys (``pokemon_about_to_evolve_friendship`` and the
  ``cp_label``/``bp_label``/``combat_power``/``battle_power`` block, both
  DONE-IN-BASE per GUARDS-TO-REAPPLY) survive in all eleven languages and keep
  their English reference values.

The setting_name.json / setting_description.json auto-catch / active-region /
team-cycle keys from the same upstream change are intentionally NOT part of this
unit -- they are gate-coupled to F28's ``settings_window.py`` hierarchical_groups
wiring (see the PR body) and travel with that leaf. This test therefore also
pins that main's ``setting_*.json`` guard keys are preserved untouched.
"""

import json
from pathlib import Path

import pytest

LANG_DIR = Path(__file__).parent.parent / "src" / "Ankimon" / "lang"

TEXT_LANGS = [
    "ch",
    "cz",
    "de",
    "en",
    "es_latam",
    "fr",
    "it",
    "jp",
    "kr",
    "po",
    "sp",
]

FRIENDSHIP_KEYS = [
    "friendship_label",
    "friendship_tooltip",
    "bff_tooltip",
    "evolve_now_button",
    "badge_ready",
    "badge_wait_day",
    "badge_wait_night",
]

# Main guard keys (GUARDS-TO-REAPPLY, DONE-IN-BASE): upstream re-added these,
# but main already had them; the port must neither drop nor overwrite them.
TEXT_GUARD_KEYS = [
    "pokemon_about_to_evolve_friendship",
    "cp_label",
    "bp_label",
    "combat_power",
    "battle_power",
]

# English reference values (byte-exact) that every other language falls back to.
EN_EXPECTED = {
    "nature_chart_button": "Check Nature Chart",
    "friendship_label": "Friendship",
    "friendship_tooltip": "Measures the bond between you and your Pokémon.",
    "bff_tooltip": "💖 Best Friend (Highest Friendship)",
    "evolve_now_button": "⇈ Evolve into {evo_name}! ⇈",
    "badge_ready": "⇈ Ready to evolve!",
    "badge_wait_day": "☀️ Ready to evolve during the day",
    "badge_wait_night": "🌙 Ready to evolve at night",
    # Guard values (main's pre-existing English text, must not be overwritten).
    "pokemon_about_to_evolve_friendship": (
        "{main_pokemon_name} is ready to evolve into {evo_pokemon_name}!"
    ),
    "cp_label": "CP",
    "bp_label": "BP",
    "combat_power": "Combat Power",
    "battle_power": "Battle Power",
}


def _load(name):
    path = LANG_DIR / f"{name}.json"
    assert path.exists(), f"missing lang file: {path}"
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)  # import/construct smoke: must be valid JSON


@pytest.mark.parametrize("lang", TEXT_LANGS)
def test_friendship_keys_present_in_every_language(lang):
    data = _load(f"{lang}_text")
    for key in FRIENDSHIP_KEYS:
        assert key in data, f"{lang}_text.json missing leaf key '{key}'"
        assert isinstance(data[key], str) and data[key].strip(), (
            f"{lang}_text.json has empty value for '{key}'"
        )


@pytest.mark.parametrize("lang", TEXT_LANGS)
def test_evolve_now_button_placeholder_contract(lang):
    # Mirrors Translator.translate(key, evo_name=...) -> template.format(...)
    template = _load(f"{lang}_text")["evolve_now_button"]
    assert "{evo_name}" in template, f"{lang}: evolve_now_button lost placeholder"
    rendered = template.format(evo_name="Ivysaur")
    assert "Ivysaur" in rendered
    assert "{evo_name}" not in rendered


def test_english_reference_values_exact():
    data = _load("en_text")
    for key, expected in EN_EXPECTED.items():
        assert data.get(key) == expected, (
            f"en_text.json['{key}'] = {data.get(key)!r}, expected {expected!r}"
        )


def test_nature_chart_button_is_localized():
    english = _load("en_text")["nature_chart_button"]
    for lang in TEXT_LANGS:
        value = _load(f"{lang}_text").get("nature_chart_button")
        assert isinstance(value, str) and value.strip(), lang
        if lang != "en":
            assert value != english, lang


@pytest.mark.parametrize("lang", TEXT_LANGS)
def test_main_guard_keys_preserved_in_every_language(lang):
    # GUARDS-TO-REAPPLY regression: pokemon_about_to_evolve_friendship and the
    # cp/bp label block were DONE-IN-BASE; the port must not have dropped them.
    data = _load(f"{lang}_text")
    for key in TEXT_GUARD_KEYS:
        assert key in data, f"{lang}_text.json lost guard key '{key}'"
        assert isinstance(data[key], str) and data[key].strip(), (
            f"{lang}_text.json has empty value for guard key '{key}'"
        )


def test_setting_guard_keys_preserved():
    # F01 does not touch setting_name/description; assert main's economy + time
    # evolution guard keys remain intact (GUARDS-TO-REAPPLY) and the two files
    # stay key-consistent (base test_settings_consistency invariant, Check 1).
    names = _load("setting_name")
    descs = _load("setting_description")
    assert set(names) == set(descs), "setting_name/description key sets diverged"
    for guard in (
        "trainer.cash_reward_amount",
        "trainer.cash_reward_interval",
        "evolution.friendship_time_enabled",
    ):
        assert guard in names and guard in descs, f"guard key '{guard}' dropped"
    # main's cash-reward economy text (daily 400 cap) must not be replaced by
    # exp's per-card 100 economy text.
    assert "400" in descs["trainer.cash_reward_amount"]
