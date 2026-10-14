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
        self.daily_items_reroll_cost = 100
        self.todays_daily_items = []
        self.todays_daily_tms = []
        self.tm_price = 1000

    @property
    def number_of_daily_items(self):
        trainer_level = int(self.settings_obj.get("trainer.level", 1))
        # 3 base slots + 1 for every 5 levels
        slots = 3 + (trainer_level // 5)
        return slots

    def get_daily_items(self):
        """Generate daily items based on the current date."""
        db = services.db
        shop_data = None
        if db:
            shop_data = db.get_user_data("todays_shop")

        target_slots = self.number_of_daily_items
        today = datetime.now().strftime("%Y-%m-%d")

        items = []
        if shop_data and shop_data.get("date") == today:
            items = shop_data.get("items", [])

        pool = get_daily_items_pool()
        num_items = min(target_slots, len(pool))

        if len(items) >= num_items:
            return items

        seed = today
        rng = random.Random(seed)

        if items:
            existing_names = {i["name"] for i in items}
            remaining_pool = [item for item in pool if item["name"] not in existing_names]
            needed = num_items - len(items)
            new_items = rng.sample(remaining_pool, needed)
            generated_items = items + new_items
        else:
            generated_items = rng.sample(pool, num_items)

        if not shop_data or shop_data.get("date") != today:
            shop_data = {"items": generated_items, "technical_machines": [], "date": today}
        else:
            shop_data["items"] = generated_items

        if db:
            db.set_user_data("todays_shop", shop_data)

        return generated_items

    def get_daily_tms(self):
        """Works like get_daily_items, but for TMs"""
        db = services.db
        shop_data = None
        if db:
            shop_data = db.get_user_data("todays_shop")

        target_slots = self.number_of_daily_items
        today = datetime.now().strftime("%Y-%m-%d")

        tms = []
        if shop_data and shop_data.get("date") == today:
            tms = shop_data.get("technical_machines", [])

        tm_pool = self.get_tm_pool()
        num_tms = min(target_slots, len(tm_pool))

        if len(tms) >= num_tms:
            return tms

        seed = today
        rng = random.Random(seed)

        if tms:
            existing_names = {t["name"] for t in tms}
            remaining_pool = [tm for tm in tm_pool if tm["name"] not in existing_names]
            needed = num_tms - len(tms)
            new_tms = rng.sample(remaining_pool, needed)
            generated_tms = tms + new_tms
        else:
            generated_tms = rng.sample(tm_pool, num_tms)

        if not shop_data or shop_data.get("date") != today:
            shop_data = {"items": [], "technical_machines": generated_tms, "date": today}
        else:
            shop_data["technical_machines"] = generated_tms

        if db:
            db.set_user_data("todays_shop", shop_data)

        return generated_tms

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
