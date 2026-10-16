"""Render the real reviewer HUD in Chromium (#864).

Run: python -m harness.checks.probe_real_hud_css [screenshot.png]
Requires harness/requirements-webengine.txt and Qt's offscreen platform or xvfb.
The font-color typo was already fixed by #795; checking computed styles catches
its return, swallowed rules, and overrides that source-level brace checks miss.
"""

from __future__ import annotations

import itertools
import pathlib
import sys

# Configures Chromium and imports WebEngine before creating QApplication.
from harness.checks.probe_real_webengine import _run_javascript, _wait_until
from harness.real_env import start_real_session
from PyQt6.QtWebEngineCore import QWebEnginePage, qWebEngineChromiumVersion
from PyQt6.QtWebEngineWidgets import QWebEngineView


class CheckedPage(QWebEnginePage):
    def __init__(self, parent):
        super().__init__(parent)
        self.errors = []

    def javaScriptConsoleMessage(self, level, message, line, source):
        if level == self.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
            self.errors.append(message)


def main():
    session = start_real_session(
        webengine=True,
        require_webengine=True,
        settings_overrides={
            "gui.hud_styling": True,
            "gui.hud_hidden_on_startup": False,
            "gui.hud_xp_bar": True,
            "gui.hud_hp_bars": True,
            "gui.hud_hp_text": True,
            "gui.hud_pokemon_name": True,
        },
    )
    from aqt.theme import theme_manager
    from Ankimon.functions.pokemon_functions import find_experience_for_level

    view = QWebEngineView()
    page = CheckedPage(view)
    view.setPage(page)
    view.resize(900, 500)
    loaded = []
    view.loadFinished.connect(loaded.append)
    view.setHtml("<!doctype html><html><body></body></html>")
    view.show()
    assert _wait_until(lambda: bool(loaded)) and loaded[-1], "page did not load"

    # Retain a test-only handle to the CLOSED root without changing its mode or
    # adding inspection hooks to the shipped portal.
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
    portal = (
        pathlib.Path(__file__).resolve().parents[2]
        / "src/Ankimon/web/ankimon_hud_portal.js"
    )
    _run_javascript(page, portal.read_text(encoding="utf-8"))
    assert _run_javascript(page, "Boolean(window.__ankimonHud && window.testHudRoot)")
    assert _run_javascript(page, "window.testHudRoot.mode") == "closed"

    calls = []
    session.aqt.mw.reviewer.web.eval = lambda script, *a, **kw: calls.append(script)

    # The report's missing bars/plain black text is also the intentional
    # Styling-off mode: empty divs lose their height and text uses normal flow.
    # Re-enable Styling before the first normal render below to verify recovery.
    session.services.settings.set("gui.hud_styling", False)
    session.services.reviewer.refresh_hud()
    for script in calls:
        _run_javascript(page, script)
    unstyled = _run_javascript(
        page,
        """
        (() => {
            const root = window.testHudRoot;
            const style = id => getComputedStyle(root.querySelector('#' + id));
            return {
                css: window.__ankimonHudData.css,
                xpColor: style('xp_text').color,
                xpPosition: style('xp_text').position,
                hpHeight: style('life-bar').height,
                xpHeight: style('xp-bar').height,
            };
        })()
        """,
    )
    assert unstyled == {
        "css": "",
        "xpColor": "rgb(0, 0, 0)",
        "xpPosition": "static",
        "hpHeight": "0px",
        "xpHeight": "0px",
    }, unstyled
    session.services.settings.set("gui.hud_styling", True)

    main_pokemon = session.services.reviewer.main_pokemon
    experience = find_experience_for_level(
        main_pokemon.growth_rate,
        main_pokemon.level,
        session.services.settings.get("misc.remove_level_cap"),
    )
    assert experience > 0, experience
    xp_values = (-1, 0, experience // 2, experience - 1, experience, experience * 2)
    cases = 0
    for width, mode, dark, location, xp_value in itertools.product(
        (320, 900, 1280), (0, 1, 2), (False, True), (1, 2), xp_values
    ):
        case = (width, mode, dark, location, xp_value)
        if view.width() != width:
            view.resize(width, 500)
            assert _wait_until(
                lambda: _run_javascript(page, "window.innerWidth") == width
            ), (case, "viewport did not resize")
        theme_manager.night_mode = dark
        session.services.settings.set("gui.show_mainpkmn_in_reviewer", mode)
        session.services.settings.set("gui.xp_bar_location", location)
        main_pokemon.xp = xp_value
        calls.clear()
        session.services.reviewer.invalidate_hud_cache()
        session.services.reviewer.refresh_hud()
        assert calls, (case, "reviewer emitted no JavaScript")
        for script in calls:
            _run_javascript(page, script)

        result = _run_javascript(
            page,
            """
            (() => {
                const root = window.testHudRoot;
                const style = id => getComputedStyle(root.querySelector('#' + id));
                const rect = id => {
                    const r = root.querySelector('#' + id).getBoundingClientRect();
                    return {left: r.left, right: r.right, top: r.top,
                        bottom: r.bottom, width: r.width, height: r.height,
                        centerY: (r.top + r.bottom) / 2};
                };
                const xp = style('xp_text');
                const hp = style('hp-display');
                const track = style('xp-bar');
                return {
                    xpColor: xp.color, xpFont: xp.fontFamily,
                    xpLabel: rect('xp_text'), xpTrack: rect('xp-bar'),
                    xpFill: rect('xp-bar-fill'),
                    xpInnerWidth: rect('xp-bar').width
                        - parseFloat(track.borderLeftWidth) - parseFloat(track.borderRightWidth),
                    xpOverflow: track.overflow,
                    viewportWidth: window.innerWidth, viewportHeight: window.innerHeight,
                    hpColor: hp.color, hpBackground: hp.backgroundColor,
                    lifePosition: style('life-bar').position,
                    lifeHeight: parseFloat(style('life-bar').height),
                    xpHeight: parseFloat(style('xp-bar').height),
                    mainBar: Boolean(root.querySelector('#mylife-bar')),
                    outline: style('ankimon-hud').getPropertyValue('--ankimon-outline').trim(),
                    xpVisible: root.querySelector('#xp_text').getBoundingClientRect().height > 0,
                };
            })()
        """,
        )
        assert result["xpColor"] == "rgba(0, 191, 255, 0.85)", (case, result)
        assert result["xpFont"] == "Arial, sans-serif", (case, result)
        # Verify the rendered relationship rather than the old label inset:
        # its transform centers the label on the track at either screen edge.
        label, track, fill = (result[key] for key in ("xpLabel", "xpTrack", "xpFill"))
        assert abs(label["centerY"] - track["centerY"]) < 0.5, (case, result)
        assert 0 <= label["left"] < label["right"] < track["left"], (case, result)
        assert 0 <= label["top"] < label["bottom"] <= result["viewportHeight"], (
            case,
            result,
        )
        assert 0 <= track["left"] < track["right"] <= result["viewportWidth"], (
            case,
            result,
        )
        assert 0 <= track["top"] < track["bottom"] <= result["viewportHeight"], (
            case,
            result,
        )
        assert (track["centerY"] < result["viewportHeight"] / 2) == (location == 1), (
            case,
            result,
        )
        assert track["left"] <= fill["left"] <= fill["right"] <= track["right"], (
            case,
            result,
        )
        assert track["top"] <= fill["top"] < fill["bottom"] <= track["bottom"], (
            case,
            result,
        )
        expected_percent = min(100, max(0, int(xp_value / experience * 100)))
        expected_width = result["xpInnerWidth"] * expected_percent / 100
        assert abs(fill["width"] - expected_width) < 0.1, (case, result)
        assert result["xpOverflow"] == "hidden", (case, result)
        assert result["hpColor"] == (
            "rgb(255, 255, 255)" if dark else "rgb(109, 109, 110)"
        ), (case, result)
        assert result["hpBackground"] == (
            "rgb(31, 31, 31)" if dark else "rgb(255, 255, 255)"
        ), (case, result)
        assert result["outline"] == ("#1F1F1F" if dark else "#FFFFFF"), (case, result)
        assert result["lifePosition"] == "fixed", (case, result)
        assert result["lifeHeight"] > 0, (case, result)
        assert result["xpHeight"] > 0, (case, result)
        assert result["mainBar"] == (mode > 0), (case, result)
        assert result["xpVisible"], (case, result)
        # Appending a rule to the exact emitted CSS also detects an unclosed
        # final block that would otherwise silently swallow future additions.
        tail = _run_javascript(
            page,
            """
            window.__ankimonHud.update(window.__ankimonHudData.html,
                window.__ankimonHudData.css + '\\n#ankimon-hud { --probe-tail: reached; }');
            getComputedStyle(window.testHudRoot.querySelector('#ankimon-hud'))
                .getPropertyValue('--probe-tail').trim();
        """,
        )
        assert tail == "reached", (case, tail)
        assert not page.errors, (case, page.errors)
        cases += 1

    if len(sys.argv) > 1:
        assert view.grab().save(sys.argv[1]), "could not save screenshot"
    print(
        f"PASS: Chromium {qWebEngineChromiumVersion()}, Styling-off reproduction "
        f"and recovery, {cases} HUD viewport/layout/theme/XP cases"
    )
    view.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
