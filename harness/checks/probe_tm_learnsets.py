"""Check TM resolution against the shipped form tables, without Anki/Qt."""
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
sys.modules["aqt"] = None
sys.modules["PyQt6"] = None

from harness.bootstrap import bootstrap

_PROFILE = tempfile.TemporaryDirectory(prefix="ankimon_tm_forms_")
bootstrap(_PROFILE.name)

from Ankimon.functions.tm_learnset import get_tm_learnset, _load_tm_learnsets_cache
from Ankimon.functions.pokedex_functions import _load_pokedex_cache, search_pokedex_by_id

# Independent correspondence checked against the bundled pokemon.csv identifiers
# and explicit Pokédex form/parent metadata. Never derive expected keys using the
# resolver under test: a base-species fallback can return plausible wrong moves.
FORM_TABLES = {
    "raticatealolatotem": "raticatetotemalola",
    "marowakalolatotem": "marowaktotem",
    "pikachuoriginal": "pikachuoriginalcap",
    "pikachuhoenn": "pikachuhoenncap",
    "pikachusinnoh": "pikachusinnohcap",
    "pikachuunova": "pikachuunovacap",
    "pikachukalos": "pikachukaloscap",
    "pikachualola": "pikachualolacap",
    "pikachupartner": "pikachupartnercap",
    "pikachuworld": "pikachuworldcap",
    "taurospaldeacombat": "taurospaldeacombatbreed",
    "taurospaldeablaze": "taurospaldeablazebreed",
    "taurospaldeaaqua": "taurospaldeaaquabreed",
    "darmanitangalar": "darmanitangalarstandard",
    "greninjabond": "greninjabattlebond",
    "rockruffdusk": "rockruffowntempo",
    "miniormeteor": "miniorredmeteor",
    "mimikyutotem": "mimikyutotemdisguised",
    "mimikyubustedtotem": "mimikyutotembusted",
    "necrozmaduskmane": "necrozmadusk",
    "necrozmadawnwings": "necrozmadawn",
    "ogerponwellspring": "ogerponwellspringmask",
    "ogerponhearthflame": "ogerponhearthflamemask",
    "ogerponcornerstone": "ogerponcornerstonemask",
    "ogerponwellspringtera": "ogerponwellspringmask",
    "ogerponhearthflametera": "ogerponhearthflamemask",
    "ogerponcornerstonetera": "ogerponcornerstonemask",
    "toxtricitygmax": "toxtricityamped",
    "toxtricitylowkeygmax": "toxtricitylowkey",
    "urshifugmax": "urshifusinglestrike",
    "urshifurapidstrikegmax": "urshifurapidstrike",
    "zygardemega": "zygarde50",
    "tatsugiricurlymega": "tatsugiricurly",
    "tatsugiridroopymega": "tatsugiridroopy",
    "tatsugiristretchymega": "tatsugiristretchy",
    "floettemega": "floetteeternal",
    "magearnaoriginalmega": "magearnaoriginal",
    "venusaurmega": "venusaurmega",
    "aegislash": "aegislashshield",
    "absolmegaz": "absol",
}


class TMLearnsetTests(unittest.TestCase):
    def test_explicit_forms_and_declared_parents_use_the_right_table(self):
        table = _load_tm_learnsets_cache()
        for name, expected in FORM_TABLES.items():
            with self.subTest(name=name):
                self.assertEqual(get_tm_learnset(name), table[expected])

    def test_every_bundled_species_except_non_tm_learners_resolves(self):
        missing = {name for name in _load_pokedex_cache() if not get_tm_learnset(name)}
        self.assertEqual(missing, {"ditto", "smeargle"})

    def test_ui_actual_id_cannot_teach_kantonian_moves_to_blaze_tauros(self):
        moves = get_tm_learnset(search_pokedex_by_id(10251))
        self.assertIn("willowisp", moves)
        self.assertIn("flareblitz", moves)
        self.assertNotIn("thunderbolt", moves)
        self.assertNotIn("surf", moves)

    def test_parent_cycles_terminate_and_still_try_the_base_species(self):
        from Ankimon.functions import tm_learnset
        metadata = {
            "testform": {"baseSpecies": "Charizard", "changesFrom": "OtherForm"},
            "otherform": {"baseSpecies": "Charizard", "battleOnly": "TestForm"},
        }
        original = tm_learnset.search_pokedex

        def lookup(name, field):
            name = name.lower()
            return metadata[name].get(field) if name in metadata else original(name, field)

        with patch.object(tm_learnset, "search_pokedex", lookup):
            self.assertEqual(get_tm_learnset("TestForm"), get_tm_learnset("Charizard"))

    def test_callers_cannot_mutate_the_cached_table(self):
        moves = get_tm_learnset("aegislash")
        moves.clear()
        self.assertIn("ironhead", get_tm_learnset("aegislash"))


if __name__ == "__main__":
    unittest.main()
