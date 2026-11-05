from harness.headless_env import start_session
env = start_session()

from harness.driver import Driver
d = Driver()
db = d.services.db

from Ankimon.pyobj.ankimon_shop import PokemonShopManager
manager = PokemonShopManager(None, d.services.settings, lambda k, v: None, lambda k: None)

d.services.settings.set("trainer.level", 1)

import datetime
from unittest.mock import patch
with patch("Ankimon.pyobj.ankimon_shop.datetime") as mock_dt:
    mock_dt.now.return_value.strftime.return_value = "2026-10-14"
    items = manager.get_daily_items()
    print([i["name"] for i in items])
