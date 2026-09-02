"""
harness/real_env.py — boot the REAL Ankimon add-on headless, under offscreen Qt.

This is Tier 2: it runs the genuine `import Ankimon` (the real __init__.py ->
singletons.py -> every real Qt window), with only the Anki *host* faked
(harness/fake_aqt.py) and real PyQt6 in offscreen mode. So the real widgets,
real memory, real PC box, real everything are exercised — just not rendered to a
screen — which is what makes real-Qt glitches / memory behaviour reproducible.

PREREQUISITE: must run under a Python that has PyQt6 + requests + markdown, with
the native Qt libs reachable. The sudo-free setup (venv + local libs) is created
by harness/setup_tier2.sh, which also writes an env file you source first:

    source .tier2/env.sh            # sets LD_LIBRARY_PATH + QT_QPA_PLATFORM + PATH
    python -m harness.checks.probe_real_boot

(LD_LIBRARY_PATH must be set BEFORE Python starts — the loader needs it for
PyQt6's libEGL — so the env file, not this module, sets it.)
"""

from __future__ import annotations

import os
import sys
import tempfile
import pathlib
from types import SimpleNamespace

REPO = pathlib.Path(__file__).resolve().parents[1]
SRC = str(REPO / "src")


def _seed_assets(user_path):
    """Give the session's profile a sprite set so the real windows can render.

    Preference order:
      1. The REAL downloaded sprite set, if fetched (harness/fetch_sprites.py).
         The session's ``sprites`` dir is symlinked to that shared cache, so runs
         are pixel-accurate with the genuine Pokemon art.
      2. Otherwise a single placeholder ``front_default/substitute.png``.
         get_sprite_path falls back to it for any missing sprite, so the real
         window code still runs (genuine code path / scaling / memory) — it just
         draws the placeholder instead of real art. (Without *any* sprite, the
         real windows divide by a null pixmap's width()==0 and crash.)

    Set ANKIMON_SPRITE_CACHE to use a sprite cache elsewhere.
    """
    base = pathlib.Path(user_path) / "sprites"

    cache_env = os.environ.get("ANKIMON_SPRITE_CACHE")
    cache = pathlib.Path(cache_env) if cache_env else (REPO / ".tier2" / "sprites-cache")
    if (cache / "download_complete.flag").exists():
        try:
            if base.is_symlink():
                return  # already linked to a cache
            # Boot's ensure_ankimon_infrastructure pre-creates an empty sprites/
            # dir; replace it with a symlink to the real cache.
            if base.exists() and not any(base.iterdir()):
                base.rmdir()
            if not base.exists():
                base.symlink_to(cache, target_is_directory=True)
                return
        except OSError:
            pass  # fall through to placeholder

    # If a non-empty real sprites dir is already there, leave it.
    if base.exists() and any(base.iterdir()):
        return

    from PyQt6.QtGui import QImage, QColor

    for sub in ("front_default", "back_default", "items", "badges",
                "front_default_gif", "back_default_gif", "berries"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    substitute = base / "front_default" / "substitute.png"
    if not substitute.exists():
        img = QImage(96, 96, QImage.Format.Format_ARGB32)
        img.fill(QColor(120, 120, 200))
        img.save(str(substitute), "PNG")


def start_real_session(user_path=None, settings_overrides=None, neuter_network=True,
                       first_run=False, webengine=False, require_webengine=False):
    """Boot the real add-on and return handles (app, aqt, services, events).

    first_run=True seeds the sprite assets BEFORE the import, so startup's
    _check_assets passes (database_complete=True) and a blank profile gets the
    genuine new-user path: the full menu populates and the starter-selection
    window opens — the state a real user is in once sprites are present. (Default
    False keeps the lean "assets incomplete" boot for fast play tests.)
    """
    if user_path is None:
        user_path = tempfile.mkdtemp(prefix="ankimon_real_")
    os.environ["ANKIMON_USER_PATH"] = str(user_path)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if SRC not in sys.path:
        sys.path.insert(0, SRC)

    # Make the import-time connectivity check fast + offline (no 5s hangs, and
    # the changelog/leaderboard network paths short-circuit). Faithful enough —
    # "offline" is a valid state and doesn't affect gameplay.
    if neuter_network:
        try:
            import requests

            def _offline(*a, **k):
                raise RuntimeError("offline (harness)")

            requests.get = _offline
            requests.post = _offline
        except Exception:
            pass

    # Real WebEngine (to render the Pokedex / HUD) MUST be imported before the
    # QApplication exists. Ordinary Tier 2 may fall back to the lightweight stub;
    # dedicated browser probes set require_webengine=True so missing Chromium
    # bindings fail loudly instead of producing a misleading green check.
    if require_webengine and not webengine:
        raise ValueError("require_webengine=True requires webengine=True")
    if webengine:
        os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
        os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS",
                              "--no-sandbox --disable-gpu --disable-dev-shm-usage "
                              "--in-process-gpu --single-process")
        try:
            import PyQt6.QtWebEngineWidgets  # noqa: F401  (must precede QApplication)
        except Exception as exc:
            if require_webengine:
                raise RuntimeError(
                    "Real QtWebEngine was required but PyQt6-WebEngine could not be imported"
                ) from exc
            webengine = False

    # Real PyQt6 first (needs the offscreen platform + native libs already loadable).
    from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox, QInputDialog

    app = QApplication.instance() or QApplication(["ankimon-harness"])

    # Assets: by default seeded AFTER boot (by the driver), so startup stays on its
    # "assets incomplete" path and skips the first-run UI. With first_run=True we
    # seed them BEFORE the import — startup's _check_assets then passes
    # (database_complete=True), the menu fully populates, and a blank profile gets
    # the genuine new-user flow (starter selection). _seed_assets symlinks to the
    # real sprite cache if present, so this renders with genuine art.
    if first_run:
        _seed_assets(user_path)

    # Neuter blocking dialogs so a real boot can never hang on a modal .exec().
    QDialog.exec = lambda self: 0
    try:
        QMessageBox.exec = lambda self: QMessageBox.StandardButton.Ok
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
        QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
        QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
        QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
    except Exception:
        pass

    # QInputDialog.* are blocking static modals too (e.g. item_window's "Select
    # Pokemon" getItem). Auto-accept with a valid default so a real boot / a GUI
    # fuzzer can't hang on them (mirrors the QMessageBox auto-answers above).
    def _fuzz_get_item(parent, title, label, items, current=0, editable=True, *a, **k):
        items = list(items)
        chosen = items[current] if 0 <= current < len(items) else (items[0] if items else "")
        return chosen, True
    try:
        QInputDialog.getItem = staticmethod(_fuzz_get_item)
        QInputDialog.getText = staticmethod(lambda *a, **k: ("Ankimon", True))
        QInputDialog.getMultiLineText = staticmethod(lambda *a, **k: ("Ankimon", True))
        QInputDialog.getInt = staticmethod(lambda *a, **k: (1, True))
        QInputDialog.getDouble = staticmethod(lambda *a, **k: (1.0, True))
    except Exception:
        pass

    # Install the fake Anki host (mw, gui_hooks, aqt.* ) BEFORE importing Ankimon.
    from . import fake_aqt

    aqt = fake_aqt.install(app, user_path, real_webengine=webengine)

    # REAL boot — runs Ankimon/__init__.py just as Anki would.
    import Ankimon  # noqa: F401

    # Anki fires "profileLoaded" after the add-on imports; the harness must too, or
    # mw.catchpokemon / mw.defeatpokemon (set by profile_hooks.on_profile_loaded) stay
    # unset and the test_window catch/defeat buttons AttributeError-crash. (This is the
    # faithful boot order — the same hook the real client runs on profile open.)
    try:
        from anki.hooks import runHook
        runHook("profileLoaded")
    except Exception:
        pass

    # Turn on event capture (emits are no-ops until enabled).
    from Ankimon.events import events
    events.enable()
    events.reset()

    from Ankimon.services import services
    if settings_overrides:
        for k, v in settings_overrides.items():
            services.settings.set(k, v)

    return SimpleNamespace(
        app=app,
        aqt=aqt,
        services=services,
        events=events,
        gui_hooks=aqt.gui_hooks,
        user_path=user_path,
        Ankimon=Ankimon,
    )
