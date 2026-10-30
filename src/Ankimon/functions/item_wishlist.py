"""Daily Item Wishlist notifications for shop items."""

from datetime import datetime

from ..services import services


_HOOK_RECORD = "_item_wishlist_main_window_hook"
_DATE_KEY = "date_today"


def _item_assets_ready() -> bool:
    """True when item sprites are present on disk.

    Checked *before* touching the daily-shop pool: on a fresh install the
    pool's first build opens the sprite-download dialog, and we do not want
    to trigger that from a notification hook or commit an empty result as a
    completed check. A genuinely empty shop with assets present reads True
    and completes normally.
    """
    try:
        from ..resources import items_path

        return items_path.is_dir() and any(
            p.endswith(".png") for p in (x.name for x in items_path.iterdir())
        )
    except OSError:
        return False


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

        if not _item_assets_ready():
            return

        wishlist = settings.get("gui.item_wishlist", [])
        if isinstance(wishlist, (list, tuple, set)):
            from ..singletons import shop_manager

            available_items = {
                item.get("name")
                for item in (shop_manager.get_daily_items() or [])
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            }
            available_items.update(
                item.get("name")
                for item in (shop_manager.get_daily_tms() or [])
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            )
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
