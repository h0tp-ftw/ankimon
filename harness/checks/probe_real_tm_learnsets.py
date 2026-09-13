"""Real modal TM learning through both screens, backed by throwaway SQLite.

Run ``python -m harness.checks.probe_real_tm_learnsets``. Optionally set
ANKIMON_TM_SCREENSHOTS to an artifact directory for Aegislash/Tauros screenshots.
Only the TM picker's native modal loop is restored; unrelated startup dialogs
keep the harness's non-blocking stub.
"""

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, Qt, QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QDialog

from harness.fixtures import build_pokemon
from harness.real_driver import RealDriver

_REAL_EXEC = QDialog.exec

# Independent expected bundled tables, not keys derived by the resolver.
CASES = (
    (681, "aegislashshield", "protect"),
    (386, "deoxysnormal", "protect"),
    (678, "meowsticmale", "protect"),
    (10025, "meowsticfemale", "protect"),
    (876, "indeedeemale", "protect"),
    (10186, "indeedeefemale", "protect"),
    (925, "mausholdfamilyoffour", "protect"),
    (931, "squawkabillygreenplumage", "protect"),
    (10251, "taurospaldeablazebreed", "willowisp"),
    (10177, "darmanitangalarstandard", "protect"),
    (10149, "marowaktotem", "shadowball"),
    (10228, "toxtricitylowkey", "thunderbolt"),
    (10227, "urshifurapidstrike", "waterfall"),
    (10296, "floetteeternal", "protect"),
    (10318, "magearnaoriginal", "protect"),
)
# Include tempting but illegal base-species TMs: Blaze Tauros must not show
# Thunderbolt/Surf, Floette-Mega must not show Light Screen, and Original
# Magearna-Mega must not show Metal Sound/Gravity.
OWNED = {
    "protect",
    "willowisp",
    "thunderbolt",
    "surf",
    "shadowball",
    "waterfall",
    "lightscreen",
    "metalsound",
    "gravity",
}


class TMLearningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.driver = RealDriver(
            first_encounter=False,
            settings_overrides={
                "audio.sounds": False,
                "audio.sound_effects": False,
            },
        )
        from Ankimon.functions import tm_learnset

        cls.tm = tm_learnset
        cls.db = cls.driver.services.db
        for move in OWNED:
            assert cls.db.add_item(move, 1, {"type": "TM"})

    def setUp(self):
        self.widgets = []
        self.errors = []
        self.calls = 0
        self.old_excepthook = sys.excepthook
        sys.excepthook = lambda kind, value, tb: self.errors.append(value)

    def tearDown(self):
        for widget in reversed(self.widgets):
            if not sip.isdeleted(widget):
                widget.close()
                widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.driver.env.app.processEvents()
        sys.excepthook = self.old_excepthook
        self.assertFalse(self.errors, self.errors)
        self.assertFalse(
            [e for e in self.driver.drain_events() if e["type"] == "error"]
        )

    def _exec_picker(self, dialog, species_id, table_key, target, cancel):
        self.widgets.append(dialog)
        self.calls += 1

        def interact():
            try:
                self.assertTrue(dialog.isVisible())
                self.assertTrue(dialog.isModal())
                available = [
                    dialog.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
                    for row in range(dialog.table.rowCount())
                ]
                self.assertEqual(
                    set(available), set(self.tm._tm_learnsets_cache[table_key]) & OWNED
                )
                self.assertIn(target, available)
                dialog.table.selectRow(available.index(target))
                self.assertTrue(dialog.learn_btn.isEnabled())
                artifacts = os.environ.get("ANKIMON_TM_SCREENSHOTS")
                if artifacts and species_id in (681, 10251) and not cancel:
                    directory = Path(artifacts)
                    directory.mkdir(parents=True, exist_ok=True)
                    self.assertTrue(
                        dialog.grab().save(str(directory / f"tm-{species_id}.png"))
                    )
                if cancel:
                    QTest.keyClick(dialog, Qt.Key.Key_Escape)
                else:
                    QTest.mouseClick(dialog.learn_btn, Qt.MouseButton.LeftButton)
            except BaseException as exc:
                # Exceptions raised by Qt callbacks must not reach PyQt's fatal
                # excepthook or leave the nested event loop hanging.
                self.errors.append(exc)
                dialog.reject()

        def timed_out():
            self.errors.append(AssertionError("TM picker modal did not finish"))
            dialog.reject()

        guard = QTimer(dialog)
        guard.setSingleShot(True)
        guard.timeout.connect(timed_out)
        guard.start(5000)
        QTimer.singleShot(0, interact)
        try:
            result = _REAL_EXEC(dialog)
        finally:
            guard.stop()
        self.assertFalse(self.errors, self.errors)
        expected = (
            QDialog.DialogCode.Rejected if cancel else QDialog.DialogCode.Accepted
        )
        self.assertEqual(result, expected)
        return result

    def _learn(self, case, surface, cancel=False):
        from Ankimon.gui_classes.pokemon_details import tm_attack_details_window
        from Ankimon.pyobj.move_picker import MovePickerDialog
        from Ankimon.pyobj.pc_box import MoveManagerWidget

        species_id, table_key, target = case
        pokemon = build_pokemon({"id": species_id, "level": 50, "moves": ["tackle"]})
        iid = pokemon.individual_id
        self.db.save_pokemon(pokemon.to_dict())
        before = self.calls
        logger = self.driver.services.logger
        # Startup must have warmed the actual TM cache. No TM file reads are
        # allowed when clicking either UI surface (other sprite/UI I/O is real).
        self.assertTrue(self.tm._tm_learnsets_cache)
        with (
            patch.object(
                self.tm,
                "open",
                create=True,
                side_effect=AssertionError("cold TM cache"),
            ),
            patch.object(
                MovePickerDialog,
                "exec",
                lambda dialog: self._exec_picker(
                    dialog, species_id, table_key, target, cancel
                ),
            ),
        ):
            if surface == "pc":
                widget = MoveManagerWidget(
                    iid, species_id, logger, self.db.save_pokemon
                )
                self.widgets.append(widget)
                widget.show()
                self.driver.env.app.processEvents()
                QTest.mouseClick(widget.tm_btn, Qt.MouseButton.LeftButton)
            else:
                tm_attack_details_window(species_id, iid, ["tackle"], logger)
        self.assertEqual(self.calls, before + 1, "TM picker was never opened")
        self.assertFalse(self.errors, self.errors)
        expected = ["tackle"] if cancel else ["tackle", target]
        self.assertEqual(self.db.get_pokemon(iid)["attacks"], expected)

    def test_form_tms_are_filtered_learned_and_saved_from_both_screens(self):
        for case in CASES:
            for surface in ("pc", "details"):
                with self.subTest(species=case[0], surface=surface):
                    self._learn(case, surface)

    def test_cancelling_either_picker_leaves_saved_moves_unchanged(self):
        for surface in ("pc", "details"):
            with self.subTest(surface=surface):
                self._learn(CASES[0], surface, cancel=True)


if __name__ == "__main__":
    unittest.main()
