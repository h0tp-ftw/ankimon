from aqt import gui_hooks, mw

from .functions.discord_function import DiscordPresence
from .singletons import ankimon_tracker_obj, logger, settings_obj

CLIENT_ID = "1319014423876075541"
LARGE_IMAGE_URL = "https://raw.githubusercontent.com/Unlucky-Life/ankimon/refs/heads/main/src/Ankimon/ankimon_logo.png"


def setup_discord_hooks():
    if settings_obj.get("misc.discord_rich_presence") != True:
        return

    mw.ankimon_presence = DiscordPresence(
        CLIENT_ID, LARGE_IMAGE_URL, ankimon_tracker_obj, logger, settings_obj
    )

    def on_reviewer_initialized(rev, card, ease):
        if not hasattr(mw, "ankimon_presence") or not mw.ankimon_presence:
            mw.ankimon_presence = DiscordPresence(
                CLIENT_ID, LARGE_IMAGE_URL, ankimon_tracker_obj, logger, settings_obj
            )
        if mw.ankimon_presence.loop is False:
            mw.ankimon_presence.loop = True
            mw.ankimon_presence.start()

    def on_reviewer_will_end(*args):
        mw.ankimon_presence.loop = False
        mw.ankimon_presence.stop_presence()

    gui_hooks.reviewer_did_answer_card.append(on_reviewer_initialized)
    gui_hooks.reviewer_will_end.append(on_reviewer_will_end)
    gui_hooks.sync_did_finish.append(mw.ankimon_presence.stop)
