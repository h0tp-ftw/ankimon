"""Item Wishlist "Choose from Bag…" picker data (PR #1013).

The Item Wishlist control mirrors the Always-Catch Wishlist: a search box over
the whole wishlist pool, plus a picker grid. The Always-Catch grid lists caught
Pokémon; the item grid lists items the player owns (``bag_items``). Those ids
must be pool members — the save path drops anything outside the pool — and are
normalised to the pool's lowercase, hyphenated keys.
"""

from pathlib import Path
from unittest.mock import patch

from test_web_bag_trade_evolutions import shop_obj  # noqa: F401  (fixture)

_SETTINGS_JS = (
    Path(__file__).parent.parent / "src" / "Ankimon" / "ankimon_items_web" / "settings.js"
)


def _services(rows):
    class _DB:
        @staticmethod
        def get_all_items():
            if isinstance(rows, Exception):
                raise rows
            return rows

    class _Services:
        db = _DB

    return _Services


def _bag(shop_obj, rows, pool):  # noqa: F811
    with patch.object(shop_obj, "services", _services(rows)):
        return shop_obj.AnkimonItemsWeb._wishlist_bag_items(set(pool))


def test_bag_items_are_owned_pool_members_only(shop_obj):  # noqa: F811
    rows = [
        {"item_name": "linking-cord", "quantity": 2},
        {"item_name": "Ultra Ball", "quantity": 1},  # normalised to pool key
        {"item_name": "potion", "quantity": 0},  # used up — not in the bag
        {"item_name": "not-in-pool", "quantity": 5},  # unsaveable id
        {"item_name": "linking-cord", "quantity": 1},  # duplicate row
        {"item_name": None, "quantity": 3},
        {"item_name": "rare-candy", "quantity": "bad"},
    ]
    pool = {"linking-cord", "ultra-ball", "potion", "rare-candy"}
    assert _bag(shop_obj, rows, pool) == ["linking-cord", "ultra-ball"]


def test_bag_items_db_failure_is_an_empty_picker(shop_obj):  # noqa: F811
    assert _bag(shop_obj, RuntimeError("db closed"), {"potion"}) == []


def test_item_picker_reads_bag_not_whole_pool():
    js = _SETTINGS_JS.read_text(encoding="utf-8")
    assert "'Choose from Bag…'" in js
    assert "setting.bag_items" in js
    # The modal grid for the item wishlist is the bag, not every obtainable item.
    assert "? bagItems" in js
    assert "Choose from Items" not in js
