from harness.headless_env import start_session
env = start_session()

from harness.driver import Driver
from harness.fixtures import build_pokemon
import pytest
import sys
from unittest.mock import MagicMock

def test_trade_held_item_returned(monkeypatch):
    driver = Driver()
    db = driver.env.services.db

    # 1. Setup: User has a Pokemon with a held item
    pkmn = build_pokemon({'id': 1, 'name': 'bulbasaur', 'level': 5, 'held_item': 'prism_scale'})
    db.save_pokemon(pkmn.to_dict())

    # Give the user the item by inserting it directly (update_item_quantity doesn't insert new items)
    db.execute("INSERT INTO items (item_name, quantity) VALUES (?, ?)", ("prism_scale", 1))

    sys.modules['aqt'] = MagicMock()
    sys.modules['aqt.utils'] = MagicMock()

    from Ankimon.pyobj.pokemon_trade import PokemonTrade

    # Create a dummy class that inherits from PokemonTrade but mocks out __init__
    class DummyTrade(PokemonTrade):
        def __init__(self, individual_id):
            self.individual_id = individual_id
            self.parent_window = None
            self.logger = driver.env.services.logger
            self.refresh_callback = lambda: None

    trade = DummyTrade(pkmn.individual_id)

    new_pkmn_stub = {
        "name": "charmander",
        "id": 4,
        "level": 5,
        "ability": "Blaze",
        "iv": pkmn.iv,
        "ev": pkmn.ev,
        "gender": pkmn.gender,
        "attacks": pkmn.attacks,
        "individual_id": "new-charmander-id",
        "shiny": False,
        "nature": "serious",
        "xp": 0,
        "tier": "Normal",
        "type": ["fire"],
        "stats": {"hp": 39, "atk": 52, "def": 43, "spa": 60, "spd": 50, "spe": 65},
        "growth_rate": "medium slow",
        "current_hp": 39,
        "base_experience": 62,
        "friendship": 0,
        "pokemon_defeated": 0,
        "everstone": False,
        "capture_date": "2026-10-01"
    }

    monkeypatch.setattr("Ankimon.pyobj.pokemon_trade.show_warning_with_traceback", lambda **kwargs: None)

    trade.replace_pokemon(new_pkmn_stub)

    # Verify: User no longer has the original bulbasaur
    assert db.get_pokemon(pkmn.individual_id) is None

    # Verify: User has the new charmander
    assert db.get_pokemon("new-charmander-id") is not None

    # Verify: prism_scale is back in the bag (starts at 1, goes to 2)
    items = {item["item_name"]: item["quantity"] for item in db.get_all_items()}
    assert items.get("prism_scale", 0) == 2
