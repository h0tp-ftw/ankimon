"""Daily Item Wishlist notifications for shop items."""

from datetime import datetime

from ..services import services


_HOOK_RECORD = "_item_wishlist_main_window_hook"
_DATE_KEY = "date_today"


def _collect_wishlist_shop_entries(shop_manager):
    """Return the set of shop item and TM names currently available today.

    Only string names are kept so that downstream set intersection and
    wishlist persistence operate on validated identifiers.
    """
    available_items = set()
    for entry in shop_manager.get_daily_items() or []:
        if isinstance(entry, dict) and isinstance(entry.get("name"), str):
            available_items.add(entry["name"])
    for entry in shop_manager.get_daily_tms() or []:
        if isinstance(entry, dict) and isinstance(entry.get("name"), str):
            available_items.add(entry["name"])
    return available_items


def _shop_stock_is_ready(shop_manager):
    """Report whether the daily shop pool has been populated.

    ``get_daily_items_pool()`` caches the result of ``daily_item_list()``. On a
    fresh install, sprites may not have been downloaded yet, so that first
    call can cache an empty pool. Treat an empty pool as "not ready" and
    return ``False`` so the caller can defer the notice and retry later
    instead of permanently suppressing it for the session.
    """
    items = shop_manager.get_daily_items() or []
    return bool(items)


def on_main_window_did_init(_main_window=None):
    """Notify once per local date when a wishlisted item is in today's shop."""
    db = services.db
    settings = services.settings
    if db is None or settings is None:
        return

    today = datetime.now().strftime("%d-%m-%y")
    try:
        if db.get_user_data(_DATE_KEY) == today:
            return

        from ..singletons import shop_manager

        if not _shop_stock_is_ready(shop_manager):
            return

        wishlist = settings.get("gui.item_wishlist", [])
        if isinstance(wishlist, (list, tuple, set)):
            available_items = _collect_wishlist_shop_entries(shop_manager)
            found = sorted(set(wishlist) & available_items)
            if found:
                services.ui.show_wishlist_popup(
                    f"{today}'s a great day! Something interesting rolled in the "
                    "shop today. Check it out!"
                )

        db.set_user_data(_DATE_KEY, today)
    except Exception as exc:
        logger = services.logger
        if logger is None:
            raise
        logger.log("error", f"Failed to check Item Wishlist shop items: {exc}")


def register_item_wishlist_hook():
    """Register one reload-safe check after Anki's main window is initialized."""
    from aqt import gui_hooks

    previous = getattr(services, _HOOK_RECORD, None)
    if previous is not None:
        try:
            gui_hooks.main_window_did_init.remove(previous)
        except ValueError:
            pass

    gui_hooks.main_window_did_init.append(on_main_window_did_init)
    setattr(services, _HOOK_RECORD, on_main_window_did_init)
