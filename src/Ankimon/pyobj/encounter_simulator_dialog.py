"""Encounter Rate Simulator — a self-contained QWebEngine dialog.

Ported from BRRRR_Experimental and re-fitted onto main's service seam:

* State is read through :data:`services` (``services.db`` / ``services.settings``
  / ``services.tracker`` / ``services.main_pokemon`` / ``services.trainer_card``)
  instead of ``aqt.mw`` and ``singletons``.
* The simulation no longer monkeypatches ``mw.ankimon_db`` / ``main_pokemon`` /
  ``business.calculate_cp_from_dict``. Instead the simulated Mastery Index (EP)
  and main-Pokémon level are computed from the slider inputs and *injected* into
  ``encounter_functions`` via keyword arguments; the pity counters are read
  read-only through an explicit ``db`` provider (defaulting to ``services.db``).
  Nothing global is mutated, so the live game state is untouched.

The heavy ``QtWebEngine`` widgets are imported lazily inside ``__init__`` and the
lighter Qt imports are guarded, so importing this module never requires Qt — the
rate math (:meth:`SimulatorBridge.get_initial_state` /
:meth:`EncounterSimulatorDialog.calculate_rates`) is exercised head-less in
tests.

This ships as a stand-alone dialog owning its own ``QWebEngineView`` +
``QWebChannel``. It can also be hosted in the shared web shell via
``WebShellHost.mount(...)`` (see ``webshell/host.py``).
"""

import json
from pathlib import Path

try:  # Qt is optional at import time so Tier-1 collection stays Qt-free.
    from PyQt6.QtWidgets import QDialog, QVBoxLayout
    from PyQt6.QtCore import QUrl, QObject, pyqtSlot, Qt
    from PyQt6.QtGui import QColor
except Exception:  # pragma: no cover - headless (PyQt6 not importable)
    QDialog = object
    QObject = object

    def pyqtSlot(*_args, **_kwargs):
        def _decorator(func):
            return func

        return _decorator


from ..services import services
from ..functions import encounter_functions as ef
from .. import business
from ..functions.pokedex_functions import (
    search_pokedex,
    search_pokedex_by_id,
    safe_int,
)


class SimulatorBridge(QObject):
    """Exposes slots to JavaScript via QWebChannel for retrieving initial state
    and running real-time encounter-rate calculations."""

    def __init__(self, dialog):
        super().__init__()
        self.dialog = dialog

    @pyqtSlot(result=str)
    def get_initial_state(self) -> str:
        """Fetch the player's active live data to pre-populate simulator sliders."""
        db = services.db
        trainer_card = services.trainer_card
        main_pokemon = services.main_pokemon
        tracker = services.tracker
        settings_obj = services.settings

        # 1. Trainer Level
        t_level = (
            trainer_card.level if trainer_card and hasattr(trainer_card, "level") else 1
        )

        # 2. Dex Completion Percentage
        d_pct = 0.0
        try:
            from ..functions.pokedex_functions import _load_pokedex_cache

            pokedex_data = _load_pokedex_cache()
            if pokedex_data and db is not None:
                caught_ids = db.get_all_pokemon_ids()
                caught_species = set()
                for pid in caught_ids:
                    if pid >= 10000:
                        name = search_pokedex_by_id(pid)
                        if name and name != "Pokémon not found":
                            base_id = safe_int(search_pokedex(name, "species_id"))
                            if base_id:
                                caught_species.add(base_id)
                    else:
                        caught_species.add(pid)

                unique_species_in_game = {
                    safe_int(v.get("species_id"))
                    for v in pokedex_data.values()
                    if v.get("species_id")
                }
                unique_species_in_game.discard(0)
                total_species_count = (
                    len(unique_species_in_game) if unique_species_in_game else 1
                )
                d_pct = (
                    len(caught_species & unique_species_in_game) / total_species_count
                ) * 100.0
        except Exception as e:
            print(
                f"[Ankimon Bridge] Warning: Could not calculate live Dex Completion: {e}"
            )

        # 3. Reviews Done
        reviews = tracker.get_total_reviews() if tracker else 0

        # 4. Daily Goal
        daily_goal = 100
        try:
            if settings_obj:
                daily_goal = int(settings_obj.get("battle.daily_average"))
        except Exception:
            pass

        # 5. Average CP (top-6)
        avg_cp = 10.0
        try:
            if db is not None:
                all_pkmn = db.get_all_pokemon()
                if all_pkmn:
                    cps = []
                    for p in all_pkmn:
                        try:
                            cps.append(business.calculate_cp_from_dict(p))
                        except Exception:
                            pass
                    cps.sort(reverse=True)
                    top_6 = cps[:6]
                    avg_cp = sum(top_6) / len(top_6) if top_6 else 10.0
        except Exception as e:
            print(f"[Ankimon Bridge] Warning: Could not calculate live top-6 CP: {e}")

        # 6. Main Pokémon Level
        main_lvl = (
            main_pokemon.level
            if main_pokemon
            and hasattr(main_pokemon, "level")
            and main_pokemon.level is not None
            else 1
        )

        # Which system is currently active in the Ankimon add-on
        active_system = "Overhaul" if ef.USE_OVERHAUL_ENCOUNTER_SYSTEM else "Legacy"

        # Package Overhaul configuration constants dynamically from encounter_functions
        overhaul_config = {
            "ep_weight_trainer_level": float(ef.EP_WEIGHT_TRAINER_LEVEL),
            "ep_weight_dex_completion": float(ef.EP_WEIGHT_DEX_COMPLETION),
            "ep_weight_session_progress": float(ef.EP_WEIGHT_SESSION_PROGRESS),
            "ep_weight_core_team_power": float(ef.EP_WEIGHT_CORE_TEAM_POWER),
            "trainer_level_cap": float(ef.TRAINER_LEVEL_CAP),
            "core_team_power_cap": float(ef.CORE_TEAM_POWER_CAP),
            "tier_params": ef.OVERHAUL_TIER_PARAMS,
            "level_thresholds": ef.OVERHAUL_LEVEL_THRESHOLDS,
            "pity_thresholds": ef.OVERHAUL_PITY_THRESHOLDS,
            "pity_divisor": float(ef.OVERHAUL_PITY_DIVISOR),
        }

        state = {
            "trainer_level": int(t_level),
            "dex_completion": round(d_pct, 1),
            "reviews_done": int(reviews),
            "daily_goal": int(daily_goal),
            "avg_cp": int(avg_cp),
            "main_level": int(main_lvl),
            "config": overhaul_config,
            "active_system": active_system,
        }
        return json.dumps(state)

    @pyqtSlot(str, result=str)
    def calculate_rates_js(self, slider_state_json: str) -> str:
        """Receive slider values, run the backend calc, return JSON weights."""
        slider_state = json.loads(slider_state_json)
        result = self.dialog.calculate_rates(slider_state)
        return json.dumps(result)


class EncounterSimulatorDialog(QDialog):
    """Modern PyQt6 dialog housing the Encounter Rate Simulator web view.

    Exposes the simulated mathematical weighting without duplicating the
    balancing logic — it calls the real ``encounter_functions`` helpers with
    injected slider state.
    """

    def __init__(self, addon_dir=None):
        super().__init__()
        # Resolve the addon directory (the package root, where the
        # ``encounter_simulator/`` asset folder lives). Defaults to this
        # package so the dialog is self-contained; a caller (menu glue or the
        # future web-shell host) may pass an explicit path, e.g.
        # ``mw.addonManager.addonFromModule(__name__)`` -> addons21/<id>.
        self.addon_dir = (
            Path(addon_dir)
            if addon_dir is not None
            else Path(__file__).resolve().parent.parent
        )
        self.setWindowTitle("Ankimon Encounter Rate Simulator")
        self.resize(1100, 800)

        # Allow minimizing/maximizing cleanly
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMinMaxButtonsHint)

        # Setup Layout
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)

        # QtWebEngine is imported lazily so importing this module never pulls in
        # the (heavy, Chromium-backed) web engine — mirrors webshell/host.py.
        from PyQt6.QtWebEngineWidgets import QWebEngineView
        from PyQt6.QtWebChannel import QWebChannel

        # Setup QWebEngineView
        self.webview = QWebEngineView()
        # Prevent white background loading flash
        self.webview.page().setBackgroundColor(QColor(13, 15, 25))

        # Configure WebChannel communication
        self.channel = QWebChannel(self.webview)
        self.bridge = SimulatorBridge(self)
        self.channel.registerObject("pyBridge", self.bridge)
        self.webview.page().setWebChannel(self.channel)

        layout.addWidget(self.webview)

        # Load local HTML file (qwebchannel.js is served by Qt via qrc:///)
        html_path = self.addon_dir / "encounter_simulator" / "simulator.html"
        self.webview.setUrl(QUrl.fromLocalFile(html_path.as_posix()))

    def calculate_rates(self, slider_state: dict, db=None) -> dict:
        """Compute live & simulated encounter rates for the given slider state.

        Seam re-fit of exp's monkeypatching implementation:

        * ``db`` is the database provider used for the *read-only* pity lookup
          and (via ``services``) the live-rate math. It defaults to
          ``services.db``; callers/tests may pass an explicit provider. The
          global registry is never mutated.
        * The simulated Mastery Index (``ep``) and main-Pokémon ``main_level``
          are derived from the slider values and injected into
          ``encounter_functions``' calculation helpers, so no CP/DB/level
          globals are patched. This reproduces exp's simulated weighting while
          keeping the gauge and the rate math self-consistent.
        """
        if db is None:
            db = services.db

        tracker = services.tracker
        trainer_card = services.trainer_card
        settings_obj = services.settings

        # 1. Live rates reflect the player's *current* state (real db / team).
        ef.clear_encounter_cache()
        live_reviews = tracker.get_total_reviews() if tracker else 0
        live_goal = 100
        try:
            if settings_obj:
                live_goal = int(settings_obj.get("battle.daily_average"))
        except Exception:
            pass
        live_trainer_lvl = (
            trainer_card.level if trainer_card and hasattr(trainer_card, "level") else 1
        )
        live_overhaul_rates = ef._modify_percentages_overhaul(
            live_reviews, live_goal, live_trainer_lvl, db=db
        )
        live_legacy_rates = ef._modify_percentages_legacy(
            live_reviews, live_goal, live_trainer_lvl
        )

        # 2. Compute the simulated EP Mastery Index directly from the sliders.
        #    (Same formula as calculate_mastery_index_ep, fed by slider values
        #    instead of the live pokedex/team — this is what the gauge shows.)
        daily_goal = slider_state["daily_goal"] or 1
        t_norm = min(
            (slider_state["trainer_level"] / ef.TRAINER_LEVEL_CAP) * 100.0, 100.0
        )
        d_norm = slider_state["dex_completion"]
        s_norm = min((slider_state["reviews_done"] / daily_goal) * 100.0, 100.0)
        c_norm = min((slider_state["avg_cp"] / ef.CORE_TEAM_POWER_CAP) * 100.0, 100.0)
        simulated_ep = (
            (ef.EP_WEIGHT_TRAINER_LEVEL * t_norm)
            + (ef.EP_WEIGHT_DEX_COMPLETION * d_norm)
            + (ef.EP_WEIGHT_SESSION_PROGRESS * s_norm)
            + (ef.EP_WEIGHT_CORE_TEAM_POWER * c_norm)
        )
        simulated_ep = max(0.0, min(simulated_ep, 100.0))

        # 3. Simulated rates: inject the slider-derived EP + main level. Pity is
        #    read read-only from the provided db; nothing global is mutated.
        overhaul_rates = ef._modify_percentages_overhaul(
            slider_state["reviews_done"],
            slider_state["daily_goal"],
            slider_state["trainer_level"],
            main_level=slider_state["main_level"],
            ep=simulated_ep,
            db=db,
        )
        legacy_rates = ef._modify_percentages_legacy(
            slider_state["reviews_done"],
            slider_state["daily_goal"],
            slider_state["trainer_level"],
            main_level=slider_state["main_level"],
        )
        ef.clear_encounter_cache()

        # 4. Active tier locks from the level thresholds.
        locks = {
            tier: (slider_state["main_level"] < limit)
            for tier, limit in ef.OVERHAUL_LEVEL_THRESHOLDS.items()
        }

        return {
            "live_overhaul": live_overhaul_rates,
            "live_legacy": live_legacy_rates,
            "overhaul": overhaul_rates,
            "legacy": legacy_rates,
            "ep": simulated_ep,
            "locks": locks,
        }
