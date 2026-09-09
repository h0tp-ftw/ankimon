"""PR #838: purchase and use the remapped items through real WebEngine + Qt.

Run: QT_QPA_PLATFORM=offscreen python3 -m harness.checks.probe_real_item_evolutions
Optional screenshot directory: ANKIMON_PROOF_SCREENSHOTS=/tmp/pr838-screenshots
"""

from contextlib import nullcontext
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

from harness.checks.probe_real_webengine import _run_javascript, _wait_until
from harness.checks.probe_item_evolutions import LINKING_CORD_EVOLUTIONS
from harness.real_driver import RealDriver


def run_proof():
    """Run the real UI proof without using or changing a shared sprite cache."""
    # Never link this fixture to a developer's shared sprite pack.
    with tempfile.TemporaryDirectory(prefix="ankimon-items-proof-") as directory:
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
    """Exercise purchases and item evolutions against real Qt, Chromium and SQLite."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QPixmap
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QLabel, QPushButton

    d = RealDriver(
        user_path=directory,
        first_encounter=False,
        webengine=True,
        require_webengine=True,
        settings_overrides={
            "trainer.cash": 20000,
            "audio.sounds": False,
            "audio.sound_effects": False,
        },
    )
    from harness.fixtures import build_pokemon
    from Ankimon import utils
    from Ankimon.functions import pokedex_functions
    from Ankimon.singletons import get_items_window, get_item_window, get_evo_window
    from Ankimon.functions.pokedex_functions import get_pretty_name_for_id
    from Ankimon.ankimon_items_web.shop_obj import SCREEN_ITEMS

    db = d.services.db
    utils.items_path.mkdir(parents=True, exist_ok=True)
    assert not (utils.items_path / "linking-cord.png").exists()
    assert not QPixmap(str(utils.get_item_sprite_path("linking-cord"))).isNull()
    pairs = [
        (prevo, evolved, "linking-cord") for prevo, evolved in LINKING_CORD_EVOLUTIONS
    ]
    pairs.append((440, 113, "oval-stone"))
    records = []
    for prevo, evolved, item in pairs:
        mon = build_pokemon({"id": prevo, "level": 30, "moves": ["Tackle"]}).to_dict()
        mon["nickname"] = f"Proof {prevo}"
        db.save_pokemon(mon)
        records.append((mon, evolved, item))
    db.save_item(2160, "linking-cord", 12)
    db.save_item(110, "oval-stone", 1)
    bag = get_item_window()
    assert {"linking-cord", "oval-stone"} <= bag.evolution_items
    native_card = bag.ItemLabel("linking-cord", 12, None)
    assert any(
        not label.pixmap().isNull() for label in native_card.findChildren(QLabel)
    ), "The native bag lost the bundled icon"
    assert any(
        button.text() == "Evolve Pokemon"
        for button in native_card.findChildren(QPushButton)
    ), "The native bag did not expose the evolution action"
    native_card.deleteLater()
    window = get_items_window()
    window.load_screen(SCREEN_ITEMS)
    window.show()
    page = window.webview_items.page()

    def js(script):
        """Evaluate JavaScript and wait for the real page's callback."""
        return _run_javascript(page, script)

    def click(selector):
        """Require the requested DOM control to exist, then activate it."""
        assert js(f"Boolean(document.querySelector({json.dumps(selector)}))"), selector
        js(f"document.querySelector({json.dumps(selector)}).click()")

    def screenshot(name):
        """Optionally capture the window after Chromium presents the new frame."""
        dest = os.environ.get("ANKIMON_PROOF_SCREENSHOTS")
        if dest:
            Path(dest).mkdir(parents=True, exist_ok=True)
            # Chromium paints asynchronously after its JS callbacks and the
            # SQLite write. Let the compositor present the asserted DOM state.
            QTest.qWait(500)
            assert window.grab().save(str(Path(dest) / f"{name}.png"))

    assert _wait_until(
        lambda: js(
            "Boolean(document.querySelector('[data-item-name=\"linking-cord\"]'))"
        )
    ), "Cord never rendered"
    assert _wait_until(
        lambda: js(
            "(() => { const i = document.querySelector('[data-item-name=\"linking-cord\"] img'); return !!i && i.complete && i.naturalWidth === 128; })()"
        )
    ), "Bundled icon did not load in Chromium"
    click('[data-item-name="linking-cord"]')
    click(".det-action-btn.buy")
    assert _wait_until(lambda: db.get_item("linking-cord")["quantity"] == 13)
    assert d.services.settings.get("trainer.cash") == 12000
    screenshot("linking-cord-shop")

    def open_picker(item):
        """Select an owned item and open its populated evolution picker."""
        # Bag view includes the Oval Stone, which need not be in today's stock.
        click('.nav-item[data-filter="owned"]')
        assert _wait_until(
            lambda: js(
                f"Boolean(document.querySelector('[data-item-name=\"{item}\"]'))"
            )
        )
        click(f'[data-item-name="{item}"]')
        assert "Evolve a Pokémon" in js(
            "document.querySelector('.det-action-btn.use')?.textContent || ''"
        )
        click(".det-action-btn.use")
        assert _wait_until(
            lambda: (
                js("document.querySelectorAll('#picker-grid .pokemon-card').length") > 0
            )
        )

    def choose(mon):
        """Select a fixture Pokemon and wait for its native evolution prompt."""
        assert js(
            f"(() => {{ const c = [...document.querySelectorAll('#picker-grid .pokemon-card')].find(c => c.textContent.includes({json.dumps(mon['nickname'])})); if (!c) return false; c.click(); return true; }})()"
        ), mon["id"]
        assert _wait_until(lambda: get_evo_window().isVisible())

    def press(label):
        """Click the unique visible action on the native evolution window."""
        matches = [
            button
            for button in get_evo_window().findChildren(QPushButton)
            if button.text() == label and button.isVisible()
        ]
        assert len(matches) == 1, (label, matches)
        QTest.mouseClick(matches[0], Qt.MouseButton.LeftButton)
        d.env.app.processEvents()

    # Cancellation must preserve both the selected Pokemon and its item.
    open_picker("linking-cord")
    assert js("document.querySelectorAll('#picker-grid .pokemon-card').length") == 12
    screenshot("linking-cord-picker")
    mon, _, _ = records[0]
    choose(mon)
    press("Cancel Evolution")
    assert db.get_pokemon(mon["individual_id"])["id"] == mon["id"]
    assert db.get_item("linking-cord")["quantity"] == 13

    for mon, evolved, item in records:
        time_context = (
            patch.object(pokedex_functions, "get_time_of_day", return_value="day")
            if item == "oval-stone"
            else nullcontext()
        )
        with time_context:
            before = db.get_item(item)["quantity"]
            open_picker(item)
            choose(mon)
            press("Evolve Pokémon")
            assert _wait_until(
                lambda: db.get_pokemon(mon["individual_id"])["id"] == evolved
            ), (mon["id"], evolved)
            saved = db.get_pokemon(mon["individual_id"])
            assert saved["nickname"] == mon["nickname"]
            assert (db.get_item(item) or {}).get("quantity", 0) == before - 1
            print(
                f"UI evolution: {mon['id']} -> {evolved} ({get_pretty_name_for_id(evolved)}), consumed one {item}"
            )
            get_evo_window().close()

    assert not [event for event in d.drain_events() if event["type"] == "error"]
    window.close()
    print(
        "probe_real_item_evolutions: OK (real Chromium icon + purchase + 13 picker/Qt confirmations + cancellation + SQLite checks)"
    )
    return True


if __name__ == "__main__":
    run_proof()
