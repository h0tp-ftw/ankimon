"""Render bag icons through real Chromium/WebChannel in a disposable profile.

Run: QT_QPA_PLATFORM=offscreen python -m harness.checks.probe_real_item_sprites
Optional ANKIMON_ITEM_SPRITE_SOURCE copies downloaded art from a read-only pack;
otherwise the probe creates tiny PNGs using the pack's actual filenames.
Optional ANKIMON_PROOF_SCREENSHOTS saves a screenshot in that directory.
"""

import json
import os
from pathlib import Path
import shutil
import tempfile

from harness.checks.probe_real_webengine import _run_javascript, _wait_until
from harness.real_driver import RealDriver


TM_MOVES = {
    "Bug": "bugbite", "Dark": "bite", "Dragon": "dragonbreath",
    "Electric": "thundershock", "Fairy": "fairywind", "Fighting": "karatechop",
    "Fire": "ember", "Flying": "gust", "Ghost": "lick", "Grass": "absorb",
    "Ground": "mudslap", "Ice": "powdersnow", "Normal": "tackle",
    "Poison": "poisonsting", "Psychic": "confusion", "Rock": "rockthrow",
    "Steel": "metalclaw", "Water": "watergun",
}
ITEM_FILES = {
    "black-augurite": "Black-Augurite.png",
    "peat-block": "Peat-Block.png",
    "potion": "potion.png",
}


def run_proof():
    with tempfile.TemporaryDirectory(prefix="ankimon-item-sprites-") as directory:
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
    from PyQt6.QtCore import QCoreApplication, QEvent, QUrl
    from PyQt6.QtGui import QColor, QImage
    from PyQt6.QtTest import QTest

    driver = RealDriver(
        user_path=directory, first_encounter=False, webengine=True,
        require_webengine=True,
        settings_overrides={
            "misc.language": 9, "audio.sounds": False,
            "audio.sound_effects": False,
        },
    )
    from Ankimon import utils
    from Ankimon.ankimon_items_web.shop_obj import SCREEN_ITEMS
    from Ankimon.functions.pokedex_functions import find_details_move
    from Ankimon.singletons import get_items_window

    source = os.environ.get("ANKIMON_ITEM_SPRITE_SOURCE")
    image = QImage(32, 32, QImage.Format.Format_ARGB32)
    image.fill(QColor(120, 120, 200))
    utils.items_path.mkdir(parents=True, exist_ok=True)
    filenames = list(ITEM_FILES.values()) + [
        f"Bag_TM_{move_type.lower()}_SV_Sprite.png" for move_type in TM_MOVES
    ]
    for filename in filenames:
        target = utils.items_path / filename
        if source:
            shutil.copyfile(Path(source) / filename, target)
        else:
            assert image.save(str(target)), filename

    expected = {}
    for name in (*ITEM_FILES, "linking-cord", "health-mochi"):
        assert driver.services.db.add_item(name, 1), name
        expected[name] = QUrl.fromLocalFile(str(utils.get_item_sprite_path(name))).toString()
    for move_type, move_name in TM_MOVES.items():
        assert find_details_move(move_name)["type"] == move_type
        assert driver.services.db.save_item(None, move_name, 1, category_id=37)
        expected[move_name] = QUrl.fromLocalFile(str(
            utils.items_path / f"Bag_TM_{move_type.lower()}_SV_Sprite.png"
        )).toString()

    window = get_items_window()
    window.load_screen(SCREEN_ITEMS)
    window.show()
    page = window.webview_items.page()

    def js(script):
        return _run_javascript(page, script)

    def click(selector):
        assert js(f"Boolean(document.querySelector({json.dumps(selector)}))"), selector
        js(f"document.querySelector({json.dumps(selector)}).click()")

    def loaded(selector, url):
        return js(f"""(() => {{
            const img = document.querySelector({json.dumps(selector)});
            return !!img && img.complete && img.naturalWidth > 0
                && img.src === {json.dumps(url)}
                && Number(getComputedStyle(img).opacity) > 0.9;
        }})()""")

    try:
        assert _wait_until(lambda: js(
            "Boolean(document.querySelector('.nav-item[data-filter=\"owned\"]'))"
            " && !!window.bridge"
        )), "Items WebChannel did not connect"
        click('.nav-item[data-filter="owned"]')
        for name, url in expected.items():
            card = f'[data-item-name="{name}"]'
            selector = card + " .shop-card-sprite img"
            assert _wait_until(lambda: loaded(selector, url)), (name, "grid", url)
            click(card)
            assert _wait_until(lambda: loaded("#det-sprite", url)), (name, "detail", url)

        # Leave the case-mismatched evolution asset visible for screenshot QA.
        click('[data-item-name="black-augurite"]')
        destination = os.environ.get("ANKIMON_PROOF_SCREENSHOTS")
        if destination:
            Path(destination).mkdir(parents=True, exist_ok=True)
            QTest.qWait(500)
            assert window.grab().save(str(Path(destination) / "ankimon-item-sprites-proof.png"))
        assert not [event for event in driver.drain_events() if event["type"] == "error"]
        print(
            f"probe_real_item_sprites: OK ({len(expected)} grid and detail icons, "
            "all 18 TM types, legacy filenames and bundled fallback art)"
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
