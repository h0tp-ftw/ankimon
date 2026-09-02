from typing import Callable
from pathlib import Path


from aqt.utils import *
from aqt.qt import *
from PyQt6.QtWidgets import QMenu
from PyQt6.QtGui import QAction, QKeySequence
from aqt import mw  # The main window object
from aqt.utils import qconnect


from .gui_classes.check_files import FileCheckerApp
from .pyobj.download_sprites import show_agreement_and_download_dialog
from .pyobj.settings import Settings
from .pyobj.translator import Translator
from .pyobj.InfoLogger import ShowInfoLogger
from .pyobj.item_window import ItemWindow
from .pyobj.pc_box import PokemonPC
from .pyobj.trainer_card import TrainerCard
from .pyobj.settings_window import SettingsWindow
from .pyobj.test_window import TestWindow
from .pyobj.ankimon_shop import PokemonShopManager
from .pokedex.pokedex_obj import Pokedex
from .pyobj.achievement_window import AchievementWindow
from .pyobj.ankimon_tracker_window import AnkimonTrackerWindow
from .pyobj.backup_manager import BackupManager
from .gui_classes.backup_manager_dialog import BackupManagerDialog
from .gui_entities import (
    License,
    Credits,
    TableWidget,
    IDTableWidget,
    Version_Dialog,
    NatureTableWidget,
)

# Developer-mode gate (detects "dev_"/"_dev" in the profile or trainer name).
# Hides the developer-only menu entries (Switch Account / Encounter Rate
# Simulator) unless the user is in Developer Mode.
from .utils import is_dev_mode, is_alive


debug = True

# Registry key for the reload-teardown guard: create_menu_actions records the
# menu it adds to the menubar on `services` so a later boot can remove the prior
# one instead of stacking a duplicate 'Ankimon' menu.
_MENU_RECORD = "_ankimon_menubar_menu"

# Initialize the menu. Under a headless MockWindow (integrity / import smoke) mw
# is a lightweight stand-in, so build a DummyMock menu — importing this module
# then never needs a real Translator or QMenu. The real (production) path below
# is unchanged.
if "mock" in mw.__class__.__name__.lower():
    class DummyMock:
        def __getattr__(self, name):
            return DummyMock()

        def __call__(self, *args, **kwargs):
            return DummyMock()

    mw.translator = DummyMock()
    mw.pokemenu = DummyMock()
else:
    try:
        mw.translator = Translator(language=int(mw.settings_obj.get("misc.language")))
    except Exception:
        # If the configured language fails to load (missing/corrupt setting or
        # translation file), fall back to English (9) so mw.translator is always
        # set — otherwise the next line raises AttributeError and the whole menu
        # fails to build.
        mw.translator = Translator(language=9)
    mw.pokemenu = QMenu('&' + mw.translator.translate("ankimon_button_title"), mw)
game_menu = mw.pokemenu.addMenu(mw.translator.translate("ankimon_game_button_title"))
profile_menu = mw.pokemenu.addMenu(mw.translator.translate("ankimon_profile_button_title"))
collection_menu = mw.pokemenu.addMenu(mw.translator.translate("ankimon_collection_button_title"))
export_menu = mw.pokemenu.addMenu(mw.translator.translate("ankimon_export_button_title"))
help_menu = mw.pokemenu.addMenu(mw.translator.translate("ankimon_help_button_title"))
if debug is True:
    debug_menu = mw.pokemenu.addMenu(mw.translator.translate("ankimon_debug_button_title"))

def create_menu_actions(
    database_complete: bool,
    online_connectivity: bool,
    item_window: ItemWindow,
    test_window: TestWindow,
    achievement_bag: AchievementWindow,
    open_team_builder: Callable,
    export_to_pkmn_showdown: Callable,
    export_all_pkmn_showdown: Callable,
    flex_pokemon_collection: Callable,
    eff_chart: TableWidget,
    gen_id_chart: IDTableWidget,
    nature_chart: NatureTableWidget,
    credits: Credits,
    license: License,
    open_help_window: Callable,
    report_bug: Callable,
    rate_addon_url: Callable,
    version_dialog: Version_Dialog,
    trainer_card: TrainerCard,
    ankimon_tracker_window: AnkimonTrackerWindow,
    logger: ShowInfoLogger,
    settings_window: SettingsWindow,
    shop_manager: PokemonShopManager,
    pokedex_window: Pokedex,
    ankimon_key,
    join_discord_url: Callable,
    open_leaderboard_url: Callable,
    settings_obj: Settings,
    addon_dir: Path,
    pokemon_pc: PokemonPC,
    backup_manager: BackupManager,
):
    from .singletons import (
        get_items_window,
        get_nature_chart,
    )

    actions = []

    def _open_shell_at(screen, view=None, action=None):
        # Every web-shell screen (Items/Shop, Ankidex, Profile, Team, Settings,
        # Mobile) lives in the one unified shell window, resolved through the
        # singletons factory (never raw mw). `view` (Items only) forces the Mart
        # vs Bag filter; `action` (Profile only) is a one-shot UI hint — 'sprite'
        # opens the picker, 'badges' scrolls to the badge case. Both are consumed
        # by the next push. Only touch the profile action when actually opening
        # Profile, so opening another screen can't clobber a queued action (or
        # leave a stale one behind).
        w = get_items_window()
        if view is not None:
            w.pending_view = view
        if screen == "profile":
            w._pending_profile_action = action
        if w.isMinimized():
            w.showNormal()
        if w.current_screen != screen:
            w.load_screen(screen)
        elif (view is not None or action) and screen in w.ready_screens and w.isVisible():
            # Already on this fully-loaded screen — re-push so the requested
            # view/action applies immediately. Hidden/loading screens get it via
            # the showEvent / loadFinished push.
            w.push_screen_data()
        w.show()
        w.raise_()
        w.activateWindow()

    if database_complete:
        # Pokémon PC
        pokemon_pc_action = QAction("Pokémon PC", mw)
        pokemon_pc_action.setMenuRole(QAction.MenuRole.NoRole)
        collection_menu.addAction(pokemon_pc_action)

        def _open_pokemon_pc():
            if pokemon_pc.isMinimized():
                pokemon_pc.setWindowState(pokemon_pc.windowState() & ~Qt.WindowState.WindowMinimized)
            pokemon_pc.show()
            pokemon_pc.raise_()
            pokemon_pc.activateWindow()

        qconnect(pokemon_pc_action.triggered, _open_pokemon_pc)

        # Ankimon Window
        ankimon_window_action = QAction(mw.translator.translate("open_ankimon_window_button"), mw)
        ankimon_window_action.setMenuRole(QAction.MenuRole.NoRole)
        game_menu.addAction(ankimon_window_action)
        ankimon_window_action.setShortcut(QKeySequence(f"{ankimon_key}"))
        qconnect(ankimon_window_action.triggered, test_window.open_dynamic_window)

        # Item Bag — unified web shell, Items screen on the owned (Bag) view.
        itembag_action = QAction(mw.translator.translate("itembag_button"), mw)
        itembag_action.setMenuRole(QAction.MenuRole.NoRole)
        itembag_action.triggered.connect(lambda: _open_shell_at("items", "owned"))
        collection_menu.addAction(itembag_action)

        # Achievements — badge case section of the web Profile shell (the native
        # AchievementsDialog is superseded by the Profile screen).
        achievement_bag_action = QAction(mw.translator.translate("achievements_button"), mw)
        achievement_bag_action.setMenuRole(QAction.MenuRole.NoRole)
        achievement_bag_action.triggered.connect(
            lambda: _open_shell_at("profile", action="badges")
        )
        profile_menu.addAction(achievement_bag_action)

        # Showdown Teambuilder
        pokemon_showdown_action = QAction(mw.translator.translate("open_showdown_teambuilder_button"), mw)
        pokemon_showdown_action.setMenuRole(QAction.MenuRole.NoRole)
        qconnect(pokemon_showdown_action.triggered, open_team_builder)
        export_menu.addAction(pokemon_showdown_action)

        # Export to Showdown
        export_main_to_showdown = QAction(mw.translator.translate("export_main_pokemon_button"), mw)
        export_main_to_showdown.setMenuRole(QAction.MenuRole.NoRole)
        qconnect(export_main_to_showdown.triggered, export_to_pkmn_showdown)
        export_menu.addAction(export_main_to_showdown)

        export_all_to_showdown = QAction(mw.translator.translate("export_all_pokemon_button"), mw)
        export_all_to_showdown.setMenuRole(QAction.MenuRole.NoRole)
        qconnect(export_all_to_showdown.triggered, export_all_pkmn_showdown)
        export_menu.addAction(export_all_to_showdown)

        # Flexing Collection
        flex_pokecoll_action = QAction(mw.translator.translate("export_all_pokemon_to_pokepaste_button"), mw)
        flex_pokecoll_action.setMenuRole(QAction.MenuRole.NoRole)
        qconnect(flex_pokecoll_action.triggered, flex_pokemon_collection)
        export_menu.addAction(flex_pokecoll_action)

        # Ankidex (Pokédex V2) — unified web shell, ankidex screen.
        ankidex_action = QAction("Ankidex", mw)
        ankidex_action.setMenuRole(QAction.MenuRole.NoRole)
        qconnect(ankidex_action.triggered, lambda: _open_shell_at("ankidex"))
        collection_menu.addAction(ankidex_action)

    # Backup Manager
    backup_manager_action = QAction("Backup Manager", mw)
    backup_manager_action.setMenuRole(QAction.MenuRole.NoRole)
    backup_manager_action.triggered.connect(lambda: BackupManagerDialog(backup_manager, mw).exec())
    game_menu.addAction(backup_manager_action)

    # Effectiveness chart
    eff_chart_action = QAction(mw.translator.translate("eff_chart_button"), mw)
    eff_chart_action.setMenuRole(QAction.MenuRole.NoRole)
    eff_chart_action.triggered.connect(eff_chart.show_eff_chart)
    help_menu.addAction(eff_chart_action)

    # Generations and Pokémon chart
    gen_and_poke_chart_action = QAction(mw.translator.translate("gen_chart_button"), mw)
    gen_and_poke_chart_action.setMenuRole(QAction.MenuRole.NoRole)
    gen_and_poke_chart_action.triggered.connect(gen_id_chart.show_gen_chart)
    help_menu.addAction(gen_and_poke_chart_action)

    # Nature chart
    nature_chart_action = QAction(mw.translator.translate("nature_chart_button"), mw)
    nature_chart_action.setMenuRole(QAction.MenuRole.NoRole)
    nature_chart_action.triggered.connect(
        lambda: get_nature_chart().show_nature_chart()
    )
    help_menu.addAction(nature_chart_action)

    # Join Discord
    join_discord_action = QAction(mw.translator.translate("join_discord_button"), mw)
    join_discord_action.setMenuRole(QAction.MenuRole.NoRole)
    join_discord_action.triggered.connect(join_discord_url)
    help_menu.addAction(join_discord_action)

    # Open Ankimon Leaderboard
    open_leaderboard_action = QAction(("Ankimon Leaderboard"), mw)
    open_leaderboard_action.setMenuRole(QAction.MenuRole.NoRole)
    open_leaderboard_action.triggered.connect(open_leaderboard_url)
    game_menu.addAction(open_leaderboard_action)

    # Credits
    credits_action = QAction(mw.translator.translate("ankimon_credits_button"), mw)
    credits_action.setMenuRole(QAction.MenuRole.NoRole)
    credits_action.triggered.connect(credits.show_window)
    help_menu.addAction(credits_action)

    # About and License
    about_and_license_action = QAction(mw.translator.translate("ankimon_about_and_license_button"), mw)
    about_and_license_action.setMenuRole(QAction.MenuRole.NoRole)
    about_and_license_action.triggered.connect(license.show_window)
    help_menu.addAction(about_and_license_action)

    # Help Guide
    help_action = QAction(mw.translator.translate("open_help_guide_button"), mw)
    help_action.setMenuRole(QAction.MenuRole.NoRole)
    help_action.triggered.connect(lambda: open_help_window(online_connectivity))
    help_menu.addAction(help_action)

    # Report Bug
    report_bug_action = QAction(mw.translator.translate("report_bug_button"), mw)
    report_bug_action.setMenuRole(QAction.MenuRole.NoRole)
    report_bug_action.triggered.connect(report_bug)
    help_menu.addAction(report_bug_action)

    # Rate Addon
    rate_action = QAction(mw.translator.translate("rate_this_button"), mw)
    rate_action.setMenuRole(QAction.MenuRole.NoRole)
    rate_action.triggered.connect(rate_addon_url)
    mw.pokemenu.addAction(rate_action)

    # Update Ankimon. On a git checkout the file-overwrite updater is unsafe (it
    # would clobber the working tree), so offer a safe `git pull --ff-only`
    # instead; otherwise the normal download-based updater dialog.
    from .pyobj.update_manager import is_git_clone, git_pull_ff_only
    if is_git_clone():
        def _git_update():
            if not askUser(
                "Update Ankimon by fast-forwarding your git clone "
                "(git pull --ff-only)?\n\nThis stops safely without making any "
                "changes if you have local edits or commits.",
                title="Update Ankimon (git)",
            ):
                return

            from aqt.operations import QueryOp

            def on_done(result):
                ok, msg = result
                (showInfo if ok else showWarning)(msg)

            QueryOp(
                parent=mw, op=lambda col: git_pull_ff_only(), success=on_done
            ).without_collection().run_in_background()

        update_action = QAction(
            mw.translator.translate("ankimon_update_button") + " (git pull)", mw
        )
        update_action.setMenuRole(QAction.MenuRole.NoRole)
        update_action.triggered.connect(_git_update)
        help_menu.addAction(update_action)
    else:
        def _open_update_dialog():
            from .pyobj.update_dialog import UpdateDialog
            dialog = UpdateDialog(parent=mw)
            dialog.exec()

        update_action = QAction(mw.translator.translate("ankimon_update_button"), mw)
        update_action.setMenuRole(QAction.MenuRole.NoRole)
        update_action.triggered.connect(_open_update_dialog)
        help_menu.addAction(update_action)

    # Version
    version_action = QAction(mw.translator.translate("ankimon_version_button"), mw)
    version_action.setMenuRole(QAction.MenuRole.NoRole)
    version_action.triggered.connect(version_dialog.open)
    help_menu.addAction(version_action)

    # Settings — opens the unified web shell at the Settings screen. The legacy
    # SettingsWindow singleton stays available as a service for callers that use
    # it directly, but is no longer launched from the menu.
    config_action = QAction(mw.translator.translate("ankimon_settings_button"), mw)
    config_action.setMenuRole(QAction.MenuRole.NoRole)
    config_action.triggered.connect(lambda: _open_shell_at("settings"))
    # Show the Settings window
    mw.pokemenu.addAction(config_action)

    # --- Developer-only entries (hidden unless is_dev_mode()). ----------------
    # Switch Account toggles the active database (ankimon.db <-> ankimonDEV.db).
    from .singletons import swap_ankimon_account

    switch_account_action = QAction("Switch Account (DEV/Normal)", mw)
    switch_account_action.setMenuRole(QAction.MenuRole.NoRole)
    switch_account_action.triggered.connect(swap_ankimon_account)
    mw.pokemenu.addAction(switch_account_action)

    # Restart Ankimon — developer hot-reload (teardown + in-place re-import of
    # the add-on). The Ctrl+Shift+R accelerator is owned by a dedicated QShortcut
    # wired at startup (see __init__.py); this menu entry deliberately sets no
    # shortcut of its own to avoid an "ambiguous shortcut overload" with it.
    from .reloader import restart_ankimon

    restart_ankimon_action = QAction("Restart Ankimon", mw)
    restart_ankimon_action.setMenuRole(QAction.MenuRole.NoRole)
    restart_ankimon_action.triggered.connect(restart_ankimon)
    mw.pokemenu.addAction(restart_ankimon_action)

    # Encounter Rate Simulator (developer tool). Cache the dialog on mw (like the
    # file's other game/dev windows) before showing it: the dialog is parentless
    # and embeds a QWebEngineView, so if the only reference lived in the lambda it
    # would be dropped the moment the lambda returned and SIP would delete the
    # underlying C++ widget — the window would vanish or crash on open. Keeping a
    # Python reference on mw (rebuilt when its C++ object dies) keeps it alive.
    from .pyobj.encounter_simulator_dialog import EncounterSimulatorDialog

    def _open_encounter_simulator():
        if not is_alive(getattr(mw, "_encounter_simulator_dialog", None)):
            mw._encounter_simulator_dialog = EncounterSimulatorDialog(addon_dir)
        dlg = mw._encounter_simulator_dialog
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    simulator_action = QAction("Encounter Rate Simulator", mw)
    simulator_action.setMenuRole(QAction.MenuRole.NoRole)
    simulator_action.triggered.connect(_open_encounter_simulator)
    help_menu.addAction(simulator_action)

    # Ankimon Tracker window + its Ctrl+Shift+K hotkey live in the "Debug"
    # submenu. These are developer tools, so register them but gate them behind
    # Developer Mode below (like Switch Account / Simulator above) rather than
    # exposing them to every user.
    tracker_window_action = QAction(mw.translator.translate("ankimon_tracker_button"), mw)
    tracker_window_action.setMenuRole(QAction.MenuRole.NoRole)
    tracker_window_action.triggered.connect(ankimon_tracker_window.toggle_window)
    tracker_window_action.setShortcut(QKeySequence("Ctrl+Shift+K"))
    
    # Database Diagnostics (Verify & Repair)
    from .pyobj.db_diagnostics import trigger_database_diagnostics
    diagnostics_action = QAction("Verify and Repair Database", mw)
    diagnostics_action.setMenuRole(QAction.MenuRole.NoRole)
    diagnostics_action.triggered.connect(trigger_database_diagnostics)
    
    if debug is True:
        debug_menu.addAction(tracker_window_action)
        debug_menu.addAction(diagnostics_action)

    # Re-evaluate the developer-only entries each time the menu opens, so
    # toggling Developer Mode takes effect without rebuilding the menu.
    def update_dev_actions_visibility():
        is_dev = is_dev_mode()
        switch_account_action.setVisible(is_dev)
        restart_ankimon_action.setVisible(is_dev)
        simulator_action.setVisible(is_dev)
        # Hide the Debug submenu and disable the tracker hotkey unless Developer
        # Mode is on. setEnabled(False) is what actually suppresses the
        # Ctrl+Shift+K shortcut — an invisible-but-enabled QAction can still fire.
        tracker_window_action.setVisible(is_dev)
        tracker_window_action.setEnabled(is_dev)
        diagnostics_action.setVisible(True)
        if debug is True:
            debug_menu.menuAction().setVisible(True)
        # Keep the Ctrl+Shift+R hot-reload accelerator in lockstep with the menu
        # action (registered in __init__.on_startup_complete). Guard on liveness:
        # the shortcut list may be absent before startup-complete or hold a dead
        # widget after a reload.
        from .services import services
        for _sc in getattr(services, "_reload_shortcuts", ()) or ():
            if is_alive(_sc):
                _sc.setEnabled(is_dev)

    mw.pokemenu.aboutToShow.connect(update_dev_actions_visibility)
    update_dev_actions_visibility()

    # Set up a shortcut (Ctrl+Shift+L) to open the log window
    ankimon_logger_action = QAction(mw.translator.translate("logger_button"), mw)
    ankimon_logger_action.setMenuRole(QAction.MenuRole.NoRole)
    ankimon_logger_action.setShortcut(QKeySequence("Ctrl+Shift+L"))
    ankimon_logger_action.triggered.connect(logger.toggle_log_window)
    game_menu.addAction(ankimon_logger_action)

    # Trainer Card — opens the web Profile shell (Ctrl+Shift+Q).
    ankimon_trainer_card_action = QAction(mw.translator.translate("trainer_card_button"), mw)
    ankimon_trainer_card_action.setMenuRole(QAction.MenuRole.NoRole)
    ankimon_trainer_card_action.setShortcut(QKeySequence("Ctrl+Shift+Q"))
    ankimon_trainer_card_action.triggered.connect(lambda: _open_shell_at("profile"))
    profile_menu.addAction(ankimon_trainer_card_action)

    # Item Shop / Mart — same unified Items window as Item Bag, opened on the
    # shop ("In Shop Today") view rather than the bag.
    shop_manager_action = QAction(mw.translator.translate("item_shop_button"), mw)
    shop_manager_action.setMenuRole(QAction.MenuRole.NoRole)
    shop_manager_action.triggered.connect(lambda: _open_shell_at("items", "in_shop"))
    game_menu.addAction(shop_manager_action)

    # Choose Trainer Sprite — web Profile shell, sprite-picker action.
    choose_trainer_sprite_action = QAction(mw.translator.translate("choose_trainer_sprite_button"), mw)
    choose_trainer_sprite_action.setMenuRole(QAction.MenuRole.NoRole)
    choose_trainer_sprite_action.triggered.connect(
        lambda: _open_shell_at("profile", action="sprite")
    )
    game_menu.addAction(choose_trainer_sprite_action)

    # Choose Pokémon Team — web Team shell.
    pokemon_team_action = QAction(mw.translator.translate("choose_pokemon_team_button"), mw)
    pokemon_team_action.setMenuRole(QAction.MenuRole.NoRole)
    pokemon_team_action.triggered.connect(lambda: _open_shell_at("team"))
    game_menu.addAction(pokemon_team_action)

    # Mobile and Web Reviews — web shell, mobile screen. Assign to the module
    # global so update_mobile_badge() can reflect the pending count on it.
    global _mobile_battles_action
    mobile_battles_action = QAction("Mobile and Web Reviews", mw)
    mobile_battles_action.setMenuRole(QAction.MenuRole.NoRole)
    mobile_battles_action.triggered.connect(lambda: _open_shell_at("mobile"))
    game_menu.addAction(mobile_battles_action)
    _mobile_battles_action = mobile_battles_action

    # Restore the previous-session pending count now that the action exists. The
    # profile_did_open bootstrap calls update_mobile_badge() as it restores the
    # badge, but on a cold boot that fires (10ms Qt timer) before this menu is
    # built by the background-boot success callback — so its update was a no-op
    # and the count was dropped. Re-applying here makes the restore ordering-
    # independent: the badge is correct the instant the action is created.
    try:
        from .services import services

        update_mobile_badge(services.db.get_pending_mobile_count())
    except Exception:
        pass

    file_check_action = QAction(mw.translator.translate("ankimon_file_checker_button"), mw)
    file_check_action.setMenuRole(QAction.MenuRole.NoRole)
    file_check_action.triggered.connect(lambda: FileCheckerApp().exec())
    help_menu.addAction(file_check_action)

    # Leaderboard credentials moved to Settings → Leaderboard.
    downloader_action = QAction(mw.translator.translate("download_resources_button"), mw)
    downloader_action.setMenuRole(QAction.MenuRole.NoRole)
    downloader_action.triggered.connect(show_agreement_and_download_dialog)
    help_menu.addAction(downloader_action)

    # Reload-teardown guard (registry-anchored, same contract as card_hooks): a
    # module re-exec rebuilds mw.pokemenu at module scope, so drop any menu a
    # previous boot added to the menubar before adding the new one — otherwise
    # the 'Ankimon' menu stacks a duplicate on every reload.
    from .services import services

    _prev_menu = getattr(services, _MENU_RECORD, None)
    if _prev_menu is not None and is_alive(_prev_menu) and _prev_menu is not mw.pokemenu:
        try:
            mw.form.menubar.removeAction(_prev_menu.menuAction())
        except Exception:
            pass
    mw.form.menubar.addMenu(mw.pokemenu)
    setattr(services, _MENU_RECORD, mw.pokemenu)


# The "Mobile and Web Reviews" menu action is created by the later menu rewrite
# (F36). Until it exists this stays None, and update_mobile_badge() is a safe
# no-op — so the mobile sync/bootstrap paths can call it unconditionally now.
_mobile_battles_action = None


def update_mobile_badge(count: int) -> None:
    """Reflect the pending mobile-battle count on the Mobile & Web Reviews entry.

    Guarded no-op: returns immediately off the main thread or before the mobile
    menu action has been built (F36). Never raises — a badge update must never
    crash the sync hook or profile bootstrap that calls it.
    """
    try:
        from .utils import is_main_thread
        if not is_main_thread():
            return
        action = globals().get("_mobile_battles_action")
        if action is None:
            return
        if count and count > 0:
            action.setText(f"({count}) Mobile and Web Reviews")
        else:
            action.setText("Mobile and Web Reviews")
    except Exception:
        pass  # Never let a badge update crash anything
