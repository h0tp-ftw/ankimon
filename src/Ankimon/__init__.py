# -*- coding: utf-8 -*-

# Ankimon
# Copyright (C) 2024 Unlucky-Life

# This program is free software: you can redistribute it and/or modify
# by the Free Software Foundation
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# Important - If you redistribute it and/or modify this addon - must give contribution in Title and Code
# aswell as ask for permission to modify / redistribute this addon or the code itself

try:
    from .debug_console import show_ankimon_dev_console
except ModuleNotFoundError:
    pass

import aqt
from aqt import gui_hooks, mw
from aqt.gui_hooks import webview_will_set_content
from aqt.webview import WebContent

from .resources import ensure_ankimon_infrastructure, user_path, addon_dir

ensure_ankimon_infrastructure(addon_dir, user_path)

# Only the cheap, aqt-free core objects are imported here. The GUI windows
# (test_window, item_window, …) are F31 lazy factories: importing their names
# constructs them, so those imports are deferred into the async-boot success
# callback below and the boot never builds a window before Anki is up.
from .singletons import (
    settings_obj,
    logger,
    translator,
    ankimon_tracker_obj,
    shop_manager,
    trainer_card,
)
from .functions.url_functions import (
    open_team_builder,
    rate_addon_url,
    report_bug,
    join_discord_url,
    open_leaderboard_url,
)
from .functions.pokemon_showdown_functions import (
    export_to_pkmn_showdown,
    export_all_pkmn_showdown,
    flex_pokemon_collection,
)
from .utils import test_online_connectivity
from .menu_buttons import create_menu_actions
from .hooks import setupHooks
from .pyobj.error_handler import show_warning_with_traceback
from .pyobj.backup_manager import BackupManager
from .services import services
from .events import events

# LEADERBOARD CREDENTIALS MIGRATION
# Moved to build_core() in core.py - runs immediately after
# services.populate() and BEFORE any TrainerCard construction.
# This ensures migration completes before any sync can fire.

# singletons.py already populated the service registry and mirrored these onto
# mw (see services.py), so the previous mw.settings_ankimon/logger/settings_obj
# writes here were pure duplication and are gone. The translator write stays:
# importing menu_buttons (above) re-creates mw.translator at its module top
# (menu_buttons.py:45), so we re-point mw.translator back to the registry's
# instance to keep mw.translator identical to services.translator. Remove this
# once menu_buttons stops building its own translator.
mw.translator = translator

# Deck-browser / deck-overview team grid (F19). Registration is gated on the
# gui.team_deck_view setting and reload-safe (F31 registry-anchored record on
# services), so an add-on reload swaps the handlers instead of stacking them.
from .gui_classes.overview_team import register_overview_hooks

register_overview_hooks()

# --- Startup readiness flag (F32 async boot) ---
# Expressed on the services registry (not mw): reviews that arrive before the
# background boot finishes are dropped by the gated hook below, exactly like
# exp's mw.ankimon_startup_finished gate. Consumers read
# getattr(services, "startup_finished", False).
_STARTUP_FINISHED_ATTR = "startup_finished"
setattr(services, _STARTUP_FINISHED_ATTR, False)

# --- Web exports for reviewer UI ---
mw.addonManager.setWebExports(
    __name__, r"(web|user_files)/.*\.(css|js|jpg|gif|html|ttf|png|mp3)"
)


def on_webview_will_set_content(web_content: WebContent, context) -> None:
    if not isinstance(context, aqt.reviewer.Reviewer):
        return
    ankimon_package = mw.addonManager.addonFromModule(__name__)
    web_content.js.append(f"/_addons/{ankimon_package}/web/ankimon_hud_portal.js")


webview_will_set_content.append(on_webview_will_set_content)

# --- Card timer and answer hooks ---
from .card_hooks import register_card_hooks

register_card_hooks()

# Browser hooks for card suspension > unsuspension and leech tagged > leech tag removed detection
# Guarded so an import failure (e.g., aqt.browser in older Anki versions) cannot
# abort add-on load. All other integration points in this file are similarly
# guarded; this aligns with that pattern.
try:
    from .functions.browser_hooks import register_browser_hooks
    register_browser_hooks()
except Exception as e:
    # Log the error but continue loading - badge 11 browser hooks will be disabled
    try:
        logger.log("error", f"Failed to register browser hooks for Badge 11: {e}")
    except Exception:
        pass

setupHooks(None, ankimon_tracker_obj)

# --- Changelog check ---
online_connectivity = test_online_connectivity()
no_more_news = settings_obj.get("misc.YouShallNotPass_Ankimon_News")
ssh = settings_obj.get("misc.ssh")

from .changelog import (
    check_and_show_changelog,
    open_help_window,
    schedule_branch_update_check,
)

check_and_show_changelog(online_connectivity, ssh, no_more_news)
# Branch self-updater (F26): poll for new BRRRR_Experimental commits once the
# profile is open (gui_hooks seam), never as a module-level side effect.
schedule_branch_update_check(online_connectivity, ssh)

# --- Battle loop ---
from .battle_loop import on_review_card, init_battle_state


def _on_review_card_gated(*args, **kwargs):
    """Forward reviews to the battle loop only once the async boot finished.

    Until the background startup completes (first enemy generated, collected
    IDs loaded) a review cannot be battled; dropping it mirrors exp's
    startup-finished gate, re-expressed on the services registry.
    """
    if not getattr(services, _STARTUP_FINISHED_ATTR, False):
        return None
    return on_review_card(*args, **kwargs)


# Reload safety (NR-21, same pattern as card_hooks.register_card_hooks): the
# (hook, handler) record lives on the services registry — which survives a
# re-execution of this module — so a second boot removes the previous
# handler before appending, instead of stacking a duplicate.
_REVIEW_HOOK_RECORD = "_review_card_handlers"

for _hook, _handler in getattr(services, _REVIEW_HOOK_RECORD, ()):
    _hook.remove(_handler)
_review_handlers = ((gui_hooks.reviewer_did_answer_card, _on_review_card_gated),)
for _hook, _handler in _review_handlers:
    _hook.append(_handler)
setattr(services, _REVIEW_HOOK_RECORD, _review_handlers)

# --- Shared boot state -------------------------------------------------------
# Both are created eagerly (cheap) so the profile hooks can be registered at
# module scope — profileLoaded / profile_did_open can fire before the
# background boot completes, and a hook registered too late never runs. The
# collected-ID set is shared by identity: the async success callback fills it
# in place, so profile hooks, battle state and reviewer UI all observe the
# same live set.
backup_manager = BackupManager(logger, settings_obj)
collected_pokemon_ids = set()

# --- Hook registry + profile hooks ---
from .hook_registry import (
    CatchPokemonHook,
    DefeatPokemonHook,
    add_catch_pokemon_hook,
    add_defeat_pokemon_hook,
)

from .profile_hooks import register_profile_hooks

register_profile_hooks(
    online_connectivity,
    backup_manager,
    CatchPokemonHook,
    DefeatPokemonHook,
    add_catch_pokemon_hook,
    add_defeat_pokemon_hook,
    collected_pokemon_ids,
)


# --- Asynchronous startup (F32) ----------------------------------------------
def start_asynchronous_startup():
    """Run the heavy boot work off the GUI thread via Anki's QueryOp.

    ``run_startup_background_checks`` (aqt-free disk/DB/CPU work) executes on
    a background thread; ``on_startup_complete`` marshals its results back to
    the main thread for the Qt half of the boot.
    """
    from aqt.operations import QueryOp
    from .startup import run_startup_background_checks, run_startup_ui_callbacks

    services._startup_in_progress = True

    def clear_startup_lifecycle_flags():
        # Both flags gate the developer hot-reload: restart_ankimon() blocks on
        # _startup_in_progress before purging modules, and _is_reloading tells
        # run_startup_background_checks to skip backups for the reload's own
        # startup. Leaving either one set after a failed boot would hang the
        # next reload or silently suppress backups for the rest of the session,
        # so every exit path below runs this.
        services._startup_in_progress = False
        services._is_reloading = False

    def run_startup_ui_sequence(results):
        # 1. Qt half of the startup sequence (migration dialog, sprite
        #    downloader, first-enemy stat application, starter window, rate
        #    prompt).
        database_complete = run_startup_ui_callbacks(results)

        # 2. Fill the shared collected-ID set in place (identity preserved
        #    for the module-level consumers registered above).
        collected_pokemon_ids.update(results["collected_pokemon_ids"])
        init_battle_state(collected_pokemon_ids)

        # 3. Reviewer UI: collected IDs + shortcut/button wiring. Imported
        #    here (not at module scope) because reviewer_ui pulls lazy F31
        #    window names at ITS import time.
        from .reviewer_ui import setup_reviewer_ui, set_collected_ids

        set_collected_ids(collected_pokemon_ids)

        # 4. Menu. The window names are F31 lazy factories: first access
        #    constructs them — on the main thread, after the boot work.
        from .singletons import (
            settings_window,
            test_window,
            achievement_bag,
            ankimon_tracker_window,
            pokedex_window,
            eff_chart,
            gen_id_chart,
            nature_chart,
            license,
            credits,
            item_window,
            version_dialog,
            pokemon_pc,
        )

        create_menu_actions(
            database_complete,
            online_connectivity,
            item_window,
            test_window,
            achievement_bag,
            open_team_builder,
            export_to_pkmn_showdown,
            export_all_pkmn_showdown,
            flex_pokemon_collection,
            eff_chart,
            gen_id_chart,
            nature_chart,
            credits,
            license,
            open_help_window,
            report_bug,
            rate_addon_url,
            version_dialog,
            trainer_card,
            ankimon_tracker_window,
            logger,
            settings_window,
            shop_manager,
            pokedex_window,
            settings_obj.get("controls.key_for_opening_closing_ankimon"),
            join_discord_url,
            open_leaderboard_url,
            settings_obj,
            addon_dir,
            pokemon_pc,
            backup_manager,
        )

        # 5. Reviewer shortcuts/buttons (base signature; F34 adds its
        #    team-cycle argument as a defaulted kwarg on its own).
        setup_reviewer_ui(
            settings_obj.get("controls.catch_key"),
            settings_obj.get("controls.defeat_key"),
            settings_obj.get("controls.pokemon_buttons"),
        )

        # 5b. Developer hot-reload shortcut (Ctrl+Shift+R -> restart_ankimon).
        #     Wired here (startup-complete, main thread) rather than at module
        #     scope so mw is fully up. Reload-safe: the QShortcut handle is
        #     recorded on the services registry and deleted by
        #     reloader.teardown_ankimon before this callback re-runs on a reload,
        #     so a reload never stacks a second Ctrl+Shift+R binding. The
        #     dedicated QShortcut (not the menu action) owns the accelerator to
        #     avoid an "ambiguous shortcut overload" with the menu entry.
        from PyQt6.QtGui import QKeySequence, QShortcut
        from .reloader import restart_ankimon
        from .utils import is_dev_mode

        for _stale_sc in getattr(services, "_reload_shortcuts", ()) or ():
            try:
                _stale_sc.setEnabled(False)
                _stale_sc.deleteLater()
            except Exception:
                pass
        _reload_shortcut = QShortcut(QKeySequence("Ctrl+Shift+R"), mw)
        _reload_shortcut.activated.connect(restart_ankimon)
        # Developer-only accelerator: disable it for normal users so the hidden
        # hot-reload chord can't tear down the add-on by accident. Kept in lockstep
        # with the "Restart Ankimon" menu action by update_dev_actions_visibility().
        _reload_shortcut.setEnabled(is_dev_mode())
        services._reload_shortcuts = [_reload_shortcut]

        # 6. Boot finished: open the review gate and signal observers.
        setattr(services, _STARTUP_FINISHED_ATTR, True)
        events.emit("startup_finished")

        # 7. If the user is already reviewing, redraw the bottom bar so the
        #    freshly wrapped _bottomHTML (catch/defeat buttons) shows up.
        if getattr(mw, "state", None) == "review" and getattr(mw, "reviewer", None):
            try:
                mw.reviewer.bottom.draw()
            except Exception:
                pass

    def on_startup_complete(results):
        # QueryOp does not route an exception raised by its *success* callback
        # to .failure() — it propagates to Anki's top-level handler instead. So
        # the flag reset has to be a finally, or a single failing Qt-half step
        # (a raising migration dialog, a missing singleton) would strand the
        # lifecycle flags and wedge every later hot-reload.
        try:
            run_startup_ui_sequence(results)
        finally:
            clear_startup_lifecycle_flags()

    def on_startup_failed(exc):
        # QueryOp offers no automatic recovery: if the background half raises
        # (e.g. a locked/corrupt ankimon.db in one of the unguarded is_migrated /
        # load_collected_pokemon_ids / get_pokemon_count reads), on_startup_complete
        # never runs, so services.startup_finished stays False — every answered
        # card is silently dropped and the Ankimon menu is never built. Without a
        # .failure() handler that state is invisible to the user. Surface it.
        try:
            logger.log("error", f"Ankimon async startup failed: {exc}")
        except Exception:
            pass
        try:
            show_warning_with_traceback(
                parent=mw,
                exception=exc,
                message="Ankimon failed to start; its features are disabled for this session:",
            )
        except Exception:
            pass

        clear_startup_lifecycle_flags()

    try:
        QueryOp(
            parent=mw,
            op=lambda _col: run_startup_background_checks(backup_manager),
            success=on_startup_complete,
        ).failure(on_startup_failed).without_collection().run_in_background()
    except Exception:
        # The op never got scheduled, so neither callback will ever run. Clear
        # the flags here too, otherwise the next hot-reload waits on a startup
        # that will never finish.
        clear_startup_lifecycle_flags()
        raise


# --- Discord integration ---
from .discord_integration import setup_discord_hooks

setup_discord_hooks()

# Start the background boot last, once every module-scope hook is registered.
start_asynchronous_startup()
