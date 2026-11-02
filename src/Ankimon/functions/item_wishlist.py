"""Daily Item Wishlist notifications for shop items."""

from datetime import datetime

from ..services import services


_HOOK_RECORD = "_item_wishlist_main_window_hook"
_PROFILE_HOOK_RECORD = "_item_wishlist_profile_hook"
_TIMER_RECORD = "_item_wishlist_rollover_timer"
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


def _run_wishlist_check_safe():
    """Wrapper so a QTimer timeout can call on_main_window_did_init without
    leaking exceptions into Qt's event loop."""
    try:
        on_main_window_did_init()
    except Exception as exc:
        logger = services.logger
        if logger is not None:
            logger.log("error", f"Item Wishlist daily check failed: {exc}")


def _install_daily_rollover_timer():
    """Fire the wishlist check shortly after local midnight, then daily.

    Reload-safe: tears down any previously installed timer stored on
    ``services`` before installing a new one.
    """
    try:
        from PyQt6.QtCore import QTimer
    except Exception:
        return

    previous = getattr(services, _TIMER_RECORD, None)
    if previous is not None:
        try:
            previous.stop()
            previous.deleteLater()
        except Exception:
            pass
        setattr(services, _TIMER_RECORD, None)

    now = datetime.now()
    next_midnight = now.replace(hour=0, minute=1, second=0, microsecond=0)
    if next_midnight <= now:
        from datetime import timedelta

        next_midnight += timedelta(days=1)
    delay_ms = max(1000, int((next_midnight - now).total_seconds() * 1000))

    timer = QTimer()
    timer.setSingleShot(True)

    def _on_fire():
        try:
            _run_wishlist_check_safe()
        finally:
            _install_daily_rollover_timer()

    timer.timeout.connect(_on_fire)
    timer.start(delay_ms)
    setattr(services, _TIMER_RECORD, timer)


def register_item_wishlist_hook():
    """Register reload-safe wishlist checks.

    - ``main_window_did_init`` covers app startup / fresh window.
    - ``profile_did_open`` covers profile switches.
    - A once-per-day ``QTimer`` covers the case where Anki stays open across
      midnight: the daily shop rolls over by date, but neither of the hooks
      above fires, so without the timer a matching item could go unnotified
      until the next restart. The persisted ``date_today`` guard makes the
      extra invocations no-ops on the same day.
    """
    from aqt import gui_hooks

    previous = getattr(services, _HOOK_RECORD, None)
    if previous is not None:
        try:
            gui_hooks.main_window_did_init.remove(previous)
        except ValueError:
            pass

    previous_profile = getattr(services, _PROFILE_HOOK_RECORD, None)
    if previous_profile is not None:
        try:
            gui_hooks.profile_did_open.remove(previous_profile)
        except (ValueError, AttributeError):
            pass

    gui_hooks.main_window_did_init.append(on_main_window_did_init)
    setattr(services, _HOOK_RECORD, on_main_window_did_init)

    try:
        gui_hooks.profile_did_open.append(on_main_window_did_init)
        setattr(services, _PROFILE_HOOK_RECORD, on_main_window_did_init)
    except AttributeError:
        pass

    _install_daily_rollover_timer()
