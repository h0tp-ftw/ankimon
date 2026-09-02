import random
from datetime import datetime
import json

from ..services import services
from ..utils import daily_item_list
from ..resources import pokemon_tm_learnset_path

# Daily Rotating Items Pool, built on first use rather than at import time.
#
# `daily_item_list()` scans the sprites directory and, when it is missing, opens
# the sprite-download agreement dialog. Doing that at module scope made merely
# importing this module scan the disk and potentially pop a modal — and this
# module is imported by `singletons`, which `battle_loop` imports lazily from
# inside `on_review_card`. So the first card review of a fresh profile could
# surface a download dialog from inside a stats notification, and a headless
# run (the agent harness) aborted outright on the QWidget construction.
_DAILY_ITEMS_POOL = None


def get_daily_items_pool():
    """Return the daily-shop item pool, building it once on first call."""
    global _DAILY_ITEMS_POOL
    if _DAILY_ITEMS_POOL is None:
        _DAILY_ITEMS_POOL = daily_item_list() or []
    return _DAILY_ITEMS_POOL


class PokemonShopManager:
    """Daily Mart data provider.

    The retro ``QDialog`` storefront that used to live here (toggle_window /
    create_gui / _create_* / buy_item / reroll_daily_items + their theme helpers)
    was retired when the web shell took over the Mart UI: menu_buttons.py now
    routes the shop action to ``_open_shell_at("items", "in_shop")`` and
    ``ankimon_items_web/shop_obj.py`` reimplements buy/reroll against these data
    methods. Only the data surface the web shell reads remains — the daily
    item/TM pools plus the cash callbacks it uses.
    """

    def __init__(self, logger, settings_obj, set_callback, get_callback):
        self.logger = logger
        self.settings_obj = settings_obj
        self.set_callback = set_callback
        self.get_callback = get_callback
        self.number_of_daily_items = 3
        self.daily_items_reroll_cost = 100
        self.todays_daily_items = []
        self.todays_daily_tms = []
        self.tm_price = 1000

    def get_daily_items(self):
        """Generate daily items based on the current date."""
        db = services.db
        shop_data = db.get_user_data("todays_shop")
        if shop_data:
            if shop_data.get("items") and shop_data.get(
                "date"
            ) == datetime.now().strftime("%Y-%m-%d"):
                return shop_data.get("items")

        seed = datetime.now().strftime("%Y-%m-%d")
        rng = random.Random(seed)
        pool = get_daily_items_pool()
        num_items = min(self.number_of_daily_items, len(pool))
        return rng.sample(pool, num_items)

    def get_daily_tms(self):
        """Works like get_daily_items, but for TMs"""
        db = services.db
        shop_data = db.get_user_data("todays_shop")
        if shop_data:
            if shop_data.get("technical_machines") and shop_data.get(
                "date"
            ) == datetime.now().strftime("%Y-%m-%d"):
                return shop_data.get("technical_machines")

        tm_pool = self.get_tm_pool()
        seed = datetime.now().strftime("%Y-%m-%d")
        rng = random.Random(seed)
        num_tms = min(self.number_of_daily_items, len(tm_pool))
        return rng.sample(tm_pool, num_tms)

    def get_tm_pool(self) -> list[dict]:
        # Cached: pokemon_tm_learnset.json is immutable at runtime, and this
        # is on the hot path of the Items window's data fetch.
        cached = getattr(self, "_tm_pool_cache", None)
        if cached is not None:
            return cached

        with open(pokemon_tm_learnset_path, "r") as f:
            pokemon_tm_learnset = json.load(f)

        def flatten(xss):
            return [x for xs in xss for x in xs]

        all_tms = flatten(pokemon_tm_learnset.values())
        all_tms = set(all_tms)
        all_tms = list(
            {
                "name": tm,
                "description": f"Allows a Pokemon to be taught the move {tm} (if able).",
                "price": self.tm_price,
                "item_type": "TM",
            }
            for tm in all_tms
        )

        # Ensure deterministic order
        all_tms.sort(key=lambda tm: tm["name"])

        self._tm_pool_cache = all_tms
        return all_tms
