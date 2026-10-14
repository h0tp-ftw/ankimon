import pytest
from unittest.mock import MagicMock, patch
import sys
from Ankimon.pyobj.ankimon_shop import PokemonShopManager
import Ankimon.pyobj.ankimon_shop as shop_mod

def test_dynamic_shop_slots_regeneration():
    class MockSettings:
        def __init__(self, level):
            self.level = level
        def get(self, key, default=None):
            if key == "trainer.level":
                return self.level
            return default

    settings = MockSettings(1)

    with patch("Ankimon.pyobj.ankimon_shop.datetime") as mock_dt, \
         patch("Ankimon.pyobj.ankimon_shop.services") as mock_services:

        mock_dt.now.return_value.strftime.return_value = "2023-10-10"

        db_mock = MagicMock()
        mock_services.db = db_mock
        db_mock.get_user_data.return_value = None

        orig_pool = shop_mod.get_daily_items_pool
        shop_mod.get_daily_items_pool = lambda: [{"name": f"item{i}"} for i in range(100)]

        manager = PokemonShopManager(None, settings, lambda k, v: None, lambda k: None)
        manager.get_tm_pool = MagicMock(return_value=[{"name": f"tm{i}"} for i in range(100)])

        items_lvl1 = manager.get_daily_items()
        tms_lvl1 = manager.get_daily_tms()

        assert len(items_lvl1) == 3
        assert len(tms_lvl1) == 3

        db_mock.get_user_data.return_value = {
            "items": items_lvl1,
            "technical_machines": tms_lvl1,
            "date": "2023-10-10"
        }

        settings.level = 10
        assert manager.number_of_daily_items == 5

        items_lvl10 = manager.get_daily_items()
        tms_lvl10 = manager.get_daily_tms()

        assert len(items_lvl10) == 5
        assert len(tms_lvl10) == 5

        assert items_lvl10[:3] == items_lvl1
        assert tms_lvl10[:3] == tms_lvl1

        shop_mod.get_daily_items_pool = orig_pool
