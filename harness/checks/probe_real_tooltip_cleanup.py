"""Tier-2 regression probe: battle tooltip widgets must not accumulate.

Run after sourcing the base Tier-2 environment::

    source .tier2/env.sh
    python -m harness.checks.probe_real_tooltip_cleanup
"""

from __future__ import annotations

import gc

from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QApplication

from harness.real_driver import RealDriver


def _custom_labels(app: QApplication):
    return [widget for widget in app.allWidgets() if type(widget).__name__ == "CustomLabel"]


def main() -> int:
    driver = RealDriver(
        settings_overrides={
            "battle.cards_per_round": 1,
            "battle.automatic_battle": 2,
            "audio.sounds": False,
            "audio.sound_effects": False,
            "gui.reviewer_text_message_box_time": 0,
        }
    )
    app = QApplication.instance()
    assert app is not None

    for _ in range(100):
        driver.answer("good")
        app.processEvents()

    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    gc.collect()

    labels = _custom_labels(app)
    assert not labels, f"{len(labels)} expired battle tooltip labels are still alive"
    print("probe_real_tooltip_cleanup: OK (100 reviews, 0 retained CustomLabel widgets)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
