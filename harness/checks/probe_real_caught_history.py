"""PR #892: render persistent caught indicators and release via the real PC UI.

Run: python -m harness.checks.probe_real_caught_history [--screenshots /tmp/pr892]
Requires the Tier-2 Qt/WebEngine environment. Uses only a temporary profile.
"""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path
from unittest.mock import patch


def _resolve_bundled_asset(addon, request_path):
    """Resolve only the probe's Pokeball asset, contained in the add-on tree."""
    # Do not join any request-controlled path onto the filesystem root. This
    # proof hides sprites and needs exactly one file from Anki's media host.
    if request_path != "/_addons/ankimon/web/images/pokeball.png":
        return None
    try:
        root = addon.resolve(strict=True)
        asset = (root / "web/images/pokeball.png").resolve(strict=True)
        asset.relative_to(root)
        return asset if asset.is_file() else None
    except (OSError, ValueError, RuntimeError):
        return None


def run_proof(screenshots=None):
    """Prove real PC release retains the icon with a restricted media adapter."""
    from PyQt6.QtCore import Qt, QUrl
    from PyQt6.QtTest import QTest
    from PyQt6.QtWebEngineCore import (
        QWebEnginePage,
        QWebEngineUrlRequestInterceptor,
        qWebEngineChromiumVersion,
    )
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWidgets import QPushButton

    from harness.checks.probe_real_webengine import _run_javascript, _wait_until
    from harness.real_driver import RealDriver

    addon = Path(__file__).resolve().parents[2] / "src/Ankimon"

    class AddonAssets(QWebEngineUrlRequestInterceptor):
        """Supply the exact bundled assets normally served by Anki's media host."""

        def __init__(self, parent):
            super().__init__(parent)
            self.blocked_paths = set()

        def interceptRequest(self, request):
            """Block unapproved add-on requests; redirect only the bundled icon."""
            prefix = "/_addons/ankimon/"
            path = request.requestUrl().path()
            if path.startswith(prefix):
                asset = _resolve_bundled_asset(addon, path)
                if asset is None:
                    self.blocked_paths.add(path)
                    request.block(True)
                else:
                    request.redirect(QUrl.fromLocalFile(str(asset)))

    class CheckedPage(QWebEnginePage):
        def __init__(self, parent):
            super().__init__(parent)
            self.errors = []

        def javaScriptConsoleMessage(self, level, message, line, source):
            if level == self.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
                self.errors.append(message)

    with tempfile.TemporaryDirectory(prefix="ankimon-caught-proof-") as directory:
        # Never create fixture assets inside a user's shared sprite cache.
        with patch.dict(os.environ, {"ANKIMON_SPRITE_CACHE": directory + "/no-cache"}):
            driver = RealDriver(
                user_path=directory,
                first_encounter=False,
                webengine=True,
                require_webengine=True,
                settings_overrides={
                    "audio.sounds": False,
                    "audio.sound_effects": False,
                    "gui.hud_hidden_on_startup": False,
                    "gui.hud_owned_indicator": True,
                    "gui.hud_enemy_sprite": False,
                    "gui.hud_player_sprite": False,
                    "gui.reviewer_image_gif": False,
                    "gui.gif_in_collection": False,
                },
            )
        from Ankimon.pyobj.reviewer_obj import Reviewer_Manager
        from Ankimon.singletons import get_pokemon_pc
        from harness.fixtures import build_pokemon, set_enemy

        services = driver.services
        db = services.db
        view = QWebEngineView()
        page = CheckedPage(view)
        view.setPage(page)
        assets = AddonAssets(page)
        page.profile().setUrlRequestInterceptor(assets)
        view.resize(900, 500)
        view.show()

        def load_reviewer():
            loaded = []
            view.loadFinished.connect(loaded.append)
            view.setHtml(
                "<!doctype html><html><body>Reviewer caught-history proof</body></html>",
                QUrl.fromLocalFile(str(addon / "web/proof.html")),
            )
            assert _wait_until(lambda: bool(loaded)) and loaded[-1], (
                "reviewer load failed"
            )
            view.loadFinished.disconnect(loaded.append)
            # Retain a test-only reference; the production shadow root stays closed.
            _run_javascript(
                page,
                """
                const attach = Element.prototype.attachShadow;
                Element.prototype.attachShadow = function(options) {
                    const root = attach.call(this, options);
                    if (this.id === 'ankimon-hud-host') window.testHudRoot = root;
                    return root;
                };
            """,
            )
            _run_javascript(page, (addon / "web/ankimon_hud_portal.js").read_text())
            assert _run_javascript(page, "window.testHudRoot.mode") == "closed"

        load_reviewer()
        # Exercise the actual Chromium interceptor as well as the headless
        # path-policy tests: neither an absolute suffix nor other bundled files
        # may become a local-file redirect.
        _run_javascript(
            page,
            """
            for (const path of [
                '/_addons/ankimon//etc/passwd',
                '/_addons/ankimon/config.json',
            ]) {
                const image = new Image();
                image.src = path;
                document.body.appendChild(image);
            }
            """,
        )
        assert _wait_until(
            lambda: (
                assets.blocked_paths
                == {
                    "/_addons/ankimon//etc/passwd",
                    "/_addons/ankimon/config.json",
                }
            )
        ), ("Chromium asset requests escaped the allowlist", assets.blocked_paths)
        driver.aqt.mw.reviewer.web.eval = lambda script: _run_javascript(page, script)

        def check_icon(expected, label):
            services.reviewer.refresh_hud()
            result = _run_javascript(
                page,
                """
                (() => {
                    const icon = window.testHudRoot.querySelector('#owned-indicator-badge');
                    return {
                        present: Boolean(icon),
                        visible: Boolean(icon && icon.getBoundingClientRect().width > 0
                            && icon.getBoundingClientRect().height > 0
                            && getComputedStyle(icon).visibility !== 'hidden'),
                    };
                })()
            """,
            )
            assert result == {"present": expected, "visible": expected}, (label, result)
            if expected:
                assert _wait_until(
                    lambda: _run_javascript(
                        page,
                        """
                    (() => {
                        const icon = window.testHudRoot.querySelector('#owned-indicator-badge');
                        return Boolean(icon && icon.complete && icon.naturalWidth > 0);
                    })()
                """,
                    )
                ), (label, "bundled Pokeball failed to load")
            assert not page.errors, (label, page.errors)

        def save_screenshot(name, widget=view):
            if screenshots:
                # JavaScript layout completes before Chromium's composited frame
                # reaches QWidget.grab(); allow the real paint events to run.
                QTest.qWait(200)
                path = Path(screenshots)
                path.mkdir(parents=True, exist_ok=True)
                assert widget.grab().save(str(path / name)), "screenshot save failed"

        species_id = 25
        assert species_id not in db.get_caught_ids(), "temporary profile is not empty"
        set_enemy(services, driver.events, {"id": species_id, "level": 12})
        check_icon(False, "uncaught encounter")

        pokemon = build_pokemon({"id": species_id, "level": 12}).to_dict()
        assert db.save_pokemon(pokemon), "catch persistence failed"
        # No explicit invalidation: exercise the real DB -> reviewer invalidation.
        check_icon(True, "saved catch updates an already rendered enemy")

        pc = get_pokemon_pc()
        pc.show()
        pc.refresh_pokemon_grid()
        pc.show_pokemon_details({"individual_id": pokemon["individual_id"]})
        buttons = [
            b for b in pc.findChildren(QPushButton) if b.text() == "Release Pokémon"
        ]
        assert len(buttons) == 1, "real PC release button missing"
        save_screenshot("pc-before-release.png", pc)
        QTest.mouseClick(buttons[0], Qt.MouseButton.LeftButton)
        assert db.get_pokemon(pokemon["individual_id"]) is None, (
            "PC release did not delete catch"
        )
        assert any(
            h["individual_id"] == pokemon["individual_id"] for h in db.get_history()
        )
        assert species_id in db.get_caught_ids(), "PC release erased capture history"
        check_icon(True, "released catch")
        pc.close()

        # Reopen the reviewer and construct a fresh manager to rule out a stale
        # True in either Python's ownership cache or the previous page's DOM.
        services.reviewer = Reviewer_Manager(
            services.settings,
            services.main_pokemon,
            services.enemy_pokemon,
            services.tracker,
        )
        load_reviewer()
        for layout in (0, 1, 2):
            services.settings.set("gui.show_mainpkmn_in_reviewer", layout)
            check_icon(True, f"released history after reopening, layout {layout}")
        save_screenshot("reviewer-after-release.png")

        services.settings.set("gui.hud_owned_indicator", False)
        check_icon(False, "indicator setting disabled")
        services.settings.set("gui.hud_owned_indicator", True)
        check_icon(True, "indicator setting enabled")
        set_enemy(services, driver.events, {"id": 133, "level": 12})
        check_icon(False, "new uncaught species")
        set_enemy(services, driver.events, {"id": species_id, "level": 12, "hp": 0})
        check_icon(True, "released history in a later fainted encounter")
        view.close()
        print(
            f"PASS: Chromium {qWebEngineChromiumVersion()}, real PC release, "
            "persistent caught icon after reviewer rebuild, three layouts, setting toggle, "
            "uncaught/fainted encounters, bundled icon loaded, unsafe asset requests blocked"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    run_proof(args.screenshots)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
