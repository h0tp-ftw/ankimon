"""PR #893: real Qt escape failures and mixed level/item evolution controls.

Run: QT_QPA_PLATFORM=offscreen python -m harness.checks.probe_real_recent_merges
Set ANKIMON_PROOF_SCREENSHOTS to retain PNGs of the evolution details and bag.
"""

from contextlib import nullcontext
import os
from pathlib import Path
import random
from unittest.mock import patch

from harness.real_driver import RealDriver


def run_proof():
    """Drive real widgets, encounter replacement and SQLite in a throwaway save."""
    random.seed(893)
    driver = RealDriver(
        settings_overrides={
            "audio.sounds": False,
            "audio.sound_effects": False,
            "gui.pop_up_dialog_message_on_encounter": False,
        }
    )
    from PyQt6.QtCore import QCoreApplication, QEvent, Qt
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget
    from Ankimon.functions import encounter_functions
    from Ankimon import gui_presenter
    from Ankimon.gui_classes.pokemon_details import PokemonCollectionDetailsSplit
    from Ankimon.singletons import get_item_window, get_evo_window
    from harness.fixtures import build_pokemon

    db = driver.services.db
    bag = get_item_window()
    enemy = driver.services.enemy_pokemon

    def screenshot(widget, name):
        """Capture a shown real widget when an artifact directory was requested."""
        destination = os.environ.get("ANKIMON_PROOF_SCREENSHOTS")
        if destination:
            Path(destination).mkdir(parents=True, exist_ok=True)
            QTest.qWait(100)
            assert widget.grab().save(str(Path(destination) / f"{name}.png"))

    for quantity in (1, 3):
        db.save_item(63, "poke-doll", quantity, {"source": "proof"}, cost=1000)
        before = db.get_item("poke-doll")
        token = enemy._ankimon_encounter_token
        with patch.object(
            encounter_functions,
            "generate_random_pokemon",
            side_effect=RuntimeError("generation failed"),
        ):
            assert bag.dispatch_use("poke-doll")["ok"] is False
        assert db.get_item("poke-doll") == before
        assert enemy._ankimon_encounter_token is token
        assert db.get_mobile_history() == []

    # Use the real new_pokemon function, raising specifically AFTER replacement.
    old_name = enemy.name
    with patch.object(
        driver.services.tracker,
        "randomize_battle_scene",
        side_effect=RuntimeError("scene unavailable"),
    ):
        assert bag.dispatch_use("poke-doll")["ok"] is True
    assert enemy._ankimon_encounter_token is not token
    assert db.get_item("poke-doll")["quantity"] == 2
    history = db.get_mobile_history()
    assert len(history) == 1 and history[0]["enemy_name"] == old_name
    assert history[0]["outcome"] == "escaped"

    # Exercise the native bag button, not just its shared web-dispatch method.
    bag.renewWidgets()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    bag.show()
    driver.env.app.processEvents()
    buttons = [
        button
        for button in bag.findChildren(QPushButton)
        if button.text() == "Escape from battle" and button.isVisible()
    ]
    assert len(buttons) == 1, [
        button.text() for button in bag.findChildren(QPushButton)
    ]
    token = enemy._ankimon_encounter_token
    QTest.mouseClick(buttons[0], Qt.MouseButton.LeftButton)
    driver.env.app.processEvents()
    assert enemy._ankimon_encounter_token is not token
    assert db.get_item("poke-doll")["quantity"] == 1
    assert len(db.get_mobile_history()) == 2
    screenshot(bag, "escape-bag")
    bag.close()

    # Native signals ignore the handler's return value. Both refund outcomes
    # must reach the production presenter's warning leaf with a useful message.
    for refund_fails in (False, True):
        db.save_item(63, "poke-doll", 3)
        bag.renewWidgets()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        bag.show()
        driver.env.app.processEvents()
        button = next(
            button
            for button in bag.findChildren(QPushButton)
            if button.text() == "Escape from battle" and button.isVisible()
        )
        token = enemy._ankimon_encounter_token
        refund_error = (
            patch.object(
                db, "refund_item", side_effect=OSError("private refund details")
            )
            if refund_fails
            else nullcontext()
        )
        with (
            refund_error,
            patch.object(
                encounter_functions,
                "generate_random_pokemon",
                side_effect=RuntimeError("private generation details"),
            ),
            patch.object(gui_presenter, "showWarning") as warning,
        ):
            QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        warning.assert_called_once()
        message = warning.call_args.args[0]
        assert "private" not in message
        if refund_fails:
            assert (
                "could not be returned" in message and "report this problem" in message
            )
        else:
            assert "was returned" in message and "try again" in message
        assert db.get_item("poke-doll")["quantity"] == (2 if refund_fails else 3)
        assert enemy._ankimon_encounter_token is token
        assert len(db.get_mobile_history()) == 2
        bag.close()

    for species, level, gender, target, item_target in (
        (281, 30, "M", 282, "Gallade"),
        (361, 42, "F", 362, "Froslass"),
        (25, 30, "M", None, "Raichu"),
    ):
        pokemon = build_pokemon({"id": species, "level": level, "gender": gender})
        record = pokemon.to_dict()
        db.save_pokemon(record)
        header, stats, footer, _ = PokemonCollectionDetailsSplit(
            name=pokemon.name,
            level=level,
            id=species,
            shiny=False,
            ability=pokemon.ability,
            type=pokemon.type,
            detail_stats=pokemon.stats,
            attacks=pokemon.attacks,
            base_experience=pokemon.base_experience,
            growth_rate=pokemon.growth_rate,
            ev=pokemon.ev,
            iv=pokemon.iv,
            gender=gender,
            nickname=None,
            individual_id=pokemon.individual_id,
            pokemon_defeated=0,
            everstone=False,
            captured_date="2026-01-01",
            language=9,
            gif_in_collection=False,
            remove_levelcap=False,
            logger=driver.services.logger,
            refresh_callback=lambda: None,
            show_sprites=False,
        )
        window = QWidget()
        layout = QVBoxLayout(window)
        for widget in (header, stats, footer):
            layout.addWidget(widget)
        window.show()
        driver.env.app.processEvents()
        texts = [label.text() for label in header.findChildren(QLabel)]
        assert any(item_target in text for text in texts), texts
        buttons = header.findChildren(QPushButton)
        assert not any("Use Evolution Item" in button.text() for button in buttons)
        evolve = [button for button in buttons if "Evolve into" in button.text()]
        assert len(evolve) == int(target is not None), [
            button.text() for button in buttons
        ]
        if target is not None:
            assert (
                evolve[0].width()
                >= evolve[0].fontMetrics().horizontalAdvance(evolve[0].text()) + 10
            ), (
                evolve[0].width(),
                evolve[0].sizeHint().width(),
                evolve[0].fontMetrics().horizontalAdvance(evolve[0].text()),
            )
            with patch.object(get_evo_window(), "ask_pokemon_evo") as prompt:
                QTest.mouseClick(evolve[0], Qt.MouseButton.LeftButton)
            prompt.assert_called_once_with(pokemon.individual_id, species, target)
        screenshot(window, f"evolution-{species}")
        window.close()
    print(
        "probe_real_recent_merges: OK (SQLite refunds, post-replacement failure, native escape and failure messages, evolution buttons)"
    )
    return True


if __name__ == "__main__":
    assert run_proof()
