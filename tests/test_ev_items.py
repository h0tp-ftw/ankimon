from harness.headless_env import start_session
start_session()
from Ankimon.pyobj.pokemon_obj import PokemonObject

def test_pokemon_modify_ev():
    pkmn = PokemonObject(
        id=1, name="Bulbasaur", type=("grass", "poison"),
        level=5, shiny=False, ability="overgrow", gender="male",
        growth_rate="medium-slow", tier="normal", individual_id="test_id",
        captured_date="2023-01-01"
    )

    # Check baseline EV
    assert pkmn.ev["atk"] == 0

    # Add +10 EV
    success, msg = pkmn.modify_ev("atk", 10)
    assert success
    assert pkmn.ev["atk"] == 10

    # Try adding past 252 (242 + 10 = 252)
    pkmn.ev["atk"] = 245
    success, msg = pkmn.modify_ev("atk", 10)
    assert success
    assert pkmn.ev["atk"] == 252 # Should clamp at 252

    # Try adding over limit
    success, msg = pkmn.modify_ev("atk", 10)
    assert not success
    assert "cannot go any higher" in msg

    # Check total limit (510)
    pkmn.ev["hp"] = 252
    pkmn.ev["def"] = 6
    # Total is 252 + 252 + 6 = 510
    success, msg = pkmn.modify_ev("spa", 10)
    assert not success
    assert "overall base points cannot go any higher" in msg

    # Test reduction
    success, msg = pkmn.modify_ev("def", -10)
    assert success
    assert pkmn.ev["def"] == 0 # Clamped to 0

    success, msg = pkmn.modify_ev("def", -10)
    assert not success
    assert "cannot go any lower" in msg
