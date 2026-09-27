"""Real-Qt evolution rendering, persistence and asynchronous lifetime regressions.

Run ``python -m harness.checks.probe_real_evolution_sprites``. Uses a temporary
SQLite profile and colored PNGs, never the shared sprite cache. QueryOp workers
run on real Python threads; delivery is controlled to exercise stale results.
Set ANKIMON_EVOLUTION_SCREENSHOTS to save the rendered prompt and celebration.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QObject, QTimer
from PyQt6.QtGui import QColor, QHideEvent, QImage, QPixmap
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QLabel, QPushButton

from harness.fixtures import build_pokemon
from harness.real_driver import RealDriver


class DeferredQueryOp:
    """Run the genuine worker off-thread, deliver its result on demand on Qt."""

    pending = []

    def __init__(self, *, parent, op, success):
        self.op = op
        self.success = success
        self.on_failure = None
        self.collection_free = False
        self.error = None
        self.result = None
        self.future = None
        self.delivered = False

    def failure(self, callback):
        self.on_failure = callback
        return self

    def without_collection(self):
        self.collection_free = True
        return self

    def run_in_background(self):
        assert self.collection_free, "Cosmetic loading must not lock Anki's collection"
        assert self.on_failure is not None, "Worker errors need a UI failure callback"
        self.pending.append(self)

    def start(self):
        assert self.future is None

        def worker():
            try:
                self.result = self.op(None)
                self.assert_plain_data(self.result)
            except Exception as error:
                self.error = error

        self.future = self.executor.submit(worker)

    @staticmethod
    def assert_plain_data(value):
        assert not isinstance(value, (QObject, QPixmap, QImage)), (
            "Qt value escaped worker"
        )
        if isinstance(value, dict):
            for item in value.values():
                DeferredQueryOp.assert_plain_data(item)
        elif isinstance(value, (tuple, list)):
            for item in value:
                DeferredQueryOp.assert_plain_data(item)

    def finish(self):
        if self.future is None:
            self.start()
        self.future.result(timeout=5)
        assert threading.current_thread() is threading.main_thread()
        assert not self.delivered
        self.delivered = True
        if self.error is None:
            self.success(self.result)
        else:
            self.on_failure(self.error)


class EvolutionSpriteTests(unittest.TestCase):
    NORMAL = {1: "#d52a32", 2: "#28bb47"}
    SHINY = {1: "#274dce", 2: "#ebd72a"}

    @classmethod
    def setUpClass(cls):
        cls.profile = tempfile.TemporaryDirectory(prefix="ankimon_evolution_")
        DeferredQueryOp.executor = ThreadPoolExecutor(max_workers=2)
        # _seed_assets must create local placeholders, never symlink a user's
        # ANKIMON_SPRITE_CACHE or the repository's downloaded sprite directory.
        with patch.dict(
            os.environ,
            {"ANKIMON_SPRITE_CACHE": str(Path(cls.profile.name) / "no-cache")},
        ):
            cls.driver = RealDriver(
                user_path=cls.profile.name,
                first_encounter=False,
                settings_overrides={
                    "audio.sounds": False,
                    "audio.sound_effects": False,
                },
            )
        from Ankimon.functions import sprite_functions
        from Ankimon.pyobj import evolution_window

        cls.sprites = sprite_functions
        cls.module = evolution_window
        cls.db = cls.driver.services.db
        cls.front = Path(cls.profile.name) / "sprites" / "front_default"
        assert not cls.front.parent.is_symlink()

    @classmethod
    def tearDownClass(cls):
        # Match Anki's reusable pool and close registered SQLite connections
        # before its thread-local connection wrappers leave scope.
        cls.db.close()
        DeferredQueryOp.executor.shutdown(wait=True)
        cls.profile.cleanup()

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.errors = []
        self.logs = []
        self.stack.enter_context(
            patch.object(
                sys, "excepthook", lambda kind, value, tb: self.errors.append(value)
            )
        )
        DeferredQueryOp.pending = []
        self.stack.enter_context(patch.object(self.module, "QueryOp", DeferredQueryOp))
        self.driver.set_setting("gui.show_sprites_across_ankimon", True)
        self.sprites._clear_sprite_cache()
        self.write_sprites()
        logger = SimpleNamespace(
            log=lambda level, message: self.logs.append((level, message)),
            log_and_showinfo=lambda level, message: self.logs.append((level, message)),
        )
        self.window = self.module.EvoWindow(
            logger,
            self.driver.services.settings,
            None,
            self.driver.services.translator,
            self.driver.services.reviewer,
            self.driver.services.test_window,
            {},
        )
        # A worker may load plain bytes but must construct no Qt graphics or
        # widgets. Keep real constructors in the GUI callback for pixel checks.
        for name in ("QPixmap", "QPainter", "QLabel", "QPushButton"):
            constructor = getattr(self.module, name)

            def on_gui_thread(*args, _constructor=constructor, **kwargs):
                self.assertIs(threading.current_thread(), threading.main_thread())
                return _constructor(*args, **kwargs)

            self.stack.enter_context(patch.object(self.module, name, on_gui_thread))

    def tearDown(self):
        if not sip.isdeleted(self.window):
            self.window.close()
            self.window.deleteLater()
        self.flush()
        self.assertFalse(self.errors, self.errors)
        self.assertTrue(
            all(op.future is None or op.future.done() for op in DeferredQueryOp.pending)
        )

    def flush(self):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.driver.env.app.processEvents()

    def write_sprites(self):
        for directory, colors in (
            (self.front, self.NORMAL),
            (self.front / "shiny", self.SHINY),
        ):
            directory.mkdir(parents=True, exist_ok=True)
            for species, color in colors.items():
                image = QImage(96, 96, QImage.Format.Format_ARGB32)
                image.fill(QColor(color))
                self.assertTrue(image.save(str(directory / f"{species}.png"), "PNG"))

    def pokemon(self, shiny):
        pokemon = build_pokemon(
            {"id": 1, "level": 16, "moves": ["tackle"], "shiny": shiny}
        )
        self.assertTrue(self.db.save_pokemon(pokemon.to_dict()))
        return pokemon.individual_id

    def buttons(self):
        return {
            button.text(): button
            for button in self.window.findChildren(QPushButton)
            if button.isVisible()
        }

    def canvas(self):
        images = [
            label.pixmap().toImage()
            for label in self.window.findChildren(QLabel)
            if label.isVisible()
            and label.pixmap() is not None
            and not label.pixmap().isNull()
        ]
        self.assertEqual(
            len(images), 1, "Expected exactly one rendered evolution canvas"
        )
        return images[0]

    def prompt_colors(self, source, target):
        image = self.canvas()
        self.assertEqual(image.pixelColor(260, 75).name(), source)
        self.assertEqual(image.pixelColor(260, 290).name(), target)
        self.assertIn("Evolve Pokémon", self.buttons())
        self.assertIn("Cancel Evolution", self.buttons())

    def complete_color(self, target):
        self.assertEqual(self.canvas().pixelColor(130, 15).name(), target)
        self.assertEqual(set(self.buttons()), {"Close"})

    def finish_latest(self):
        DeferredQueryOp.pending[-1].finish()
        self.flush()

    def screenshot(self, name):
        output = os.environ.get("ANKIMON_EVOLUTION_SCREENSHOTS")
        if output:
            path = Path(output)
            path.mkdir(parents=True, exist_ok=True)
            self.assertTrue(self.window.grab().save(str(path / name)))

    def test_shiny_and_ordinary_prompt_and_completion(self):
        for shiny, colors in ((True, self.SHINY), (False, self.NORMAL)):
            with self.subTest(shiny=shiny):
                iid = self.pokemon(shiny)
                threads = []
                get_pokemon = self.db.get_pokemon
                get_sprite_path = self.module.get_sprite_path

                def resolve(*args, **kwargs):
                    self.assertIsNot(
                        threading.current_thread(), threading.main_thread()
                    )
                    return get_sprite_path(*args, **kwargs)

                def lookup(value):
                    threads.append(threading.current_thread())
                    return get_pokemon(value)

                with (
                    patch.object(self.db, "get_pokemon", side_effect=lookup) as get,
                    patch.object(
                        self.module, "get_sprite_path", side_effect=resolve
                    ) as resolver,
                ):
                    self.window.ask_pokemon_evo(iid, 1, 2)
                    self.assertTrue(self.window.isVisible())
                    get.assert_not_called()
                    resolver.assert_not_called()
                    self.assertNotIn("Evolve Pokémon", self.buttons())
                    self.assertIn("Close", self.buttons())
                    self.finish_latest()
                    get.assert_called_once_with(iid)
                    self.assertIsNot(threads[0], threading.main_thread())
                    self.prompt_colors(colors[1], colors[2])
                    self.screenshot(f"{'shiny' if shiny else 'ordinary'}-prompt.png")
                    get.reset_mock()
                    self.window.display_evo_complete(1, 2, shiny)
                    self.finish_latest()
                    get.assert_not_called()
                    self.complete_color(colors[2])
                    self.screenshot(f"{'shiny' if shiny else 'ordinary'}-complete.png")

    def test_missing_shiny_source_and_target_use_ordinary_independently(self):
        iid = self.pokemon(True)
        for missing in ((1,), (2,), (1, 2)):
            with self.subTest(missing=missing):
                self.write_sprites()
                for species in missing:
                    (self.front / "shiny" / f"{species}.png").unlink()
                self.sprites._clear_sprite_cache()
                self.window.ask_pokemon_evo(iid, 1, 2)
                self.finish_latest()
                colors = {
                    species: (self.NORMAL if species in missing else self.SHINY)[
                        species
                    ]
                    for species in (1, 2)
                }
                self.prompt_colors(colors[1], colors[2])
                self.window.display_evo_complete(1, 2, True)
                self.finish_latest()
                self.complete_color(colors[2])

    def test_missing_all_images_is_harmless(self):
        iid = self.pokemon(True)
        for path in self.front.rglob("*.png"):
            path.unlink()
        self.sprites._clear_sprite_cache()
        self.window.ask_pokemon_evo(iid, 1, 2)
        self.finish_latest()
        self.canvas()
        self.assertIn("Evolve Pokémon", self.buttons())
        self.window.display_evo_complete(1, 2, True)
        self.finish_latest()
        self.canvas()
        self.assertEqual(set(self.buttons()), {"Close"})
        self.assertFalse([entry for entry in self.logs if entry[0] == "error"])

    def test_hidden_sprites_skip_cosmetic_database_and_sprite_work(self):
        self.driver.set_setting("gui.show_sprites_across_ankimon", False)
        with (
            patch.object(
                self.db,
                "get_pokemon",
                side_effect=AssertionError("hidden sprites queried DB"),
            ) as get,
            patch.object(
                self.module,
                "get_sprite_path",
                side_effect=AssertionError("hidden sprites resolved assets"),
            ) as resolve,
        ):
            self.window.ask_pokemon_evo("not-in-database", 1, 2)
            self.flush()
            self.assertIn("Evolve Pokémon", self.buttons())
            self.assertNotIn(
                self.canvas().pixelColor(260, 75).name(),
                (self.NORMAL[1], self.SHINY[1]),
            )
            self.window.display_evo_complete(1, 2, True)
            self.flush()
            self.assertEqual(set(self.buttons()), {"Close"})
            self.assertEqual(DeferredQueryOp.pending, [])
            get.assert_not_called()
            resolve.assert_not_called()

    def test_lookup_error_is_logged_and_retry_recovers_without_false_ordinary_sprite(
        self,
    ):
        iid = self.pokemon(True)
        with patch.object(
            self.db,
            "get_pokemon",
            side_effect=sqlite3.OperationalError("database is locked"),
        ):
            self.window.ask_pokemon_evo(iid, 1, 2)
            self.finish_latest()
        self.assertTrue(
            any(
                level == "error" and "database is locked" in message
                for level, message in self.logs
            )
        )
        self.assertNotIn("Evolve Pokémon", self.buttons())
        self.assertIn("Close", self.buttons())
        self.assertIn("Retry", self.buttons())
        self.assertTrue(
            all(
                label.pixmap() is None or label.pixmap().isNull()
                for label in self.window.findChildren(QLabel)
            )
        )
        self.buttons()["Retry"].click()
        self.finish_latest()
        self.prompt_colors(self.SHINY[1], self.SHINY[2])

    def test_worker_submission_failure_is_logged_with_retry_and_close(self):
        with patch.object(
            DeferredQueryOp,
            "run_in_background",
            side_effect=RuntimeError("executor stopped"),
        ):
            self.window.ask_pokemon_evo(self.pokemon(True), 1, 2)
        self.flush()
        self.assertTrue(
            any(
                level == "error" and "executor stopped" in message
                for level, message in self.logs
            )
        )
        self.assertEqual(set(self.buttons()), {"Retry", "Close"})
        self.buttons()["Retry"].click()
        self.finish_latest()
        self.prompt_colors(self.SHINY[1], self.SHINY[2])

    def test_gui_render_failure_is_logged_with_retry_and_close(self):
        self.window.ask_pokemon_evo(self.pokemon(True), 1, 2)
        with patch.object(
            self.module, "QPainter", side_effect=RuntimeError("cannot create painter")
        ):
            self.finish_latest()
        self.assertTrue(
            any(
                level == "error" and "cannot create painter" in message
                for level, message in self.logs
            )
        )
        self.assertEqual(set(self.buttons()), {"Retry", "Close"})
        self.buttons()["Retry"].click()
        self.finish_latest()
        self.prompt_colors(self.SHINY[1], self.SHINY[2])

    def test_sprite_preference_is_rechecked_after_pending_work(self):
        for screen in ("prompt", "completion"):
            with self.subTest(screen=screen):
                self.driver.set_setting("gui.show_sprites_across_ankimon", True)
                if screen == "prompt":
                    self.window.ask_pokemon_evo(self.pokemon(True), 1, 2)
                else:
                    self.window.display_evo_complete(1, 2, True)
                self.driver.set_setting("gui.show_sprites_across_ankimon", False)
                self.finish_latest()
                x, y = (260, 75) if screen == "prompt" else (130, 15)
                self.assertNotIn(
                    self.canvas().pixelColor(x, y).name(), self.SHINY.values()
                )
                self.assertNotIn("Retry", self.buttons())

    def test_deleted_pokemon_cannot_be_displayed_as_ordinary(self):
        self.window.ask_pokemon_evo("missing-individual", 1, 2)
        self.finish_latest()
        self.assertNotIn("Evolve Pokémon", self.buttons())
        self.assertIn("Close", self.buttons())
        self.assertTrue(any(level == "error" for level, message in self.logs))

    def test_evolution_preserves_shiny_and_reuses_saved_record_for_completion(self):
        from Ankimon import singletons

        iid = self.pokemon(True)
        self.window.ask_pokemon_evo(iid, 1, 2)
        self.finish_latest()
        get_pokemon = self.db.get_pokemon
        with (
            patch.object(self.db, "get_pokemon", wraps=get_pokemon) as get,
            patch.object(singletons, "pokemon_pc") as pc,
        ):
            pc.isVisible.return_value = False
            self.buttons()["Evolve Pokémon"].click()
            self.assertFalse(self.errors, self.errors)
            get.assert_called_once_with(iid)
            self.finish_latest()
            get.assert_called_once_with(iid)
            self.complete_color(self.SHINY[2])
        saved = get_pokemon(iid)
        self.assertEqual(saved["id"], 2)
        self.assertIs(saved["shiny"], True)

    def test_newer_request_wins_out_of_order_callbacks(self):
        self.window.ask_pokemon_evo(self.pokemon(True), 1, 2)
        old = DeferredQueryOp.pending[-1]
        self.window.ask_pokemon_evo(self.pokemon(False), 1, 2)
        self.finish_latest()
        old.finish()
        self.flush()
        self.prompt_colors(self.NORMAL[1], self.NORMAL[2])

    def test_close_hide_and_widget_deletion_discard_pending_results(self):
        iid = self.pokemon(True)
        for action in (self.window.close, self.window.hide):
            self.window.ask_pokemon_evo(iid, 1, 2)
            pending = DeferredQueryOp.pending[-1]
            action()
            pending.finish()
            self.flush()
            self.assertFalse(self.window.isVisible())
        self.window.ask_pokemon_evo(iid, 1, 2)
        pending = DeferredQueryOp.pending[-1]
        self.window.deleteLater()
        self.flush()
        self.assertTrue(sip.isdeleted(self.window))
        pending.finish()
        self.assertFalse(self.errors, self.errors)

    def test_profile_and_database_changes_discard_pending_results(self):
        iid = self.pokemon(True)
        for owner, name, changed in (
            (self.driver.services, "db", object()),
            (self.db, "db_path", Path(self.profile.name) / "different.db"),
            (self.module.mw, "col", object()),
        ):
            with self.subTest(attribute=name):
                self.window.ask_pokemon_evo(iid, 1, 2)
                pending = DeferredQueryOp.pending[-1]
                pending.start()
                pending.future.result(timeout=5)
                with patch.object(owner, name, changed):
                    pending.finish()
                    self.flush()
                    self.assertNotIn("Evolve Pokémon", self.buttons())

    def test_stale_failure_does_not_replace_newer_completion(self):
        self.window.ask_pokemon_evo("missing-individual", 1, 2)
        failed = DeferredQueryOp.pending[-1]
        self.window.display_evo_complete(1, 2, True)
        self.finish_latest()
        failed.finish()
        self.flush()
        self.complete_color(self.SHINY[2])

    def test_stale_buttons_cannot_evolve_or_cancel_after_new_request(self):
        iid = self.pokemon(True)
        self.window.ask_pokemon_evo(iid, 1, 2)
        self.finish_latest()
        old_buttons = self.buttons()
        self.window.display_evo_complete(1, 2, True)
        with (
            patch.object(self.window, "evolve_pokemon") as evolve,
            patch.object(self.window, "cancel_evolution") as cancel,
        ):
            # Qt deletes replaced widgets later; queued clicks can still arrive.
            old_buttons["Evolve Pokémon"].click()
            old_buttons["Cancel Evolution"].click()
            evolve.assert_not_called()
            cancel.assert_not_called()
        self.finish_latest()
        self.complete_color(self.SHINY[2])

    def test_profile_change_invalidates_visible_action_buttons(self):
        iid = self.pokemon(True)
        self.window.ask_pokemon_evo(iid, 1, 2)
        self.finish_latest()
        buttons = self.buttons()
        with (
            patch.object(self.module.mw, "col", object()),
            patch.object(self.window, "evolve_pokemon") as evolve,
            patch.object(self.window, "cancel_evolution") as cancel,
        ):
            buttons["Evolve Pokémon"].click()
            buttons["Cancel Evolution"].click()
            evolve.assert_not_called()
            cancel.assert_not_called()

    def test_minimize_restore_preserves_pending_and_ready_prompts(self):
        iid = self.pokemon(True)
        for pending in (True, False):
            with self.subTest(pending=pending):
                self.window.ask_pokemon_evo(iid, 1, 2)
                if not pending:
                    self.finish_latest()
                self.window.showMinimized()
                self.driver.env.app.processEvents()
                self.assertTrue(self.window.isMinimized())
                if pending:
                    self.finish_latest()
                self.window.showNormal()
                self.flush()
                self.prompt_colors(self.SHINY[1], self.SHINY[2])
                with patch.object(self.window, "evolve_pokemon") as evolve:
                    self.buttons()["Evolve Pokémon"].click()
                    evolve.assert_called_once()

    def test_minimized_nonspontaneous_hide_preserves_pending_and_ready_prompts(self):
        iid = self.pokemon(True)
        for pending in (True, False):
            with self.subTest(pending=pending):
                self.window.ask_pokemon_evo(iid, 1, 2)
                if not pending:
                    self.finish_latest()
                self.window.showMinimized()
                self.driver.env.app.processEvents()
                self.assertTrue(self.window.isMinimized())
                self.assertFalse(self.window.isHidden())
                # Synthetic portability guard: the request depends on explicit
                # widget visibility, not a platform's event-spontaneity choice.
                event = QHideEvent()
                self.assertFalse(event.spontaneous())
                QCoreApplication.sendEvent(self.window, event)
                if pending:
                    self.finish_latest()
                self.window.showNormal()
                self.flush()
                self.prompt_colors(self.SHINY[1], self.SHINY[2])
                with patch.object(self.window, "evolve_pokemon") as evolve:
                    self.buttons()["Evolve Pokémon"].click()
                    evolve.assert_called_once()

    def test_explicit_hide_while_minimized_invalidates_pending_and_ready_prompts(self):
        iid = self.pokemon(True)
        for pending in (True, False):
            with self.subTest(pending=pending):
                self.window.showNormal()
                self.window.ask_pokemon_evo(iid, 1, 2)
                if not pending:
                    self.finish_latest()
                buttons = self.buttons()
                self.window.showMinimized()
                self.driver.env.app.processEvents()
                self.assertTrue(self.window.isMinimized())
                self.window.hide()
                self.assertTrue(self.window.isHidden())
                if pending:
                    self.finish_latest()
                else:
                    with (
                        patch.object(self.window, "evolve_pokemon") as evolve,
                        patch.object(self.window, "cancel_evolution") as cancel,
                    ):
                        buttons["Evolve Pokémon"].click()
                        buttons["Cancel Evolution"].click()
                        evolve.assert_not_called()
                        cancel.assert_not_called()
                self.flush()
                self.assertTrue(self.window.isHidden())
                self.assertFalse(self.window.isVisible())

    def test_old_close_buttons_cannot_dismiss_newer_prompt(self):
        iid = self.pokemon(True)
        for screen in ("loading", "completion", "failure"):
            with self.subTest(screen=screen):
                if screen == "completion":
                    self.window.display_evo_complete(1, 2, True)
                    self.finish_latest()
                else:
                    self.window.ask_pokemon_evo(
                        "missing" if screen == "failure" else iid, 1, 2
                    )
                    if screen == "failure":
                        self.finish_latest()
                close = self.buttons()["Close"]
                self.window.ask_pokemon_evo(iid, 1, 2)
                close.click()
                self.assertTrue(self.window.isVisible())
                self.finish_latest()
                self.prompt_colors(self.SHINY[1], self.SHINY[2])

    def test_qt_heartbeat_continues_while_database_lookup_is_blocked(self):
        iid = self.pokemon(True)
        entered = threading.Event()
        release = threading.Event()
        ticks = []
        get_pokemon = self.db.get_pokemon

        def blocked_lookup(value):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test did not release database worker")
            return get_pokemon(value)

        timer = QTimer()
        timer.setInterval(10)
        timer.timeout.connect(lambda: ticks.append(True))
        with patch.object(self.db, "get_pokemon", side_effect=blocked_lookup):
            self.window.ask_pokemon_evo(iid, 1, 2)
            pending = DeferredQueryOp.pending[-1]
            pending.start()
            try:
                self.assertTrue(entered.wait(2))
                timer.start()
                QTest.qWait(120)
                self.assertGreaterEqual(len(ticks), 2)
                self.assertFalse(pending.future.done())
                self.assertTrue(self.window.isVisible())
                self.assertIn("Close", self.buttons())
            finally:
                timer.stop()
                release.set()
                pending.finish()
        self.flush()
        self.prompt_colors(self.SHINY[1], self.SHINY[2])


if __name__ == "__main__":
    unittest.main(verbosity=2)
