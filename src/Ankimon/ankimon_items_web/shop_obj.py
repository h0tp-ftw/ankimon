"""Unified shell window — Items (Mart + Bag) and Ankidex live in one QDialog.

The same QWebEngineView swaps between two screens by changing its URL. No
window close/open flicker; the dropdown switcher in either screen calls back
through QWebChannel to swap content in place.
"""

import json
import random
import math
import os
import time
import traceback
import threading
import base64
from datetime import datetime
from aqt import QDialog, QVBoxLayout, QWebEngineView, QWebEnginePage, mw
from aqt.qt import Qt, QUrl, QFrame, QWebEngineProfile
from PyQt6.QtCore import QObject, pyqtSlot, QTimer, QByteArray
from PyQt6.QtGui import QColor
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWidgets import QStackedWidget
import csv
from ..utils import give_item, is_alive
from ..pyobj.settings import DEFAULT_CONFIG, HUD_TOGGLE_AUTO_SYNC_KEYS

try:
    from ..utils import is_dev_mode
except ImportError:  # dev helper not landed yet (thread-reload-and-misc-utils unit)

    def is_dev_mode():
        return False


from ..resources import items_path, csv_file_items_cost, csv_file_descriptions


class SafeWebEnginePage(QWebEnginePage):
    def __init__(self, profile, screen_name, logger, parent=None):
        """Initialize a web engine page with a screen identifier and optional logger."""
        super().__init__(profile, parent)
        self.screen_name = screen_name
        self.logger = logger

    def javaScriptConsoleMessage(self, level, message, line, source):
        """
        Forward JavaScript console messages to the configured logger with a severity level and screen identifier.

        Parameters:
            level: JavaScript console message severity.
            message: Text emitted by JavaScript.
            line: Source line associated with the message.
            source: Source location associated with the message.
        """
        try:
            if self.logger:
                if (
                    level
                    == QWebEnginePage.JavaScriptConsoleMessageLevel.InfoMessageLevel
                ):
                    self.logger.log("info", f"[JS:{self.screen_name}] {message}")
                elif (
                    level
                    == QWebEnginePage.JavaScriptConsoleMessageLevel.WarningMessageLevel
                ):
                    self.logger.log("warning", f"[JS:{self.screen_name}] {message}")
                else:
                    self.logger.log("error", f"[JS:{self.screen_name}] {message}")
        except Exception:
            pass


from ..functions.pokedex_functions import (
    find_details_move,
    _ITEM_EVO_TRIGGERS,
    _load_pokedex_cache,
    check_evolution_by_item,
    evolution_gender_allows,
    return_id_for_item_name,
)
from ..business import calculate_cp_from_dict
from ..ankimon_profile_web.profile_data import ProfileData
from ..services import services
from ..events import events

# NOTE: functions/mobile_sync.py is a later (mobile) unit and is intentionally
# NOT a module-load dependency of the web-shell host. The MobileBridge slots
# lazy-import it in-method and degrade to a benign/neutral payload when it is
# absent, so the host, shop, settings, profile and team screens all work with
# mobile not yet installed.


def _local_pokemon_name(pokedex_id, english_fallback):
    """Localized species name for the current language, English fallback."""
    try:
        lang = int(services.settings.get("misc.language", 9))
        if lang != 9 and pokedex_id:
            from ..functions.pokedex_functions import get_pokemon_diff_lang_name

            loc = get_pokemon_diff_lang_name(int(pokedex_id), lang)
            if loc and loc != "No Translation in this language":
                return loc
    except Exception:
        pass
    return english_fallback


SCREEN_ITEMS = "items"
SCREEN_ANKIDEX = "ankidex"
SCREEN_SETTINGS = "settings"
SCREEN_PROFILE = "profile"
SCREEN_TEAM = "team"
SCREEN_MOBILE = "mobile"
SCREEN_HISTORY = "history"

SPRITE_VISIBILITY_SCREENS = (
    SCREEN_ITEMS,
    SCREEN_ANKIDEX,
    SCREEN_SETTINGS,
    SCREEN_PROFILE,
    SCREEN_TEAM,
)


class NavBridge(QObject):
    """Cross-screen navigation — exposed in all shell pages."""

    def __init__(self, window):
        super().__init__()
        self._w = window

    @pyqtSlot(result=int)
    def getPendingReviewsCount(self) -> int:
        try:
            db = services.db
            return db.get_pending_mobile_count() if db is not None else 0
        except Exception:
            return 0

    @pyqtSlot()
    def openItems(self):
        self._w.load_screen(SCREEN_ITEMS)

    @pyqtSlot()
    def openAnkidex(self):
        self._w.load_screen(SCREEN_ANKIDEX)

    @pyqtSlot()
    def openSettings(self):
        self._w.load_screen(SCREEN_SETTINGS)

    @pyqtSlot()
    def openProfile(self):
        self._w.load_screen(SCREEN_PROFILE)

    @pyqtSlot()
    def openTeam(self):
        self._w.load_screen(SCREEN_TEAM)

    @pyqtSlot()
    def openMobile(self):
        self._w.load_screen(SCREEN_MOBILE)

    @pyqtSlot()
    def openHistory(self):
        self._w.load_screen(SCREEN_HISTORY)


class TrainerBridge(QObject):
    """Profile-screen data + sprite-picker actions (delegates to ProfileData)."""

    def __init__(self, window):
        super().__init__()
        self._w = window

    @pyqtSlot(result="QVariant")
    def getProfile(self):
        return self._w.get_profile_payload()

    @pyqtSlot(result="QVariant")
    def getSprites(self):
        return self._w.profile_data.get_sprite_data()

    @pyqtSlot(str, result="QVariant")
    def setSprite(self, name):
        return self._w.profile_data.handle_set_sprite(name)

    @pyqtSlot(str, result="QVariant")
    def setName(self, name):
        return self._w.profile_data.handle_set_name(name)


class TeamBridge(QObject):
    """Team-builder screen actions (delegates to ProfileData)."""

    def __init__(self, window):
        super().__init__()
        self._w = window

    @pyqtSlot(result="QVariant")
    def getTeam(self):
        return self._w.profile_data.get_team_data()

    @pyqtSlot(result="QVariant")
    def getRoster(self):
        return self._w.profile_data.get_roster_data()

    @pyqtSlot(str)
    def saveSpriteMode(self, mode):
        services.settings.set("ankidex.spriteMode", mode)

    @pyqtSlot(int)
    def saveCycleCount(self, count):
        services.settings.set("controls.team_cycle_count", count)

    @pyqtSlot(str, result=int)
    def getCp(self, individual_id):
        return self._w.profile_data._calc_cp(individual_id)

    @pyqtSlot(str, result="QVariant")
    def getMemberStats(self, individual_id):
        # {cp, types} for a Pokémon just added to a slot (roster stubs omit both).
        return self._w.profile_data.get_member_stats(individual_id)

    # JSON string in (PyQt QVariant-list unwrap is unreliable on first call).
    @pyqtSlot(str, str, str, result="QVariant")
    def saveTeam(self, team_json, xp_share_id, companion_id):
        try:
            team_ids = json.loads(team_json) if team_json else []
            if not isinstance(team_ids, list):
                raise ValueError("team payload must be a list")
        except (TypeError, ValueError) as e:
            return {"ok": False, "message": f"Invalid team payload: {e}"}
        return self._w.profile_data.handle_save_team(
            team_ids, xp_share_id or None, companion_id or None
        )


class SettingsBridge(QObject):
    """Settings-screen actions — only meaningful when Settings is loaded."""

    def __init__(self, window):
        super().__init__()
        self._w = window

    @pyqtSlot(result="QVariant")
    def getSettings(self):
        return self._w.get_settings_data()

    # Accept a JSON-encoded string rather than a QVariant dict — PyQt's
    # QVariant → dict auto-unwrap can fail on the first invocation
    # (depending on Qt/PyQt versions), making the first save click error
    # out while later clicks succeed. Round-tripping through JSON removes
    # that ambiguity entirely.
    @pyqtSlot(str, result="QVariant")
    def saveSettings(self, payload_json):
        try:
            payload = json.loads(payload_json) if payload_json else {}
        except (TypeError, ValueError) as e:
            return {"ok": False, "message": f"Invalid payload JSON: {e}"}

        explicit_overrides = None
        if isinstance(payload, dict) and isinstance(payload.get("values"), dict):
            explicit_overrides = payload.get("explicit_hud_overrides")
            payload = payload["values"]
        return self._w.handle_save_settings(payload, explicit_overrides)

    @pyqtSlot(str, result="QVariant")
    def searchPokemon(self, query):
        """Return up to 20 Pokédex entries whose name contains `query`."""
        return self._w.handle_pokemon_search(query)

    @pyqtSlot(result="QVariant")
    def getCaughtPokemon(self):
        """Return list of [{id, name, sprite_url}] for all caught/collected Pokémon."""
        return self._w.handle_get_caught_pokemon()


class ItemsBridge(QObject):
    """Items-screen actions — only meaningful when Items is loaded."""

    def __init__(self, window):
        super().__init__()
        self._w = window

    @pyqtSlot(str, bool, result="QVariant")
    def buy(self, item_name, is_tm):
        result = self._w.handle_buy(item_name, bool(is_tm))
        self._w.push_screen_data()
        return result

    @pyqtSlot(result="QVariant")
    def reroll(self):
        result = self._w.handle_reroll()
        self._w.push_screen_data()
        return result

    @pyqtSlot(bool, result="QVariant")
    def setSkipRerollConfirm(self, skip):
        return self._w.handle_set_skip_reroll_confirm(bool(skip))

    @pyqtSlot(str, result="QVariant")
    def useItem(self, item_name):
        result = self._w.handle_use(item_name)
        self._w.push_screen_data()
        return result

    # In-shell Pokémon picker — replaces the legacy QInputDialog flow for
    # evolution items + held items. JS calls getPokemonChoices() to populate
    # the modal, then useItemOnPokemon() with the chosen individual_id.
    @pyqtSlot(str, result="QVariant")
    def getPokemonChoices(self, item_name=None):
        # Each picker (re)open requests a fresh roster: a catch/release/evolution
        # during reviews changes the captured-Pokémon set without pushing a screen
        # refresh, so drop the base cache here before rebuilding to avoid serving a
        # stale roster (JS keeps its own per-context cache for in-modal re-renders).
        self._w._pokemon_choices_cache = None
        return self._w.get_pokemon_choices(item_name)

    @pyqtSlot(str, str, result="QVariant")
    def useItemOnPokemon(self, item_name, individual_id):
        result = self._w.handle_use_with_target(item_name, individual_id)
        self._w.push_screen_data()
        return result

    @pyqtSlot(str, str, result="QVariant")
    def unequipItem(self, individual_id, item_name):
        result = self._w.handle_unequip_item(individual_id, item_name)
        self._w.push_screen_data()
        return result

    # Back-compat: items.shop.js previously called bridge.openAnkidex; keep
    # it as a passthrough so older cached pages still work.
    @pyqtSlot()
    def openAnkidex(self):
        self._w.load_screen(SCREEN_ANKIDEX)


class MobileBridge(QObject):
    """Mobile reviews screen — data and actions."""

    def __init__(self, window):
        super().__init__()
        self._w = window

    @pyqtSlot(result="QVariant")
    def getMobileStatus(self) -> dict:
        """
        Returns all data needed to render State 1 or State 2.
        Called by mobile.js on page load and after actions.
        """
        try:
            db = services.db
            # Language code (jp / sp / es_latam / en / ...) so the mobile battle
            # narration can localize itself; mirrors move_names._current_lang_code.
            try:
                from ..move_names import _current_lang_code
                mobile_language = _current_lang_code()
            except Exception:
                mobile_language = "en"
            # 1. Count and ease breakdown in one GROUP BY query (lightweight)
            rows = db.execute(
                """SELECT ease, COUNT(*) as cnt FROM pending_mobile_battles
                   WHERE resolved = 0 GROUP BY ease"""
            ).fetchall()
            pending_count = sum(r[1] for r in rows)

            # Read settings for cards_per_round
            from ..functions import mobile_sync

            settings_obj = services.settings
            cards_per_round, _ = mobile_sync._parse_cards_per_round(settings_obj)

            # Read cached resolved count to compute total battle count quickly.
            # Wrapped individually so an older/unmigrated DB lacking the metadata
            # table degrades to resolved_battles=0 instead of failing the whole
            # status load.
            resolved_battles = 0
            try:
                cursor = db.execute(
                    "SELECT value FROM metadata WHERE key = 'mobile_resolved_encounters_count'"
                )
                row = cursor.fetchone()
                resolved_battles = int(row[0]) if row else 0
            except Exception:
                pass
            battle_count = resolved_battles + math.ceil(pending_count / cards_per_round)

            if pending_count == 0:
                return {"pending_count": 0, "cap": 10000, "battle_count": 0, "language": mobile_language}

            # Populate ease breakdown from rows count
            ease_breakdown = {"1": 0, "2": 0, "3": 0, "4": 0}
            for row in rows:
                ease_breakdown[str(row[0])] = row[1]

            settings_obj = services.settings
            main_pokemon = services.main_pokemon
            trainer_card = services.trainer_card
            ankimon_tracker_obj = services.tracker

            # Get descriptive name for auto-battle setting
            auto_battle_mode_names = {
                0: "Manual (Auto-Resolve)",
                1: "Auto-Catch",
                2: "Auto-Defeat",
                3: "Catch Uncollected",
            }
            auto_battle_val = 0
            try:
                auto_battle_val = int(settings_obj.get("battle.automatic_battle", 0))
            except Exception:
                pass
            auto_battle_mode = auto_battle_mode_names.get(auto_battle_val, "Manual")

            rare_catch_active = False
            if settings_obj is not None:
                rare_catch_active = (
                    settings_obj.get("battle.auto_catch_legendary", True)
                    or settings_obj.get("battle.auto_catch_mythical", True)
                    or settings_obj.get("battle.auto_catch_ultra", True)
                    or settings_obj.get("battle.auto_catch_starter", True)
                    or settings_obj.get("battle.auto_catch_mega", True)
                    or settings_obj.get("battle.auto_catch_gmax", True)
                    or settings_obj.get("battle.auto_catch_regional", True)
                    or bool(settings_obj.get("battle.auto_catch_wishlist", []))
                )

            # Main Pokémon info for preview
            main_pokemon_name = None
            main_pokemon_level = None
            main_pokemon_sprite = None
            sprite_mode = "static"
            if main_pokemon is not None:
                main_pokemon_name = main_pokemon.name
                main_pokemon_level = main_pokemon.level

                from ..functions.sprite_functions import get_relative_sprite_path

                main_pokemon_sprite = get_relative_sprite_path(
                    main_pokemon.id,
                    bool(main_pokemon.shiny),
                    (main_pokemon.gender or "N"),
                    main_pokemon.name,
                    "gif",
                )

            if settings_obj is not None:
                sprite_mode = settings_obj.get(
                    "ankidex.spriteMode",
                    settings_obj.get("pokedex_v2.spriteMode", "static"),
                )

            # Trigger async estimates calculation if there are pending reviews
            estimates_loading = False
            estimates = {
                "xp": 0,
                "encounters": 0,
                "catches": 0,
                "caught_list": [],
                "is_truncated": False,
                "total_reviews": 0,
                "simulated_reviews": 0,
                "cash": 0,
            }
            if pending_count > 0:
                estimates_loading = True

                def run_sim(col):
                    reviews_rows_thread = db.execute(
                        """SELECT id, revlog_id, card_id, ease, review_time, review_type, queued_at
                           FROM pending_mobile_battles
                           WHERE resolved = 0
                           ORDER BY id ASC LIMIT 105"""
                    ).fetchall()
                    reviews_list_thread = [
                        {
                            "id": r[0],
                            "revlog_id": r[1],
                            "card_id": r[2],
                            "ease": r[3],
                            "review_time": r[4],
                            "review_type": r[5],
                            "queued_at": r[6],
                        }
                        for r in reviews_rows_thread
                    ]
                    if pending_count > len(reviews_list_thread):
                        reviews_list_thread.extend(
                            [{"ease": 3}] * (pending_count - len(reviews_list_thread))
                        )

                    from ..functions.mobile_sync import simulate_pending_mobile_battles

                    return simulate_pending_mobile_battles(
                        reviews_list_thread,
                        main_pokemon,
                        settings_obj,
                        trainer_card,
                        ankimon_tracker_obj,
                        ankimon_db=db,
                    )

                def on_sim_success(sim_res):
                    res_est = {
                        "xp": sim_res["xp"],
                        "encounters": sim_res["encounters"],
                        "catches": sim_res.get("catches_count", len(sim_res["caught"])),
                        "caught_list": sim_res["caught"],
                        "is_truncated": sim_res.get("is_truncated", False),
                        "total_reviews": sim_res.get("total_reviews", 0),
                        "simulated_reviews": sim_res.get("simulated_reviews", 0),
                        "cash": sim_res.get("cash", 0),
                    }
                    js = f"if (window.updateMobileEstimates) {{ window.updateMobileEstimates({json.dumps(res_est)}); }}"
                    self._w.webview_mobile.page().runJavaScript(js)

                # Test seam: under the suite aqt.operations is stubbed with a
                # no-op MagicMock, so QueryOp never delivers a result. Run the
                # simulation synchronously there and return the estimates inline;
                # production dispatches off-thread and pushes results via JS.
                if "PYTEST_CURRENT_TEST" in os.environ:
                    sim_res = run_sim(None)
                    estimates = {
                        "xp": sim_res["xp"],
                        "encounters": sim_res["encounters"],
                        "catches": sim_res.get("catches_count", len(sim_res["caught"])),
                        "caught_list": sim_res["caught"],
                        "is_truncated": sim_res.get("is_truncated", False),
                        "total_reviews": sim_res.get("total_reviews", 0),
                        "simulated_reviews": sim_res.get("simulated_reviews", 0),
                        "cash": sim_res.get("cash", 0),
                    }
                    estimates_loading = False
                    battle_count = estimates["encounters"]
                else:
                    from aqt.operations import QueryOp

                    QueryOp(
                        parent=self._w, op=run_sim, success=on_sim_success
                    ).without_collection().run_in_background()

            return {
                "pending_count": pending_count,
                "pending_count_at_start": pending_count,
                "cards_per_round": cards_per_round,
                "battle_count": battle_count,
                "cap": 10000,
                "ease_breakdown": ease_breakdown,
                "estimates": estimates,
                "estimates_loading": estimates_loading,
                "auto_battle_mode": auto_battle_mode,
                "rare_catch_active": rare_catch_active,
                "main_pokemon_name": main_pokemon_name,
                "main_pokemon_level": main_pokemon_level,
                "main_pokemon_sprite": main_pokemon_sprite,
                "sprite_mode": sprite_mode,
                "team_status": self.getTeamStatus(),
                "language": mobile_language,
            }
        except Exception as e:
            import traceback

            logger = services.logger
            if logger:
                logger.log(
                    "error", f"getMobileStatus failed: {e}\n{traceback.format_exc()}"
                )
            return {
                "error": str(e),
                "pending_count": 0,
                "pending_count_at_start": 0,
                "cap": 10000,
                "language": "en",
            }

    @pyqtSlot(result="QVariant")
    def getMobileHistory(self) -> list:
        """Retrieves mobile battle history."""
        try:
            return services.db.get_mobile_history(limit=500)
        except Exception as e:
            return []

    @pyqtSlot(result="QVariant")
    def clearMobileHistory(self) -> bool:
        """Clears mobile battle history."""
        try:
            return services.db.clear_mobile_history()
        except Exception as e:
            return False

    @pyqtSlot(result="QVariant")
    def dismissAll(self) -> dict:
        """
        Mark ALL pending battles as resolved without running any battle logic.
        This is the escape hatch for users who don't want to replay.
        """
        try:
            db = services.db
            count_before = db.get_pending_mobile_count()
            with db._get_connection() as conn:
                conn.execute(
                    "UPDATE pending_mobile_battles SET resolved=1, resolved_at=? WHERE resolved=0",
                    (int(time.time() * 1000),),
                )
            from ..menu_buttons import update_mobile_badge

            update_mobile_badge(0)
            return {"dismissed": count_before, "success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _run_resolve_all(self, limit=None):
        # Lazy import: mobile_sync is a later unit; callers translate its
        # absence into a benign error payload instead of crashing the shell.
        from ..functions.mobile_sync import resolve_all

        day_cutoff = mw.col.sched.day_cutoff if (mw and mw.col) else 0
        return resolve_all(
            db=services.db,
            settings_obj=services.settings,
            tracker=services.tracker,
            trainer_card=services.trainer_card,
            main_pokemon=services.main_pokemon,
            logger=services.logger,
            day_cutoff=day_cutoff,
            limit=limit,
        )

    @pyqtSlot(result="QVariant")
    def resolveAll(self) -> dict:
        """
        Runs the deterministic auto-resolve for all pending reviews, applying the exact same
        encounters and outcomes simulated in the preview.
        """
        try:
            return self._run_resolve_all()
        except Exception as e:
            import traceback

            logger = services.logger
            if logger:
                logger.log("error", f"resolveAll failed: {e}\n{traceback.format_exc()}")
            return {"success": False, "error": str(e)}

    @pyqtSlot(int, result="QVariant")
    def resolveChunk(self, limit: int) -> dict:
        """
        Resolves a chunk of pending battles up to the specified limit.
        """
        try:
            res = self._run_resolve_all(limit=limit)
            if isinstance(res, dict) and res.get("success"):
                res["done"] = services.db.get_pending_mobile_count() == 0
            return res
        except Exception as e:
            return {"success": False, "error": str(e)}

    @pyqtSlot()
    def startBulkResolve(self):
        """Starts bulk auto-resolve in a background thread."""
        # Count safely — the mobile DB layer may not be present (mobile is a
        # later unit); default to 0 rather than raising out of the slot.
        try:
            total = services.db.get_pending_mobile_count()
        except Exception:
            total = 0
        self._bulk_progress = {
            "processed": 0,
            "total": total,
            "resolved": 0,
            "catches": 0,
            "cash_gained": 0,
            "trainer_xp_gained": 0,
            "xp_gained": 0,
            "caught_list": [],
            "done": False,
            "error": None,
        }
        self._bulk_paused = False
        self._bulk_stopped = False
        self._bulk_refreshed = False
        self._bulk_last_yield = 0.0  # GIL-yield throttle clock (Bug 2)

        # Read the scheduler's day cutoff on the main GUI thread — mw.col.sched
        # must not be touched from the worker thread. Matches resolveAll /
        # resolveChunk / resolveNext, which all precompute it before dispatching.
        day_cutoff = mw.col.sched.day_cutoff if (mw and mw.col) else 0

        def bg_resolve():
            try:
                # Lazy import: absent mobile_sync surfaces as a benign progress
                # error (done=True) instead of crashing the worker thread.
                from ..functions.mobile_sync import resolve_all

                def progress_cb(status, total=None):
                    if isinstance(status, dict):
                        self._bulk_progress.update(status)
                    else:
                        self._bulk_progress["processed"] = status
                        if total is not None:
                            self._bulk_progress["total"] = total

                    import time

                    # GIL yield / rate limit (Bug 2). The per-review battle
                    # simulation is CPU-bound pure Python that holds the GIL; on a
                    # plain worker thread it starves the Qt GUI thread, so the
                    # progress bar froze then jumped and Pause/Stop registered late.
                    # Sleeping briefly hands the GIL to the GUI thread so it can
                    # repaint and process clicks. Time-gated (~3ms handed over per
                    # ~20ms of work => ~13% slower, smooth UI) rather than per call,
                    # so throughput barely drops. Only the background bulk-resolve
                    # passes a progress_callback, so the synchronous post-sync
                    # auto-resolve path is unaffected.
                    now = time.monotonic()
                    if now - getattr(self, "_bulk_last_yield", 0.0) >= 0.02:
                        time.sleep(0.003)
                        self._bulk_last_yield = time.monotonic()

                    while getattr(self, "_bulk_paused", False) and not getattr(
                        self, "_bulk_stopped", False
                    ):
                        time.sleep(0.1)

                    if getattr(self, "_bulk_stopped", False):
                        return False
                    return True

                res = resolve_all(
                    db=services.db,
                    settings_obj=services.settings,
                    tracker=services.tracker,
                    trainer_card=services.trainer_card,
                    main_pokemon=services.main_pokemon,
                    logger=services.logger,
                    day_cutoff=day_cutoff,
                    limit=None,
                    progress_callback=progress_cb,
                )
                if res:
                    if not res.get("success", True):
                        raise Exception(
                            res.get("error", "Unknown error in background resolve")
                        )
                    self._bulk_progress["processed"] = res.get("reviews_processed", 0)
                    self._bulk_progress["resolved"] = res.get("resolved", 0)
                    self._bulk_progress["catches"] = res.get("catches", 0)
                    self._bulk_progress["cash_gained"] = res.get("cash_gained", 0)
                    self._bulk_progress["trainer_xp_gained"] = res.get(
                        "trainer_xp_gained", 0
                    )
                    self._bulk_progress["xp_gained"] = res.get("xp_gained", 0)
                    if res.get("caught_list"):
                        self._bulk_progress["caught_list"] = res.get("caught_list")

            except Exception as e:
                import traceback

                logger = services.logger
                if logger:
                    logger.log(
                        "error", f"bg_resolve failed: {e}\n{traceback.format_exc()}"
                    )
                self._bulk_progress["error"] = f"{str(e)}\n{traceback.format_exc()}"
            finally:
                self._bulk_progress["done"] = True

        thread = threading.Thread(target=bg_resolve, daemon=True)
        thread.start()

    @pyqtSlot()
    def pauseBulkResolve(self):
        self._bulk_paused = True

    @pyqtSlot()
    def resumeBulkResolve(self):
        self._bulk_paused = False

    @pyqtSlot()
    def stopBulkResolve(self):
        self._bulk_stopped = True

    @pyqtSlot(result="QVariant")
    def getBulkResolveProgress(self) -> dict:
        progress = getattr(
            self, "_bulk_progress", {"done": True, "processed": 0, "total": 0}
        ).copy()
        progress["paused"] = getattr(self, "_bulk_paused", False)
        # If it just finished, perform safe main-thread refreshes!
        if progress.get("done") and not getattr(self, "_bulk_refreshed", False):
            self._bulk_refreshed = True
            try:
                # Refresh trainer card
                if services.trainer_card:
                    services.trainer_card.refresh()
                # Refresh active companion
                if services.main_pokemon:
                    from ..functions.update_main_pokemon import update_main_pokemon

                    update_main_pokemon(services.main_pokemon)
                # Update mobile badge safely on main thread
                try:
                    remaining = services.db.get_pending_mobile_count()
                    from ..menu_buttons import update_mobile_badge

                    update_mobile_badge(remaining)
                except Exception:
                    pass
                # Live-refresh whatever screen is showing (self._w IS the shell
                # window) and emit the seam signal for any cross-cutting
                # listeners — replaces exp's singletons.notify_stats_changed().
                self._w.refresh_live_screen()
                events.emit("stats_changed")
            except Exception as e:
                import traceback

                logger = services.logger
                if logger:
                    logger.log(
                        "error",
                        f"Error refreshing singletons after bulk resolve: {e}\n{traceback.format_exc()}",
                    )
        return progress

    @pyqtSlot(str, result="QVariant")
    def resolveNext(self, companion_id: str = "") -> dict:
        try:
            # Lazy import: mobile_sync is a later unit — benign error if absent.
            from ..functions.mobile_sync import resolve_next

            db = services.db
            settings_obj = services.settings
            tracker = services.tracker
            trainer_card = services.trainer_card
            main_pokemon = services.main_pokemon
            day_cutoff = mw.col.sched.day_cutoff if (mw and mw.col) else 0

            # Test seam: under the suite aqt.operations is stubbed with a no-op
            # MagicMock, so QueryOp never delivers a result. Resolve synchronously
            # there and return the outcome inline; production dispatches off-thread
            # and pushes the result to the view via JS.
            if "PYTEST_CURRENT_TEST" in os.environ:
                res = resolve_next(
                    companion_id=companion_id,
                    db=db,
                    settings_obj=settings_obj,
                    tracker=tracker,
                    trainer_card=trainer_card,
                    main_pokemon=main_pokemon,
                    logger=services.logger,
                    day_cutoff=day_cutoff,
                )
                if isinstance(res, dict) and "current_pending_outcome" in res:
                    outcome = res["current_pending_outcome"]
                    if outcome:
                        outcome.update(
                            {
                                "main_pokemon": main_pokemon,
                                "trainer_card": trainer_card,
                                "settings_obj": settings_obj,
                            }
                        )
                    self._current_pending_outcome = outcome
                    return res["result"]
                return res
            else:
                from aqt.operations import QueryOp

                def run_sim(col):
                    return resolve_next(
                        companion_id=companion_id,
                        db=db,
                        settings_obj=settings_obj,
                        tracker=tracker,
                        trainer_card=trainer_card,
                        main_pokemon=main_pokemon,
                        logger=services.logger,
                        day_cutoff=day_cutoff,
                    )

                def on_sim_success(sim_res):
                    if (
                        isinstance(sim_res, dict)
                        and "current_pending_outcome" in sim_res
                    ):
                        outcome = sim_res["current_pending_outcome"]
                        if outcome:
                            outcome.update(
                                {
                                    "main_pokemon": main_pokemon,
                                    "trainer_card": trainer_card,
                                    "settings_obj": settings_obj,
                                }
                            )
                        self._current_pending_outcome = outcome
                        result_data = sim_res["result"]
                    else:
                        result_data = sim_res

                    import json

                    js = f"if (window.onResolveNextReady) {{ window.onResolveNextReady({json.dumps(result_data)}); }}"
                    self._w.webview_mobile.page().runJavaScript(js)

                QueryOp(
                    parent=self._w, op=run_sim, success=on_sim_success
                ).without_collection().run_in_background()

                return {"loading": True}
        except Exception as e:
            import traceback

            logger = services.logger
            if logger:
                logger.log(
                    "error", f"resolveNext failed: {e}\n{traceback.format_exc()}"
                )
            return {"success": False, "error": str(e)}

    @pyqtSlot(str, result="QVariant")
    def commitReplayOutcome(self, choice: str) -> dict:
        try:
            # Lazy import: mobile_sync is a later unit — benign error if absent.
            from ..functions.mobile_sync import commit_replay_outcome

            db = services.db
            if db is None:
                return {"success": False, "error": "Database service is uninitialized."}
            settings_obj = services.settings
            trainer_card = services.trainer_card
            main_pokemon = services.main_pokemon
            achievements_dict = services.achievements
            logger = services.logger
            outcome_data = getattr(self, "_current_pending_outcome", None)

            res = commit_replay_outcome(
                choice=choice,
                outcome_data=outcome_data,
                db=db,
                settings_obj=settings_obj,
                trainer_card=trainer_card,
                main_pokemon=main_pokemon,
                achievements_dict=achievements_dict,
                logger=logger,
            )
            if res.get("success"):
                self._current_pending_outcome = None
            return res
        except Exception as e:
            import traceback

            logger = services.logger
            if logger:
                logger.log(
                    "error",
                    f"commitReplayOutcome failed: {e}\n{traceback.format_exc()}",
                )
            return {"success": False, "error": str(e)}

    @pyqtSlot(str, result="QVariant")
    def toggleMobileCompanion(self, individual_id: str) -> dict:
        """Toggle a team member in/out of mobile.inactive_companions. Returns updated inactive list."""
        try:
            settings_obj = services.settings
            inactive = settings_obj.get("mobile.inactive_companions", [])
            if not isinstance(inactive, list):
                inactive = []
            inactive = [str(x) for x in inactive]
            if individual_id in inactive:
                inactive.remove(individual_id)
            else:
                inactive.append(individual_id)
            settings_obj.set("mobile.inactive_companions", inactive)
            return {"inactive": inactive, "success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @pyqtSlot(result="QVariant")
    def getTeamStatus(self) -> dict:
        """Returns the current team with inactive flags for rendering the mobile team grid."""
        try:
            db = services.db
            team_rows = db.get_team()
            settings_obj = services.settings
            inactive = (
                set(settings_obj.get("mobile.inactive_companions", []))
                if settings_obj
                else set()
            )
            from ..functions.sprite_functions import get_relative_sprite_path

            team_list = []
            for t in team_rows:
                ind_id = t.get("individual_id")
                if ind_id:
                    data = db.get_pokemon(ind_id)
                    if data:
                        from ..pyobj.pokemon_obj import PokemonObject

                        pkmn = PokemonObject(**data)
                        name = pkmn.display_name
                        level = data.get("level", 5)
                        shiny = bool(data.get("shiny", False))
                        gender = data.get("gender") or "N"
                        pkmn_id = data.get("id")
                        pkmn_type = data.get("type", ["Normal"])
                        if isinstance(pkmn_type, str):
                            pkmn_type = [pkmn_type]

                        sprite_path = get_relative_sprite_path(
                            pkmn_id, shiny, gender, pkmn.name, "gif"
                        )
                        is_inactive = ind_id in inactive

                        team_list.append(
                            {
                                "individual_id": ind_id,
                                "name": name,
                                "level": level,
                                "sprite_path": sprite_path,
                                "type": pkmn_type,
                                "inactive": is_inactive,
                            }
                        )

            return {"team": team_list, "inactive": list(inactive)}
        except Exception as e:
            return {"team": [], "inactive": [], "error": str(e)}

    @pyqtSlot(result="QVariant")
    def triggerAnkiSync(self) -> dict:
        """
        Triggers Anki's built-in synchronization on the main window.
        """
        try:
            from aqt import mw

            if hasattr(mw, "onSync"):
                from aqt.qt import QTimer

                QTimer.singleShot(0, mw.onSync)
                return {"success": True}
            else:
                return {"success": False, "error": "mw.onSync not available"}
        except Exception as e:
            return {"success": False, "error": str(e)}


class AnkimonItemsWeb(QDialog):
    def __init__(
        self,
        addon_dir,
        shop_manager,
        item_window,
        ankimon_tracker,
        trainer_card=None,
        settings_obj=None,
        logger=None,
    ):
        """
        Initialize the persistent web-based Ankimon interface and its data bridges.

        Parameters:
            addon_dir: Directory containing the add-on resources.
            shop_manager: Manager for shop inventory and player currency.
            item_window: Legacy item-window integration used for item actions.
            ankimon_tracker: Tracker providing Ankimon gameplay state.
            trainer_card: Optional trainer-card data source.
            settings_obj: Optional settings manager.
            logger: Optional logger for JavaScript console messages.
        """
        super().__init__()
        self.addon_dir = addon_dir
        self.shop_manager = shop_manager
        self.item_window = item_window
        self.ankimon_tracker = ankimon_tracker

        # Instantiate isolated, private browser profile to prevent Anki
        # and other addons from injecting conflicting scripts/stylesheets
        self.profile = QWebEngineProfile()

        # Profile + Team are folded into this shell so all five screens share
        # one window and one dropdown. Their data lives in ProfileData.
        self.profile_data = ProfileData(addon_dir, trainer_card, settings_obj, logger)
        self._pending_profile_action = None
        # Live updates: map of screen -> bound method that pushes fresh data to
        # that screen. Only screens listed here react to gameplay events. To add
        # a new live screen (e.g. a Stats screen), add an entry here, a matching
        # _push_*_live method, and a window.liveRefreshX receiver in its JS.
        # See ankimon_items_web/LIVE_UPDATES.md.
        self._live_refreshers = {
            SCREEN_PROFILE: self._push_profile_live,
            SCREEN_MOBILE: self._push_mobile_live,
            SCREEN_HISTORY: self._push_history_live,
        }
        self._live_refresh_pending = False
        self.current_screen = None
        self.setWindowTitle("Ankimon")

        # Paint the shell dark from the first frame. The web views set their
        # own page background, but the surrounding QDialog/QFrame/QStackedWidget
        # would otherwise briefly show the light system palette while a screen
        # loads — a visible flash on open.
        self.setStyleSheet(
            "QDialog, QFrame, QStackedWidget { background-color: #0d1117; }"
        )

        # Disabled WA_TranslucentBackground to prevent heavy window-level repaint
        # flickering under Windows DWM when QWebEngineView re-composes or updates.
        # self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowMinimizeButtonHint
        )
        self.resize(1180, 720)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setLayout(layout)

        frame = QFrame()
        frame.setContentsMargins(0, 0, 0, 0)
        frame.setFrameStyle(QFrame.Shape.NoFrame)
        frame.setLayout(QVBoxLayout())
        frame.layout().setContentsMargins(0, 0, 0, 0)
        layout.addWidget(frame)

        self.stack = QStackedWidget()
        frame.layout().addWidget(self.stack)

        self.webview_items = QWebEngineView()
        self.webview_items.setPage(
            SafeWebEnginePage(self.profile, SCREEN_ITEMS, logger, self.webview_items)
        )

        self.webview_ankidex = QWebEngineView()
        self.webview_ankidex.setPage(
            SafeWebEnginePage(
                self.profile, SCREEN_ANKIDEX, logger, self.webview_ankidex
            )
        )

        self.webview_settings = QWebEngineView()
        self.webview_settings.setPage(
            SafeWebEnginePage(
                self.profile, SCREEN_SETTINGS, logger, self.webview_settings
            )
        )

        self.webview_profile = QWebEngineView()
        self.webview_profile.setPage(
            SafeWebEnginePage(
                self.profile, SCREEN_PROFILE, logger, self.webview_profile
            )
        )

        self.webview_team = QWebEngineView()
        self.webview_team.setPage(
            SafeWebEnginePage(self.profile, SCREEN_TEAM, logger, self.webview_team)
        )

        self.webview_mobile = QWebEngineView()
        self.webview_mobile.setPage(
            SafeWebEnginePage(self.profile, SCREEN_MOBILE, logger, self.webview_mobile)
        )

        self.webview_history = QWebEngineView()
        self.webview_history.setPage(
            SafeWebEnginePage(
                self.profile, SCREEN_HISTORY, logger, self.webview_history
            )
        )
        self._views = {
            SCREEN_ITEMS: self.webview_items,
            SCREEN_ANKIDEX: self.webview_ankidex,
            SCREEN_SETTINGS: self.webview_settings,
            SCREEN_PROFILE: self.webview_profile,
            SCREEN_TEAM: self.webview_team,
            SCREEN_MOBILE: self.webview_mobile,
            SCREEN_HISTORY: self.webview_history,
        }

        self.bridge = ItemsBridge(self)
        self.nav = NavBridge(self)
        self.settings_bridge = SettingsBridge(self)
        self.trainer_bridge = TrainerBridge(self)
        self.team_bridge = TeamBridge(self)
        self._mobile_bridge = MobileBridge(self)

        # Each screen gets its own channel, but every channel registers the
        # same bridge objects so any page can navigate / call any action.
        for screen, view in self._views.items():
            view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
            view.page().setBackgroundColor(QColor("#0d1117"))
            self.stack.addWidget(view)

            channel = QWebChannel(view)
            channel.registerObject("bridge", self.bridge)
            channel.registerObject("nav", self.nav)
            channel.registerObject("settings", self.settings_bridge)
            channel.registerObject("trainer", self.trainer_bridge)
            channel.registerObject("team", self.team_bridge)
            if screen in (SCREEN_MOBILE, SCREEN_HISTORY):
                channel.registerObject("mobile", self._mobile_bridge)
            view.page().setWebChannel(channel)

            view.loadFinished.connect(
                lambda ok, s=screen: self._on_screen_load_finished(ok, s)
            )

        self.loaded_screens = set()
        # Screens whose first load has actually *finished* (loaded_screens
        # only records that a load was kicked off). Used to decide whether a
        # forced view can be pushed immediately or must wait for loadFinished.
        self.ready_screens = set()
        # One-shot Items View filter ('in_shop' | 'owned') requested by a menu
        # entry (Mart vs Item Bag). Consumed by the next inventory push, then
        # cleared so later pushes (buy/use/reroll) don't reset the user's view.
        self.pending_view = None
        # The first show() is fed by the open path; later re-shows refresh data.
        self._shown_once = False

        # Boot with Items by default; menu entries can call load_screen()
        # before show() to pick a different initial screen.
        self.load_screen(SCREEN_ITEMS)
        self._restore_geometry()

    # ------------------------------------------------------------------
    # Screen switching
    # ------------------------------------------------------------------
    def load_screen(self, screen):
        def do_load():
            self.current_screen = screen
            if screen == SCREEN_ITEMS:
                title = "Ankimon — Items"
                target_view = self.webview_items
                path = self.addon_dir / "ankimon_items_web" / "shop.html"
            elif screen == SCREEN_ANKIDEX:
                title = "Ankimon — Ankidex"
                target_view = self.webview_ankidex
                path = self.addon_dir / "ankidex" / "ankidex.html"
            elif screen == SCREEN_SETTINGS:
                title = "Ankimon — Settings"
                target_view = self.webview_settings
                path = self.addon_dir / "ankimon_items_web" / "settings.html"
            elif screen == SCREEN_PROFILE:
                title = "Ankimon — Profile"
                target_view = self.webview_profile
                path = self.addon_dir / "ankimon_profile_web" / "profile.html"
            elif screen == SCREEN_TEAM:
                title = "Ankimon — Team"
                target_view = self.webview_team
                path = self.addon_dir / "ankimon_profile_web" / "team.html"
            elif screen == SCREEN_MOBILE:
                title = "Ankimon — Mobile Battles"
                target_view = self.webview_mobile
                path = self.addon_dir / "ankimon_mobile_web" / "mobile.html"
            elif screen == SCREEN_HISTORY:
                title = "Ankimon — Mobile History"
                target_view = self.webview_history
                path = self.addon_dir / "ankimon_mobile_web" / "history.html"
            else:
                return

            self.setWindowTitle(title)
            self.stack.setCurrentWidget(target_view)

            if screen not in self.loaded_screens:
                self.loaded_screens.add(screen)
                target_view.setUrl(QUrl.fromLocalFile(path.as_posix()))
            else:
                self.push_screen_data()

        # Save Ankidex prefs before navigating away
        if self.current_screen == SCREEN_ANKIDEX and screen != SCREEN_ANKIDEX:
            self._save_ankidex_prefs(callback=do_load)
        else:
            do_load()

    def _on_screen_load_finished(self, ok, screen):
        if not ok:
            return
        self.ready_screens.add(screen)
        settings_obj = (
            self.shop_manager.settings_obj if self.shop_manager is not None else None
        )
        if settings_obj is not None and screen in SPRITE_VISIBILITY_SCREENS:
            self._apply_sprite_visibility(
                settings_obj.get("gui.show_sprites_across_ankimon", True),
                screens=(screen,),
            )
        if self.current_screen == screen:
            self.push_screen_data()

    def _apply_sprite_visibility(self, show_sprites, screens=None):
        """Hide raster sprite content without collapsing the surrounding UI.

        The setting applies only to the intended non-battle shell screens.
        ``visibility`` preserves every image element's allocated size; removing
        raster CSS backgrounds leaves their sized containers intact.
        """
        requested_screens = screens or SPRITE_VISIBILITY_SCREENS
        targets = tuple(
            screen
            for screen in requested_screens
            if screen in SPRITE_VISIBILITY_SCREENS
        )
        enabled = "true" if show_sprites else "false"
        script = f"""
            (() => {{
                const show = {enabled};
                let style = document.getElementById('ankimon-sprite-visibility');
                if (!style) {{
                    style = document.createElement('style');
                    style.id = 'ankimon-sprite-visibility';
                    document.head.appendChild(style);
                }}
                document.querySelectorAll('*').forEach((element) => {{
                    const background = getComputedStyle(element).backgroundImage;
                    if (background.includes('.png') || background.includes('.gif')) {{
                        element.classList.add('ankimon-raster-background');
                    }}
                }});
                style.textContent = show ? '' : `
                    img[src*=".png"], img[src*=".gif"] {{ visibility: hidden !important; }}
                    [style*=".png"], [style*=".gif"] {{ background-image: none !important; }}
                    .ankimon-raster-background {{ background-image: none !important; }}
                `;
            }})()
        """
        for screen in targets:
            view = self._views.get(screen)
            if view is not None:
                view.page().runJavaScript(script)

    def push_screen_data(self):
        # A screen can only receive data once its first load has finished.
        # Pushing earlier is a no-op in JS and would wrongly consume one-shot
        # state like pending_view — the loadFinished handler re-pushes once
        # ready, so just skip here.
        if self.current_screen not in self.ready_screens:
            return
        # The persistent-singleton window can be reopened / switched back to after
        # the captured-Pokémon DB changed externally (a catch during reviews, a
        # release from the PC box). Drop the picker's roster cache on every screen
        # (re)entry so the next getPokemonChoices() rebuilds from fresh DB state.
        self._invalidate_pokemon_cache()
        if self.current_screen == SCREEN_ITEMS:
            data = self.get_inventory_data()
            # Apply a menu-requested View filter exactly once, atomically with
            # the render so it survives the async page load. Cleared after use
            # so subsequent pushes don't clobber the user's own filter choice.
            if self.pending_view is not None:
                data["initial_view"] = self.pending_view
                self.pending_view = None
            js = f"if (window.initializeItems) window.initializeItems({json.dumps(data)});"
            self.webview_items.page().runJavaScript(js)
        elif self.current_screen == SCREEN_ANKIDEX:
            data = self._get_ankidex_data()
            js = f"if (window.initializeAnkidex) window.initializeAnkidex({json.dumps(data)});"
            self.webview_ankidex.page().runJavaScript(js)
        elif self.current_screen == SCREEN_SETTINGS:
            data = self.get_settings_data()
            js = f"if (window.initializeSettings) window.initializeSettings({json.dumps(data)});"
            self.webview_settings.page().runJavaScript(js)
        elif self.current_screen == SCREEN_PROFILE:
            data = self.get_profile_payload()
            js = f"if (window.initializeProfile) window.initializeProfile({json.dumps(data)});"
            self.webview_profile.page().runJavaScript(js)
        elif self.current_screen == SCREEN_TEAM:
            data = self.profile_data.get_team_data()
            js = (
                f"if (window.initializeTeam) window.initializeTeam({json.dumps(data)});"
            )
            self.webview_team.page().runJavaScript(js)
        elif self.current_screen == SCREEN_MOBILE:
            data = self._mobile_bridge.getMobileStatus()
            js = f"if (window.initializeMobile) window.initializeMobile({json.dumps(data)});"
            self.webview_mobile.page().runJavaScript(js)
        elif self.current_screen == SCREEN_HISTORY:
            db = services.db
            data = db.get_mobile_history() if db is not None else []
            js = f"if (window.initializeHistory) window.initializeHistory({json.dumps(data)});"
            self.webview_history.page().runJavaScript(js)

    def get_profile_payload(self):
        """Profile data + a one-shot UI action ('sprite' opens the picker,
        'badges' scrolls to the badge case), set by the menu entry points."""
        data = self.profile_data.get_profile_data()
        data["action"] = self._consume_profile_action()
        return data

    def _consume_profile_action(self):
        action = self._pending_profile_action
        self._pending_profile_action = None
        return action

    # ------------------------------------------------------------------
    # Live updates — keep the open screen current after a gameplay event
    # (catch, XP, cash, ...). Full pattern + how to add a new live screen:
    # ankimon_items_web/LIVE_UPDATES.md
    # ------------------------------------------------------------------
    def refresh_live_screen(self):
        """Refresh whichever screen is currently showing after a gameplay event,
        **iff** it supports live updates.

        Today the only in-process caller is ``getBulkResolveProgress`` (after a
        bulk mobile auto-resolve). This is also the intended entry point for a
        future ``singletons.notify_stats_changed()`` bridge that would fan real
        catch / XP / cash write-sites here — that bridge is a deferred seam (see
        ``singletons.py`` and ``LIVE_UPDATES.md``) and is NOT wired yet, so
        gameplay outside the bulk-resolve path does not live-refresh a shell.

        Cheap and safe to call from anywhere on the GUI thread: a no-op unless
        the window is visible, the current screen is fully loaded, and that
        screen has a registered refresher. Several calls in the same event-loop
        turn coalesce into a single refresh (so e.g. a defeat that grants XP and
        a cash reward only triggers one re-render)."""
        # A gameplay event may have added/removed/evolved a captured Pokémon, so
        # the "Give Item"/"Evolve" picker's roster cache is now stale. Drop it up
        # front — before the visibility/screen early-returns — so the cache is
        # correct even when the event fires while the window is on a non-live
        # screen (e.g. Items) or hidden.
        self._invalidate_pokemon_cache()
        if not self.isVisible():
            return
        if self.current_screen not in self.ready_screens:
            return
        if self.current_screen not in self._live_refreshers:
            return
        if self._live_refresh_pending:
            return
        self._live_refresh_pending = True
        # Defer to the next event-loop turn: coalesces bursts and lets the
        # triggering gameplay logic finish (DB writes are already committed).
        QTimer.singleShot(0, self._run_live_refresh)

    def _run_live_refresh(self):
        self._live_refresh_pending = False
        # Re-check — state may have changed before this deferred call ran.
        # isVisible() raises RuntimeError if the dialog's C++ object was deleted
        # (window closed) between scheduling this timer and it firing.
        try:
            if not self.isVisible() or self.current_screen not in self.ready_screens:
                return
        except RuntimeError:
            return

        # Update the navigation switcher notification dot in the active web view
        active_view = self.stack.currentWidget()
        if active_view:
            try:
                db = services.db
                count = db.get_pending_mobile_count() if db is not None else 0
                active_view.page().runJavaScript(
                    f"if (window.updateNavSwitcherUnresolvedCount) window.updateNavSwitcherUnresolvedCount({count});"
                )
            except Exception:
                pass

        refresher = self._live_refreshers.get(self.current_screen)
        if refresher is None:
            return
        try:
            refresher()
        except Exception as e:
            logger = services.logger
            if logger:
                logger.log(
                    "error",
                    f"[Ankimon] live refresh failed ({self.current_screen}): {e}",
                )

    def _push_profile_live(self):
        """Push a full Profile refresh (cash, caught, Pokédex, shinies, highest,
        XP bar, team levels, recently caught). New catches animate into Recently
        Caught; the JS diffs the list so stat-only changes don't re-render it."""
        data = self.profile_data.get_profile_data()
        js = (
            "if (window.liveRefreshProfile) "
            f"window.liveRefreshProfile({json.dumps(data)});"
        )
        self.webview_profile.page().runJavaScript(js)

    def _push_mobile_live(self):
        """Push a full Mobile reviews refresh when stats or pending reviews change."""
        data = self._mobile_bridge.getMobileStatus()
        js = (
            "if (window.liveRefreshMobile) "
            f"window.liveRefreshMobile({json.dumps(data)});"
        )
        self.webview_mobile.page().runJavaScript(js)

    def _push_history_live(self):
        """Push a history refresh when a mobile review outcome is committed."""
        db = services.db
        history_data = db.get_mobile_history() if db is not None else []
        js = (
            "if (window.liveRefreshHistory) "
            f"window.liveRefreshHistory({json.dumps(history_data)});"
        )
        self.webview_history.page().runJavaScript(js)

    def _get_ankidex_data(self):
        # Reuse the existing Ankidex singleton's data getter — keeps the
        # dex query logic in one place.
        from ..singletons import get_ankidex_window

        ankidex = get_ankidex_window()
        return ankidex.get_ankidex_data()

    def _save_ankidex_prefs(self, callback=None):
        def on_state_ready(state):
            if state and isinstance(state, dict):
                for key, val in state.items():
                    services.settings.set(f"ankidex.{key}", val)
            if callback:
                callback()

        self.webview_ankidex.page().runJavaScript(
            "if (window.getAnkidexState) window.getAnkidexState();",
            on_state_ready,
        )

    def show(self):
        if self.isMinimized():
            self.showNormal()
        else:
            super().show()
        self.raise_()
        self.activateWindow()

    def _restore_geometry(self):
        try:
            geo = mw.pm.profile.get("ankimon.items_web_window.geometry")
            if geo:
                self.restoreGeometry(QByteArray(base64.b64decode(geo)))
        except Exception:
            pass

    def _save_geometry(self):
        try:
            if not self.isMinimized():
                mw.pm.profile["ankimon.items_web_window.geometry"] = base64.b64encode(
                    bytes(self.saveGeometry())
                ).decode()
        except Exception:
            pass

    def closeEvent(self, event):
        if self.current_screen == SCREEN_ANKIDEX:
            self._save_ankidex_prefs()
        self._save_geometry()
        super().closeEvent(event)

    def hideEvent(self, event):
        self._save_geometry()
        super().hideEvent(event)

    def showEvent(self, event):
        # The first show is fed by the open path (which pushes once the page
        # is ready), so skip the redundant push here — that double render
        # during load is what caused the flash. On later re-shows, refresh in
        # case buy/use changed data while the window was hidden.
        if self._shown_once:
            self.push_screen_data()
        else:
            self._shown_once = True
        super().showEvent(event)

    # Back-compat alias for the bridge methods that still call update_ui_data.
    def update_ui_data(self):
        self.push_screen_data()

    def handle_pokemon_search(self, query: str):
        """Search the Pokédex by name substring. Returns {results: [{id, name}]}."""
        from ..functions.pokedex_functions import _load_pokedex_cache, format_lore_name
        from ..functions import encounter_data

        query = (query or "").strip().lower()
        if len(query) < 2:
            return {"results": []}
        pokedex = _load_pokedex_cache()
        results = []
        for internal_name, data in pokedex.items():
            # Exclude alternate sub-forms of plate/drive/memory switching species to avoid redundancy
            if internal_name.startswith("arceus") and internal_name != "arceus":
                continue
            if internal_name.startswith("silvally") and internal_name != "silvally":
                continue
            if internal_name.startswith("genesect") and internal_name != "genesect":
                continue

            name = data.get("name", internal_name)
            pretty_name = format_lore_name(name)
            if query in name.lower() or query in pretty_name.lower():
                pid = data.get("actual_id") or data.get("species_id")
                if pid and int(pid) > 0:
                    pid_val = int(pid)
                    if pid_val not in encounter_data.UNAVAILABLE:
                        results.append(
                            {"id": pid_val, "name": _local_pokemon_name(pid_val, pretty_name)}
                        )
            if len(results) >= 20:
                break
        results.sort(key=lambda r: r["name"].lower())
        return {"results": results}

    def handle_get_caught_pokemon(self):
        """Get the list of caught/collected Pokémon for the quick-add panel."""
        from ..utils import load_collected_pokemon_ids
        from ..functions.pokedex_functions import (
            _load_pokedex_cache,
            search_pokedex_by_id,
            get_pretty_name_for_id,
        )

        caught_ids = load_collected_pokemon_ids()
        results = []
        pokedex = _load_pokedex_cache()

        for pid in sorted(list(caught_ids)):
            internal_name = search_pokedex_by_id(pid)
            if internal_name and internal_name != "Pokémon not found":
                pretty_name = get_pretty_name_for_id(pid)
                results.append(
                    {
                        "id": int(pid),
                        "name": _local_pokemon_name(int(pid), pretty_name),
                    }
                )
        # Sort by name alphabetically
        results.sort(key=lambda r: r["name"].lower())
        return {"results": results}

    def get_inventory_data(self):
        sm = self.shop_manager
        if sm is None:
            # Reload-safe: shop_manager can be unset during early boot / a
            # profile swap. Serve an empty-but-valid payload (same keys the
            # normal return builds) so shop.js still renders.
            return {
                "cash": 0,
                "reroll_cost": 0,
                "skip_reroll_confirm": False,
                "items": [],
            }

        # Today's stock (cached by PokemonShopManager.get_daily_items)
        raw_items = sm.get_daily_items() or []
        raw_tms = sm.get_daily_tms() or []
        sm.todays_daily_items = raw_items
        sm.todays_daily_tms = raw_tms

        shop_index = {}
        for entry in raw_items:
            shop_index[entry["name"]] = {
                "price": int(self._lookup_price(entry["name"]) or 0),
                "is_tm": False,
                "item_type": entry.get("item_type"),
            }
        for entry in raw_tms:
            shop_index[entry["name"]] = {
                "price": int(sm.tm_price or 0),
                "is_tm": True,
                "item_type": entry.get("item_type") or "TM",
            }

        # Player's bag (every owned item)
        owned_rows = []
        try:
            owned_rows = services.db.get_all_items() or []
        except Exception:
            owned_rows = []

        # Find all equipped items from Pokemon
        equipped_by_map = {}
        try:
            all_pokemons = services.db.get_all_pokemon() or []
            for pkm in all_pokemons:
                held = pkm.get("held_item")
                if held:
                    if held not in equipped_by_map:
                        equipped_by_map[held] = []
                    equipped_by_map[held].append(
                        {
                            "name": pkm.get("name", "Unknown"),
                            "individual_id": pkm.get("individual_id"),
                        }
                    )
        except Exception as e:
            logger = services.logger
            if logger:
                logger.log(
                    "error",
                    f"[Ankimon] get_all_pokemon failed in _get_mart_and_bag_data: {e}",
                )

        owned_index = {}
        for row in owned_rows:
            name = row.get("item_name") or row.get("name")
            qty = int(row.get("quantity") or 0)
            if not name or qty <= 0:
                continue
            owned_index[name] = {
                "quantity": qty,
                "category_id": row.get("category_id"),
            }

        all_names = sorted(
            set(shop_index.keys())
            | set(owned_index.keys())
            | set(equipped_by_map.keys())
        )

        items = []
        for name in all_names:
            shop_entry = shop_index.get(name)
            owned_entry = owned_index.get(name)
            is_tm = bool(
                (shop_entry or {}).get("is_tm")
                or (owned_entry or {}).get("category_id") == 37
            )
            items.append(
                self._serialize_item(
                    name=name,
                    is_tm=is_tm,
                    in_shop=bool(shop_entry),
                    shop_price=(shop_entry or {}).get("price"),
                    item_type=(shop_entry or {}).get("item_type"),
                    owned_quantity=(owned_entry or {}).get("quantity", 0),
                    equipped_instances=equipped_by_map.get(name, []),
                )
            )

        return {
            "cash": int(sm.get_callback("trainer.cash") or 0),
            "reroll_cost": int(sm.daily_items_reroll_cost or 0),
            "skip_reroll_confirm": self._get_skip_reroll_today(),
            "items": items,
            # pokemon_choices intentionally NOT included — for players with
            # 10k+ captures the payload is multiple MB. JS lazy-fetches via
            # bridge.getPokemonChoices() on first picker open + caches.
        }

    def _get_skip_reroll_today(self):
        # Stored as {"date": "YYYY-MM-DD", "skip": bool}. Treated as False
        # whenever the date doesn't match today, which gives the "reset every
        # day" behavior without needing a separate cleanup pass.
        try:
            data = services.db.get_user_data("shop_skip_reroll_confirm")
        except Exception:
            return False
        if not isinstance(data, dict):
            return False
        if data.get("date") != datetime.now().strftime("%Y-%m-%d"):
            return False
        return bool(data.get("skip"))

    def handle_set_skip_reroll_confirm(self, skip):
        try:
            services.db.set_user_data(
                "shop_skip_reroll_confirm",
                {"date": datetime.now().strftime("%Y-%m-%d"), "skip": bool(skip)},
            )
        except Exception as e:
            return {"ok": False, "message": str(e)}
        return {"ok": True}

    def _serialize_item(
        self,
        name,
        is_tm,
        in_shop,
        shop_price,
        item_type,
        owned_quantity,
        equipped_instances=None,
    ):
        from ..localized_text import (
            item_name as _item_name,
            item_description as _item_desc,
            move_name as _move_name,
            move_description as _move_desc,
            type_name as _type_name,
        )

        english_ui_name = name.replace("-", " ").title()
        if is_tm:
            ui_name = _move_name(name, english_ui_name)
        else:
            ui_name = _item_name(name, english_ui_name)
        entry = {
            "name": name,
            "ui_name": ui_name,
            "is_tm": is_tm,
            "in_shop": in_shop,
            "price": int(shop_price) if shop_price is not None else None,
            "owned_quantity": int(owned_quantity or 0),
            "item_type": item_type,
            "category": self._categorize(name, is_tm),
            "equipped_instances": equipped_instances or [],
        }

        if is_tm:
            move = find_details_move(name) or {}
            move_type = move.get("type") or "Normal"
            entry["image_url"] = QUrl.fromLocalFile(
                str(items_path / f"Bag_TM_{move_type}_SV_Sprite.png")
            ).toString()
            short_desc = _move_desc(name, move.get("shortDesc") or "")
            entry["description"] = (
                f"Teaches a compatible Pokémon the move {ui_name}."
                + (f" {short_desc}" if short_desc else "")
            )
            entry["move_type"] = move_type
            entry["move_type_label"] = _type_name(move_type, move_type)
            entry["move_power"] = self._coerce_int(move.get("basePower"))
            accuracy = move.get("accuracy")
            entry["move_accuracy"] = (
                "—" if accuracy is True else self._coerce_int(accuracy)
            )
            entry["move_pp"] = self._coerce_int(move.get("pp"))
            entry["move_damage_class"] = (move.get("category") or "").title() or None
        else:
            entry["image_url"] = QUrl.fromLocalFile(
                str(items_path / f"{name}.png")
            ).toString()
            entry["description"] = _item_desc(
                name, self._lookup_description(name) or f"A useful item: {ui_name}"
            )

        return entry

    def _categorize(self, name, is_tm):
        """Bucket items into the same groups the legacy bag exposed."""
        if is_tm:
            return "tm"
        bag = self.item_window
        if bag is not None:
            if name in getattr(bag, "hp_heal_items", {}):
                return "heal"
            if name in getattr(bag, "fossil_pokemon", {}):
                return "fossil"
            if name in getattr(bag, "pokeball_chances", {}):
                return "pokeball"
            if name in getattr(bag, "evolution_items", set()):
                return "evolution"
        return "other"

    def _lookup_price(self, name):
        entry = self._items_csv.get(name)
        return entry["cost"] if entry else 0

    def _lookup_description(self, name):
        entry = self._items_csv.get(name)
        if not entry:
            return None
        try:
            settings = self.shop_manager.settings_obj
            lang = (
                int(settings.get("misc.language") or 9) if settings is not None else 9
            )
        except (TypeError, ValueError, AttributeError):
            lang = 9
        if lang == 14:  # es_latam → fall back to es per legacy behaviour
            lang = 7
        return self._descriptions.get((entry["id"], lang))

    @property
    def _items_csv(self):
        """{identifier: {"id": int, "cost": int}} — items.csv loaded once."""
        cached = getattr(self, "_items_csv_cache", None)
        if cached is not None:
            return cached
        index = {}
        try:
            with open(csv_file_items_cost, mode="r", newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    try:
                        index[row["identifier"]] = {
                            "id": int(row["id"]),
                            "cost": int(row["cost"]),
                        }
                    except (KeyError, ValueError):
                        continue
        except OSError:
            pass
        self._items_csv_cache = index
        return index

    @property
    def _descriptions(self):
        """{(item_id, language_id): flavor_text} — item_flavor_text.csv loaded once."""
        cached = getattr(self, "_descriptions_cache", None)
        if cached is not None:
            return cached
        index = {}
        try:
            with open(csv_file_descriptions, mode="r", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    try:
                        key = (int(row["item_id"]), int(row["language_id"]))
                    except (KeyError, ValueError):
                        continue
                    # First occurrence wins (legacy get_item_description does the same).
                    index.setdefault(key, row.get("flavor_text"))
        except OSError:
            pass
        self._descriptions_cache = index
        return index

    @staticmethod
    def _coerce_int(value):
        try:
            if value in (None, "", "—"):
                return None
            if isinstance(value, bool):
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    # ------------------------------------------------------------------
    # Actions (JS → Python)
    # ------------------------------------------------------------------
    def handle_buy(self, item_name, is_tm):
        item = self._find_serialized(item_name)
        if not item or not item.get("in_shop"):
            return {"ok": False, "message": "Item is not in today's stock."}

        ui_name = item["ui_name"]
        price = int(item.get("price") or 0)
        cash = int(self.shop_manager.get_callback("trainer.cash") or 0)

        if is_tm and item.get("owned_quantity", 0) > 0:
            return {"ok": False, "message": f"{ui_name} is already owned."}
        if cash < price:
            return {"ok": False, "message": "Not enough money."}

        try:
            self.shop_manager.set_callback("trainer.cash", int(cash - price))
            give_item(item_name, item.get("item_type") if is_tm else None)
        except Exception as e:
            self.shop_manager.set_callback("trainer.cash", cash)
            return {"ok": False, "message": f"Purchase failed: {e}"}

        return {"ok": True, "message": f"Bought {ui_name} for {price}¥"}

    def handle_reroll(self):
        sm = self.shop_manager
        cost = int(sm.daily_items_reroll_cost or 0)
        cash = int(sm.get_callback("trainer.cash") or 0)
        if cash < cost:
            return {"ok": False, "message": "Not enough money to reroll."}

        # Compute new stock + write to DB first; only deduct cash once the
        # write succeeds. Otherwise a DB failure could swallow the reroll
        # cost with nothing to show for it.
        from ..pyobj.ankimon_shop import get_daily_items_pool

        daily_items_pool = get_daily_items_pool()

        random.seed()
        # Clamp sample sizes — random.sample raises if asked for more entries
        # than the pool contains, which would crash the bridge call.
        tm_pool = sm.get_tm_pool()
        num_items = min(sm.number_of_daily_items, len(daily_items_pool))
        num_tms = min(sm.number_of_daily_items, len(tm_pool))
        new_items = random.sample(daily_items_pool, num_items)
        new_tms = random.sample(tm_pool, num_tms)

        try:
            services.db.set_user_data(
                "todays_shop",
                {
                    "items": new_items,
                    "technical_machines": new_tms,
                    "date": datetime.now().strftime("%Y-%m-%d"),
                },
            )
            sm.todays_daily_items = new_items
            sm.todays_daily_tms = new_tms
            sm.set_callback("trainer.cash", int(cash - cost))
        except Exception as e:
            return {"ok": False, "message": f"Reroll failed: {e}"}

        return {"ok": True, "message": f"Rerolled stock for {cost}¥"}

    def handle_use(self, item_name):
        item = self._find_serialized(item_name)
        if not item:
            return {"ok": False, "message": "Item not found in your bag."}
        if (item.get("owned_quantity") or 0) <= 0:
            return {"ok": False, "message": "You don't own that item."}
        if self.item_window is None:
            return {"ok": False, "message": "Item bag service unavailable."}
        item_type = item.get("item_type") or ("TM" if item.get("is_tm") else None)
        result = self.item_window.dispatch_use(item_name, item_type)
        # Fossils + healing main can change team data (new entry / hp).
        if item.get("category") in ("fossil", "heal"):
            self._invalidate_pokemon_cache()
        return result

    def _invalidate_pokemon_cache(self):
        self._pokemon_choices_cache = None
        # The Team screen's roster picker keeps its own snapshot (ProfileData.
        # _roster_cache); a catch / release / evolution that stales the Items
        # picker stales that one too, so drop both on the same events.
        pd = getattr(self, "profile_data", None)
        if pd is not None:
            pd._roster_cache = None

    def get_pokemon_choices(self, item_name=None):
        """Return the player's Pokémon team for the in-shell picker.

        Enhancements:
        - Calculates CP for each Pokémon.
        - Provides base species ID ('b') for sprite fallbacks.
        - Checks evolution eligibility ('e') if an evolution item is used.
        - Sorts by eligibility (top), then active status, then level, then name.
        - Utilizes an instance cache for base results to maintain O(1) speed
          for repeated opens with non-evolution items.
        """
        # Determine if we need specific eligibility data
        is_evo_item = False
        if item_name:
            # We assume non-TM here; if it was a TM, useItemOnPokemon wouldn't be called.
            is_evo_item = self._categorize(item_name, False) == "evolution"

        cached = getattr(self, "_pokemon_choices_cache", None)
        # If not an evolution item, we can safely return the base cache (if it exists).
        # This keeps the "Give Item" picker snappy even with 10k+ Pokémon.
        if not is_evo_item and cached is not None:
            return cached

        try:
            pokemons = services.db.get_all_pokemon() or []
        except Exception as e:
            logger = services.logger
            if logger:
                logger.log(
                    "error",
                    f"[Ankimon] get_pokemon_choices: get_all_pokemon failed: {e}",
                )
            return {"choices": []}

        # Active Pokémon's individual_id (so we can flag it in the UI).
        main_individual_id = None
        bag = self.item_window
        if bag is not None and getattr(bag, "main_pokemon", None):
            main_individual_id = getattr(bag.main_pokemon, "individual_id", None)

        pokedex_data = _load_pokedex_cache()
        from ..functions.pokedex_functions import search_pokedex_by_id

        # Pre-fetch the region setting to avoid repeated lookups
        active_region = None
        if services.settings:
            active_region = services.settings.get("misc.active_region")
            if active_region:
                active_region = active_region.strip()

        choices = []
        for data in pokemons:
            try:
                if not isinstance(data, dict):
                    continue
                individual_id = data.get("individual_id")
                pokedex_id = data.get("id")
                name = data.get("name")
                if not individual_id or not name:
                    continue

                nickname = (data.get("nickname") or "").strip()
                held_item = data.get("held_item") or ""
                level = data.get("level")
                shiny = bool(data.get("shiny"))
                is_main = bool(
                    main_individual_id and individual_id == main_individual_id
                )

                # Resolve internal name using the optimized pokedex index
                internal_name = search_pokedex_by_id(pokedex_id)
                p_details = pokedex_data.get(internal_name)

                # Sprite fallback: get base species_id
                base_id = pokedex_id
                if p_details:
                    base_id = p_details.get("species_id") or pokedex_id

                entry = {
                    "id": individual_id,
                    "p": pokedex_id or 0,
                    "b": base_id or 0,
                    "n": name,
                    "l": int(level) if level is not None else None,
                    "cp": calculate_cp_from_dict(data),
                }
                if shiny:
                    entry["s"] = 1
                if is_main:
                    entry["m"] = 1
                if held_item:
                    entry["h"] = held_item
                if nickname and nickname.lower() != (name or "").lower():
                    entry["nk"] = nickname

                # Evolution eligibility (Optimized inline to avoid file I/O)
                if is_evo_item and item_name and p_details:
                    # Gender gate, through the SAME helper check_evolution_by_item
                    # uses: Gallade needs a male Kirlia, Froslass a female
                    # Snorunt. Without it this picker flags a Pokemon that
                    # Check_Evo_Item then refuses — and shop.js filters the list
                    # to e === 1, so the player is offered the one candidate the
                    # actual use will turn down. Sharing the helper is what keeps
                    # the two verdicts from drifting; the CSV lookup behind it is
                    # lru_cached, so the picker stays free of per-row file I/O.
                    pokemon_gender = data.get("gender")
                    evo_list = p_details.get("evos")
                    if evo_list:
                        for target_evo_name in evo_list:
                            normalized_target = (
                                target_evo_name.lower()
                                .replace(" ", "")
                                .replace("-", "")
                                .replace("'", "")
                                .replace(".", "")
                                .replace(":", "")
                            )
                            target_data = pokedex_data.get(
                                normalized_target
                            ) or pokedex_data.get(target_evo_name.lower())

                            if target_data and target_data.get("evoType") in (
                                "useItem",
                                "trade",
                            ):
                                if not evolution_gender_allows(
                                    target_data, pokemon_gender, _ITEM_EVO_TRIGGERS
                                ):
                                    continue

                                # "trade" belongs here alongside "useItem": Ankimon has no
                                # trading, so the trade-with-held-item species (Rhydon ->
                                # Rhyperior via Protector, Onix -> Steelix via Metal Coat,
                                # Seadra -> Kingdra via Dragon Scale, ...) are evolved by
                                # applying the item directly. Omitting it hid every one of
                                # them from this picker, which shop.js filters to e === 1.
                                #
                                # Normalize both sides by stripping spaces, hyphens and
                                # apostrophes so pokedex.json display names (e.g.
                                # "King's Rock") match items.csv identifiers (e.g.
                                # "kings-rock"), which drop the apostrophe. Mirrors the
                                # canonical logic in functions/pokedex_functions.py.
                                required_item = (
                                    (target_data.get("evoItem") or "")
                                    .lower()
                                    .replace(" ", "")
                                    .replace("-", "")
                                    .replace("'", "")
                                )
                                normalized_item_name = (
                                    (item_name or "")
                                    .lower()
                                    .replace(" ", "")
                                    .replace("-", "")
                                    .replace("'", "")
                                )
                                if required_item == normalized_item_name:
                                    target_region = target_data.get("evoRegion")

                                    if target_region:
                                        if (
                                            active_region
                                            and active_region.lower()
                                            == target_region.lower()
                                        ):
                                            entry["e"] = 1
                                            break
                                    else:
                                        # Standard form is only allowed if there is no regional sibling for this region/method
                                        has_matching_regional_sibling = False
                                        for sibling_name in evo_list:
                                            sib_norm = (
                                                sibling_name.lower()
                                                .replace(" ", "")
                                                .replace("-", "")
                                                .replace("'", "")
                                                .replace(".", "")
                                                .replace(":", "")
                                            )
                                            sib_data = pokedex_data.get(
                                                sib_norm
                                            ) or pokedex_data.get(sibling_name.lower())
                                            if (
                                                sib_data
                                                and sib_data.get("evoRegion")
                                                and active_region
                                                and sib_data.get("evoRegion").lower()
                                                == active_region.lower()
                                            ):
                                                if (
                                                    sib_data.get("evoType")
                                                    == target_data.get("evoType")
                                                    and (
                                                        sib_data.get("evoItem") or ""
                                                    ).lower()
                                                    == (
                                                        target_data.get("evoItem") or ""
                                                    ).lower()
                                                ):
                                                    has_matching_regional_sibling = True
                                                    break
                                        if not has_matching_regional_sibling:
                                            entry["e"] = 1
                                            break

                choices.append(entry)
            except Exception as e:
                logger = services.logger
                if logger:
                    logger.log(
                        "error",
                        f"[Ankimon] get_pokemon_choices: skipping malformed record: {e}",
                    )
                continue

        # Eligible first, then active first, then level (high → low), then alphabetical.
        choices.sort(
            key=lambda c: (
                not c.get("e"),
                not c.get("m"),
                -(c.get("l") or 0),
                (c.get("nk") or c.get("n") or "").lower(),
            )
        )
        result = {"choices": choices}
        # Update the base cache if this was a non-evolution run.
        if not is_evo_item:
            self._pokemon_choices_cache = result
        return result

    def handle_use_with_target(self, item_name, individual_id):
        """Apply an item to a specific Pokémon (chosen via the in-shell
        picker). Bypasses dispatch_use's QInputDialog branches by calling
        the underlying item_window helpers directly with the id."""
        item = self._find_serialized(item_name)
        if not item:
            return {"ok": False, "message": "Item not found in your bag."}
        if (item.get("owned_quantity") or 0) <= 0:
            return {"ok": False, "message": "You don't own that item."}
        if self.item_window is None:
            return {"ok": False, "message": "Item bag service unavailable."}
        if not individual_id:
            return {"ok": False, "message": "No Pokémon selected."}

        bag = self.item_window
        # Either branch below mutates the team (held-item or evolution),
        # so invalidate up front regardless of which path runs.
        self._invalidate_pokemon_cache()
        try:
            if item.get("category") == "evolution":
                # Check_Evo_Item needs the pre-evo's pokedex id to match
                # against the evolution table. Pull it from the proven
                # get_pokemon() API.
                pokemon_data = None
                try:
                    pokemon_data = services.db.get_pokemon(individual_id)
                except Exception as e:
                    logger = services.logger
                    if logger:
                        logger.log(
                            "error",
                            f"[Ankimon] get_pokemon({individual_id}) failed: {e}",
                        )
                pokedex_id = (pokemon_data or {}).get("id")
                if not pokedex_id:
                    return {"ok": False, "message": "Could not look up that Pokémon."}
                # Hand the record over rather than let Check_Evo_Item re-read it
                # for the gender gate: one query, and the id and the gender are
                # guaranteed to come from the same snapshot.
                bag.Check_Evo_Item(
                    individual_id, pokedex_id, item_name, pokemon_data=pokemon_data
                )
                return {"ok": True, "message": ""}

            # Held items (and anything else routed through the give-item
            # flow) — the legacy method already surfaces success/error via
            # log_and_showinfo, so we just return an empty message.
            bag._give_held_item_by_id(individual_id, item_name)
            return {"ok": True, "message": ""}
        except Exception as e:
            return {"ok": False, "message": f"Use failed: {e}"}

    def handle_unequip_item(self, individual_id, item_name):
        """Unequip a held item from a specific Pokémon and return it to the bag."""
        if not individual_id:
            return {"ok": False, "message": "No Pokémon selected."}

        self._invalidate_pokemon_cache()
        try:
            from ..pyobj.pokemon_obj import PokemonObject

            pokemon_data = services.db.get_pokemon(individual_id)
            if not pokemon_data:
                return {"ok": False, "message": "Could not find that Pokémon."}

            pokemon_obj = PokemonObject.from_dict(pokemon_data)
            if pokemon_obj.held_item != item_name:
                return {
                    "ok": False,
                    "message": "That Pokémon is not holding this item.",
                }

            pokemon_obj.remove_held_item()

            # Refresh open legacy item bag if it exists
            if self.item_window is not None:
                self.item_window.renewWidgets()

            # Also refresh an already-open PC Box window. Peek the registry
            # (services.pokemon_pc) rather than importing the lazy ``pokemon_pc``
            # proxy — that proxy would CONSTRUCT a brand-new PC window (and a Test
            # window) when none is open, so is_alive() would always be True and
            # we'd force an unwanted build. Mirrors singletons.swap_ankimon_account.
            from ..utils import is_alive

            pc = services.pokemon_pc
            if is_alive(pc):
                pc.refresh_gui()

            return {
                "ok": True,
                "message": f"Unequipped {item_name.replace('-', ' ').title()} from {pokemon_data.get('name')}.",
            }
        except Exception as e:
            return {"ok": False, "message": f"Unequip failed: {e}"}

    def _find_serialized(self, item_name):
        data = self.get_inventory_data()
        for entry in data["items"]:
            if entry["name"] == item_name:
                return entry
        return None

    # ------------------------------------------------------------------
    # Settings screen
    # ------------------------------------------------------------------
    def get_settings_data(self):
        """Build the schema + current values payload for the Settings screen."""
        from . import settings_schema

        settings_obj = (
            self.shop_manager.settings_obj if self.shop_manager is not None else None
        )
        if settings_obj is None:
            # Reload-safe: shop_manager / settings can be unset during early boot
            # or a profile swap. Serve an empty-but-valid payload so settings.js
            # still renders instead of crashing the Settings screen.
            return {"groups": [], "dev_mode": False}
        # Refresh config from disk so external edits are picked up.
        try:
            config = settings_obj.load_config()
        except Exception:
            config = settings_obj.config

        name_map = self._load_lang_json("setting_name.json")
        desc_map = self._load_lang_json("setting_description.json")
        # Reverse the friendly_name → key map so we can resolve friendly names
        # from the schema back to their config keys.
        key_by_friendly = {v: k for k, v in name_map.items()}

        groups = []
        for group_def in settings_schema.GROUPS:
            settings = self._serialize_settings_list(
                group_def.get("settings", []),
                key_by_friendly,
                name_map,
                desc_map,
                config,
            )
            # Append a chip-group as one composite setting after the regular
            # settings — keeps it in the same scroll section.
            chip_def = group_def.get("chip_group")
            if chip_def:
                settings.append(self._serialize_chip_group(chip_def, config))
            group = {
                "label": group_def["label"],
                "settings": settings,
                "subgroups": [],
            }
            for sub in group_def.get("subgroups", []):
                sub_settings = self._serialize_settings_list(
                    sub.get("settings", []),
                    key_by_friendly,
                    name_map,
                    desc_map,
                    config,
                )
                sub_chip_def = sub.get("chip_group")
                if sub_chip_def:
                    sub_settings.append(
                        self._serialize_chip_group(sub_chip_def, config)
                    )
                group["subgroups"].append(
                    {
                        "label": sub["label"],
                        "settings": sub_settings,
                    }
                )
            groups.append(group)
        return {"groups": groups, "dev_mode": bool(is_dev_mode())}

    @staticmethod
    def _serialize_chip_group(chip_def, config):
        chips = []
        for key, chip_label in chip_def["keys"]:
            val = config.get(key, DEFAULT_CONFIG[key])
            chips.append(
                {
                    "key": key,
                    "label": chip_label,
                    "value": bool(val),
                }
            )
        return {
            "key": "__chips__" + chip_def["label"].lower().replace(" ", "_"),
            "label": chip_def["label"],
            "description": chip_def.get("description", ""),
            "type": "chips",
            "chips": chips,
        }

    def _serialize_settings_list(
        self, friendly_names, key_by_friendly, name_map, desc_map, config
    ):
        out = []
        for friendly in friendly_names:
            if isinstance(friendly, dict):
                key = friendly["key"]
                if key not in config:
                    continue
                entry = {
                    "key": key,
                    "label": friendly.get("label", ""),
                    "description": friendly.get("description", ""),
                    "value": config.get(key),
                    "type": friendly.get("type", "text"),
                }
                if "options" in friendly:
                    entry["options"] = friendly["options"]
                out.append(entry)
            else:
                key = key_by_friendly.get(friendly)
                if not key or key not in config:
                    continue
                out.append(
                    self._serialize_setting(
                        key,
                        friendly,
                        name_map,
                        desc_map,
                        config.get(key),
                    )
                )
        return out

    @staticmethod
    def _serialize_setting(key, friendly, name_map, desc_map, value):
        from . import settings_schema

        entry = {
            "key": key,
            "label": friendly,
            "description": desc_map.get(key, ""),
            "value": value,
        }

        if key == "battle.auto_catch_wishlist":
            entry["type"] = "wishlist"
            from ..functions.pokedex_functions import get_pretty_name_for_id

            names_dict = {}
            if isinstance(value, list):
                for pid in value:
                    try:
                        pid_int = int(pid)
                        names_dict[pid_int] = get_pretty_name_for_id(pid_int)
                    except Exception:
                        names_dict[pid] = f"#{pid}"
            entry["names"] = names_dict
            return entry

        if key == "leaderboard.api_key":
            entry.update(settings_schema.serialize_secret_setting(value))
            return entry

        if key == "misc.active_region":
            entry["type"] = "select"
            entry["options"] = settings_schema.ACTIVE_REGION_OPTIONS
        elif isinstance(value, bool):
            entry["type"] = "boolean"
        elif isinstance(value, int):
            entry["type"] = "int"
        elif isinstance(value, float):
            entry["type"] = "float"
        else:
            entry["type"] = "text"
        return entry

    def _load_lang_json(self, filename):
        import json as _json

        cache_attr = f"_lang_{filename.replace('.', '_')}_cache"
        cached = getattr(self, cache_attr, None)
        if cached is not None:
            return cached
        path = self.addon_dir / "lang" / filename
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = _json.load(f)
        except (OSError, _json.JSONDecodeError):
            data = {}
        setattr(self, cache_attr, data)
        return data

    def handle_save_settings(self, payload, explicit_overrides=None):
        """Apply the JS-side payload, run legacy bounds checks, persist."""
        from . import settings_schema

        if not isinstance(payload, dict):
            return {"ok": False, "message": "Invalid payload."}

        if explicit_overrides is None:
            # Backward compatibility for callers that still send the legacy flat
            # payload: a HUD key present there represents an explicit edit.
            explicit_overrides = {
                key for key in payload if key in HUD_TOGGLE_AUTO_SYNC_KEYS
            }
        elif isinstance(explicit_overrides, (list, tuple, set)):
            explicit_overrides = {
                str(key)
                for key in explicit_overrides
                if str(key) in HUD_TOGGLE_AUTO_SYNC_KEYS
            }
        else:
            explicit_overrides = set()

        settings_obj = (
            self.shop_manager.settings_obj if self.shop_manager is not None else None
        )
        if settings_obj is None:
            return {"ok": False, "message": "Settings service is uninitialized."}

        # Load the CURRENT live config as our baseline
        live_config = settings_obj.load_config()
        # Create a detached working copy for all validation/coercion
        working_config = dict(live_config)
        original_config = dict(live_config)

        # Coerce incoming values back to the type of the existing config
        # entry so e.g. an int field doesn't silently become a string.
        try:
            for raw_key, raw_val in payload.items():
                key = str(raw_key)
                if key not in working_config:
                    continue
                if (
                    key == "leaderboard.api_key"
                    and settings_schema.is_unchanged_secret_placeholder(raw_val)
                ):
                    continue
                working_config[key] = self._coerce_incoming(
                    working_config[key], raw_val
                )
        except ValueError as e:
            return {"ok": False, "message": f"Validation error: {e}"}

        working_config, adjustments = settings_schema.validate_and_clamp(
            working_config, original_config
        )

        try:
            changed = False
            save_order = []
            main_key = "gui.show_sprites_across_ankimon"
            if original_config.get(main_key) != working_config.get(main_key):
                save_order.append(main_key)
            for key, val in working_config.items():
                if key == main_key:
                    continue
                if original_config.get(key) != val:
                    save_order.append(key)

            # Only write to the live settings object after all validation has passed
            if save_order:
                for key in save_order:
                    settings_obj.set(key, working_config[key], explicit_overrides)
                changed = True

            if changed:
                # Persist the live settings object, not the original payload dict.
                # The live object may have been updated by auto-sync logic in
                # Settings.set() for dependent HUD toggles.
                settings_obj.save_config(settings_obj.config, explicit_overrides)
                # Reload the final config state from the live object
                final_config = dict(settings_obj.config)
            else:
                final_config = original_config
        except Exception as e:
            # Restore the original live config state on failure to prevent
            # partial application from leaking into subsequent operations.
            try:
                # If the write loop partially applied some keys before failing,
                # restore the original_config into the live settings object.
                for key, val in original_config.items():
                    if settings_obj.config.get(key) != val:
                        settings_obj.config[key] = val
            except Exception:
                # If restoration itself fails, we're in a degraded state;
                # still return the error to the caller.
                pass
            return {"ok": False, "message": f"Save failed: {e}"}

        # Apply sprite visibility to web views and then refresh hotkeys.
        self._apply_sprite_visibility(
            final_config.get("gui.show_sprites_across_ankimon", True)
        )
        self._refresh_reviewer_hotkeys(final_config)

        # Refresh already-open native windows that depend on sprite visibility.
        self._refresh_native_windows()

        # Emit a shared settings-change notification for diagnostics only.
        try:
            events.emit("settings_changed", config=final_config)
        except Exception as e:
            logger = services.logger
            if logger:
                logger.log("error", f"Failed to emit settings_changed event: {e}")

        if adjustments:
            return {
                "ok": True,
                "message": "Saved (with adjustments).",
                "adjustments": adjustments,
            }
        return {"ok": True, "message": "Settings saved."}

    def _on_settings_changed(self, data):
        """React to a settings_changed event from any source (web settings
        or legacy SettingsWindow) by applying sprite visibility to web views
        and refreshing native views that depend on the sprite setting."""
        config = data.get("config") if isinstance(data, dict) else None
        if config is None:
            # If no config payload was provided, reload from disk.
            settings_obj = (
                self.shop_manager.settings_obj
                if self.shop_manager is not None
                else None
            )
            if settings_obj is not None:
                try:
                    config = settings_obj.load_config()
                except Exception:
                    pass
        if config is not None:
            self._apply_sprite_visibility(
                config.get("gui.show_sprites_across_ankimon", True)
            )

    def _refresh_native_windows(self):
        from ..utils import is_alive
        from .. import singletons

        try:
            if is_alive(services.pokemon_pc):
                services.pokemon_pc.refresh_gui()
        except Exception:
            pass

        try:
            if is_alive(services.reviewer):
                services.reviewer.refresh_hud()
        except Exception:
            pass

        try:
            if services.trainer_card is not None:
                services.trainer_card.refresh()
        except Exception:
            pass

        try:
            achievement_win = singletons._WINDOW_CACHE.get("achievement_bag")
            if is_alive(achievement_win):
                achievement_win.renewWidgets()
        except Exception:
            pass

    def _coerce_incoming(self, existing, incoming):
        """Match the new value's type to the existing config entry so a
        text-input UI doesn't accidentally rewrite an int field as a str.
        Raises ValueError for non-coercible numeric input — caller surfaces
        the failure rather than silently writing garbage to config."""
        if isinstance(existing, list):
            if isinstance(incoming, list):
                # Accept only integer IDs; silently drop anything non-numeric.
                return [int(x) for x in incoming if str(x).lstrip("-").isdigit()]
            return existing  # reject non-list payloads silently
        if isinstance(existing, bool):
            return bool(incoming)
        if isinstance(existing, int) and not isinstance(existing, bool):
            try:
                return int(incoming)
            except (TypeError, ValueError):
                # Range strings (e.g. "1-3" for cards_per_round) pass through;
                # validate_and_clamp's _coerce_cards_per_round normalizes them.
                if isinstance(incoming, str) and "-" in incoming:
                    return incoming
                raise ValueError(f"Expected integer, got {incoming!r}")
        if isinstance(existing, float):
            try:
                return float(incoming)
            except (TypeError, ValueError):
                raise ValueError(f"Expected float, got {incoming!r}")
        if existing is None:
            # active_region accepts None or a string region name
            return incoming if incoming not in ("", None, "None") else None
        return incoming if incoming is None else str(incoming)

    @staticmethod
    def _refresh_reviewer_hotkeys(config):
        try:
            from ..reviewer_ui import setup_reviewer_ui

            setup_reviewer_ui(
                config.get("controls.catch_key", "6"),
                config.get("controls.defeat_key", "5"),
                config.get("controls.pokemon_buttons", True),
                config.get("controls.team_cycle_key", "9"),
            )
        except Exception:
            # Best-effort — settings still saved even if the hook fails.
            pass


# _attribute_xp_and_evs_to_companion has been moved to functions.mobile_sync
