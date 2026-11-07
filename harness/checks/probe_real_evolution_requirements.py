"""Real Qt proof and reproducible screenshots for the compact PC requirements.

python -m harness.checks.probe_real_evolution_requirements
python -m harness.checks.probe_real_evolution_requirements --screenshots <dir> --sprites <sprite-dir>

Sprites are optional and copied read-only into the throwaway profile. The probe
never opens a user's save. Without --sprites, the harness placeholder is used.
"""

import argparse
from datetime import datetime
from pathlib import Path
import random
import shutil

from harness.real_driver import RealDriver
from harness.fixtures import build_pokemon


def run(screenshots=None, sprites=None):
    from PyQt6.QtCore import QPoint
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QFrame, QLabel, QPushButton, QToolButton

    random.seed(1006)
    d = RealDriver(
        first_encounter=False,
        settings_overrides={
            "gui.gif_in_collection": False,
            "audio.sounds": False,
            "audio.sound_effects": False,
        },
    )
    from Ankimon.functions import friendship_evolution as fe
    from aqt.theme import theme_manager

    # Freeze only the presentation clock so screenshots and routes agree.
    fe._now_in_configured_tz = lambda: datetime(2026, 10, 25, 9, 0)
    pc, app, db = d.services.pokemon_pc, d.env.app, d.services.db
    pc.gif_in_collection = False
    if screenshots:
        screenshots = Path(screenshots)
        screenshots.mkdir(parents=True, exist_ok=True)
    species = ("Eevee", "Golbat", "Riolu", "Pikachu", "Kirlia")
    pokemon = {}
    for name in species:
        p = build_pokemon(
            {
                "species": name,
                "friendship": 106,
                "level": 20,
                "moves": ["Tackle"],
                "gender": "M",
            }
        )
        db.save_pokemon(p.to_dict())
        pokemon[name] = p
        if sprites:
            source = Path(sprites) / "front_default" / f"{p.id}.png"
            assert source.is_file(), source
            target = Path(d.env.user_path) / "sprites" / "front_default" / source.name
            shutil.copy2(source, target)

    def settle():
        app.processEvents()
        QTest.qWait(450)

    def check_geometry():
        card = pc.header_stack.currentWidget().findChild(
            QFrame, "evolutionRequirements"
        )
        assert card is not None
        for label in card.findChildren(QLabel):
            if label.isVisible():
                assert label.height() >= label.heightForWidth(label.width()), (
                    label.text()
                )
        # No horizontal clipping, even with the vertical scrollbar present.
        assert pc.header_scroll.widget().width() <= pc.header_scroll.viewport().width()
        right = card.mapTo(pc.header_scroll.viewport(), QPoint(card.width(), 0)).x()
        assert right <= pc.header_scroll.viewport().width(), (
            right,
            pc.header_scroll.viewport().width(),
            card.width(),
            pc.width(),
        )
        footer = pc.footer_stack
        top = footer.mapTo(pc, QPoint()).y()
        assert 0 <= top < top + footer.height() <= pc.height()
        assert pc.minimumHeight() <= 600, pc.minimumHeight()
        return card

    def show(name, filename, size=(1280, 720)):
        pc.show_pokemon_details({"individual_id": pokemon[name].individual_id})
        pc.resize(*size)
        settle()
        assert pc.width() <= size[0], (pc.width(), size)
        assert pc.height() <= size[1], (pc.height(), size)
        card = check_geometry()
        if screenshots:
            assert pc.grab().save(str(screenshots / filename))
        return card

    pc.show()
    pc.refresh_pokemon_grid()
    pc._update_time_display()
    card = show("Eevee", "eevee-routes.png")
    if screenshots:
        assert card.grab().save(str(screenshots / "eevee-routes-detail.png"))
    labels = {w.text() for w in card.findChildren(QLabel) if w.isVisible()}
    assert {"Espeon", "Umbreon", "Sylveon", "Teach a Fairy-type move"} <= labels
    toggle = card.findChild(QToolButton, "itemEvolutionToggle")
    assert toggle is not None and not toggle.isChecked()
    original_size = pc.size()
    toggle.click()
    settle()
    check_geometry()
    assert pc.size() == original_size
    assert pc.header_scroll.verticalScrollBar().maximum() > 0
    assert "Water Stone" in {
        w.text() for w in card.findChildren(QLabel) if w.isVisible()
    }
    pc.header_scroll.ensureWidgetVisible(card)
    settle()
    if screenshots:
        assert pc.grab().save(str(screenshots / "eevee-items-expanded.png"))
        assert card.grab().save(str(screenshots / "eevee-items-detail.png"))

    eevee = db.get_pokemon(pokemon["Eevee"].individual_id)
    eevee["attacks"] = ["Baby-Doll Eyes"]
    db.save_pokemon(eevee)
    card = show("Eevee", "eevee-fairy-move.png")
    pc.header_scroll.verticalScrollBar().setValue(0)
    settle()
    labels = {w.text() for w in card.findChildren(QLabel) if w.isVisible()}
    assert "Sylveon" in labels and "Espeon" not in labels and "Umbreon" not in labels
    if screenshots:
        assert pc.grab().save(str(screenshots / "eevee-fairy-move.png"))

    show("Golbat", "golbat-routes.png")
    show("Riolu", "riolu-routes.png")
    card = show("Kirlia", "kirlia-routes.png")
    assert "Evolves into Gardevoir at Lv30" in {
        w.text() for w in card.findChildren(QLabel)
    }
    card = show("Pikachu", "pikachu-items.png")
    assert card.findChild(QToolButton, "itemEvolutionToggle") is None
    assert "Thunder Stone" in {
        w.text() for w in card.findChildren(QLabel) if w.isVisible()
    }

    # The existing ready action survives replacing the requirements UI.
    eevee["friendship"] = 160
    db.save_pokemon(eevee)
    show("Eevee", "eevee-ready.png")
    button = pc.header_stack.currentWidget().findChild(QPushButton, "evolveNowButton")
    assert button is not None and button.isEnabled()
    assert "Sylveon" in button.text().title()

    # Small viewport, dark appearance, expanded routes: content scrolls while
    # the complete action footer stays inside the dialog.
    eevee.update(friendship=106, attacks=["Tackle"])
    db.save_pokemon(eevee)
    theme_manager.night_mode = True
    # Construct a fresh PC under the dark theme, just as opening it on startup
    # does. Live theme refresh itself is a separate existing PC behavior.
    previous_pc = pc
    pc.close()
    pc = type(pc)(
        pc.logger,
        pc.translator,
        pc.reviewer_obj,
        pc.test_window,
        pc.settings,
        pc.main_pokemon,
    )
    pc.show()
    pc.refresh_pokemon_grid()
    pc._update_time_display()
    previous_pc.deleteLater()
    card = show("Eevee", "eevee-dark-small.png", (1100, 600))
    card.findChild(QToolButton, "itemEvolutionToggle").click()
    settle()
    pc.header_scroll.ensureWidgetVisible(card)
    settle()
    check_geometry()
    assert pc.height() == 600
    if screenshots:
        assert pc.grab().save(str(screenshots / "eevee-dark-small.png"))

    errors = [event for event in d.drain_events() if event.get("type") == "error"]
    assert not errors, errors
    pc.close()
    app.processEvents()
    print(
        "PASS: route selection, item disclosure, ready action, text bounds, "
        "720/600px windows and light/dark Qt rendering"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshots", type=Path)
    parser.add_argument("--sprites", type=Path)
    args = parser.parse_args()
    run(args.screenshots, args.sprites)
