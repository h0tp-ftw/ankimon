"""PR #959: search and display item targets through real Chromium/WebChannel.

Run: QT_QPA_PLATFORM=offscreen python -m harness.checks.probe_real_item_picker_names
Optional screenshot directory: ANKIMON_PROOF_SCREENSHOTS=/tmp/picker-screenshots
"""

import json
import os
from pathlib import Path
import tempfile

from harness.checks.probe_real_webengine import _run_javascript, _wait_until
from harness.real_driver import RealDriver


def run_proof():
    """Use a disposable save and no shared sprite cache."""
    with tempfile.TemporaryDirectory(prefix="ankimon-picker-names-") as directory:
        previous_cache = os.environ.get("ANKIMON_SPRITE_CACHE")
        os.environ["ANKIMON_SPRITE_CACHE"] = str(Path(directory) / "no-cache")
        try:
            return _run(directory)
        finally:
            if previous_cache is None:
                os.environ.pop("ANKIMON_SPRITE_CACHE", None)
            else:
                os.environ["ANKIMON_SPRITE_CACHE"] = previous_cache


def _run(directory):
    from PyQt6.QtCore import QCoreApplication, QEvent
    from PyQt6.QtGui import QColor, QImage
    from PyQt6.QtTest import QTest

    driver = RealDriver(
        user_path=directory,
        first_encounter=False,
        webengine=True,
        require_webengine=True,
        settings_overrides={
            "misc.language": 1,
            "audio.sounds": False,
            "audio.sound_effects": False,
        },
    )
    from harness.fixtures import build_pokemon
    from Ankimon import utils
    from Ankimon.ankimon_items_web.shop_obj import SCREEN_ITEMS
    from Ankimon.functions.pokedex_functions import get_pretty_name_for_name
    from Ankimon.singletons import get_items_window

    db = driver.services.db
    named = build_pokemon({"id": 25, "level": 30}).to_dict()
    named["nickname"] = "Sparky"
    form = build_pokemon({"species": "wooperpaldea", "level": 30}).to_dict()
    form["nickname"] = get_pretty_name_for_name(form["name"])
    form["held_item"] = "leftovers"
    default = build_pokemon({"id": 133, "level": 30}).to_dict()
    default["nickname"] = get_pretty_name_for_name(default["name"])
    punctuated = build_pokemon({"id": 122, "level": 30}).to_dict()
    punctuated["nickname"] = get_pretty_name_for_name(punctuated["name"])
    for row in (named, form, default, punctuated):
        db.save_pokemon(row)
    db.add_item("leftovers", 1)

    # Placeholder pixels belong only to this disposable profile. Names/search
    # do not depend on downloading or modifying a developer's real sprites.
    image = QImage(96, 96, QImage.Format.Format_ARGB32)
    image.fill(QColor(120, 120, 200))
    sprites = Path(directory) / "sprites" / "front_default"
    sprites.mkdir(parents=True, exist_ok=True)
    for row in (named, form, default, punctuated):
        assert image.save(str(sprites / f"{row['id']}.png"))
    utils.items_path.mkdir(parents=True, exist_ok=True)
    assert image.save(str(utils.items_path / "leftovers.png"))

    window = get_items_window()
    window.load_screen(SCREEN_ITEMS)
    window.show()
    page = window.webview_items.page()

    def js(script):
        return _run_javascript(page, script)

    def click(selector):
        assert js(f"Boolean(document.querySelector({json.dumps(selector)}))"), selector
        js(f"document.querySelector({json.dumps(selector)}).click()")

    def card_names():
        return js(
            '[...document.querySelectorAll("#picker-grid .card-name")]'
            ".map(el => el.textContent).sort()"
        )

    def search(query, expected):
        js(
            f"document.querySelector('#picker-search').value = {json.dumps(query)};"
            "document.querySelector('#picker-search').dispatchEvent("
            "new Event('input', {bubbles: true}));"
        )
        # The production input handler debounces rendering by 80 ms. Waiting
        # beyond it prevents a stale grid from satisfying a same-count query.
        QTest.qWait(120)
        assert _wait_until(lambda: card_names() == sorted(expected)), (
            query,
            card_names(),
            expected,
        )

    try:
        assert _wait_until(
            lambda: js(
                "Boolean(document.querySelector('.nav-item[data-filter=\"owned\"]'))"
                " && !!window.bridge"
            )
        ), "The Items screen never connected to its real WebChannel"
        click('.nav-item[data-filter="owned"]')
        assert _wait_until(
            lambda: js(
                "Boolean(document.querySelector('[data-item-name=\"leftovers\"]'))"
            )
        )
        click('[data-item-name="leftovers"]')
        click(".det-action-btn.use")
        expected_all = ["Sparky", "パルデアウパー", "イーブイ", "バリヤード"]
        assert _wait_until(lambda: card_names() == sorted(expected_all)), card_names()

        choices = window.get_pokemon_choices()["choices"]
        custom = next(c for c in choices if c["id"] == named["individual_id"])
        regional = next(c for c in choices if c["id"] == form["individual_id"])
        ordinary = next(c for c in choices if c["id"] == default["individual_id"])
        formatted = next(c for c in choices if c["id"] == punctuated["individual_id"])
        assert custom["n"] == "ピカチュウ" and custom["nk"] == "Sparky"
        assert custom["a"] == "pikachu"
        assert regional["n"] == "パルデアウパー" and "nk" not in regional
        assert regional["a"] == "wooperpaldea Paldean Wooper"
        assert ordinary["n"] == "イーブイ" and "nk" not in ordinary
        assert formatted["n"] == "バリヤード" and "nk" not in formatted
        assert formatted["a"] == "mrmime Mr. Mime"
        assert (
            js(
                '[...document.querySelectorAll("#picker-grid .pokemon-card")]'
                '.find(c => c.querySelector(".card-name").textContent === "Sparky")'
                '.querySelector(".team-card-meta").textContent'
            )
            == "ピカチュウ"
        )

        for query, expected in (
            ("sparky", ["Sparky"]),
            ("pikachu", ["Sparky"]),
            ("ピカチュウ", ["Sparky"]),
            ("wooper", ["パルデアウパー"]),
            ("wooperpaldea", ["パルデアウパー"]),
            ("Paldean Wooper", ["パルデアウパー"]),
            ("パルデアウパー", ["パルデアウパー"]),
            ("leftovers", ["パルデアウパー"]),
            ("eevee", ["イーブイ"]),
            ("イーブイ", ["イーブイ"]),
            ("133", ["イーブイ"]),
            ("Mr. Mime", ["バリヤード"]),
            ("mrmime", ["バリヤード"]),
            ("バリヤード", ["バリヤード"]),
            ("__missing__", []),
            ("", expected_all),
        ):
            search(query, expected)

        dest = os.environ.get("ANKIMON_PROOF_SCREENSHOTS")
        if dest:
            Path(dest).mkdir(parents=True, exist_ok=True)
            QTest.qWait(500)
            assert window.grab().save(str(Path(dest) / "item-picker-names.png"))
        assert not [
            event for event in driver.drain_events() if event["type"] == "error"
        ]
        print(
            "probe_real_item_picker_names: OK (real Chromium/WebChannel custom nickname, "
            "localized species subtitle, formatted English/internal species aliases, "
            "default nicknames, and 16 searches)"
        )
        return True
    finally:
        window.close()
        for view in window._views.values():
            view.page().setWebChannel(None)
            view.setParent(None)
            view.deleteLater()
        driver.env.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


if __name__ == "__main__":
    run_proof()
