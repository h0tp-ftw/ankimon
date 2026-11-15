"""Repair concurrency and process interruption through the real add-on startup.

Run with the Tier-2 Python/Qt environment in a fresh process. Only the Anki
host, scheduling, user confirmation and interruption timing are substituted.
All profiles are temporary. No network or existing save is used.
"""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
import json
import logging
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def boot(folder):
    folder.mkdir(parents=True, exist_ok=True)
    os.environ["ANKIMON_SPRITE_CACHE"] = str(folder / "no-external-cache")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from harness.real_driver import RealDriver

    original_handler = logging.FileHandler

    def log_handler(filename, *args, **kwargs):
        return original_handler(folder / Path(filename).name, *args, **kwargs)

    with patch.object(logging, "FileHandler", log_handler):
        return RealDriver(user_path=folder, first_run=True, first_encounter=False)


def seed(driver):
    from harness.fixtures import seed_db
    from Ankimon.functions.update_main_pokemon import update_main_pokemon

    seed_db(
        {
            "main": {"species": "Pikachu", "level": 30, "individual_id": "keeper"},
            "box": [{"species": "Eevee", "level": 20, "individual_id": "second"}],
        },
        driver.services.db,
    )
    driver.services.settings.set("trainer.cash", 4321)
    update_main_pokemon(driver.services.main_pokemon)
    for window in driver.env.app.topLevelWidgets():
        if window.windowTitle() == "Choose a Starter":
            window.close()


def on_disk(path):
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "SELECT individual_id, data FROM captured_pokemon ORDER BY individual_id"
        ).fetchall()
        cash = conn.execute(
            "SELECT value FROM config WHERE key='trainer.cash'"
        ).fetchone()
        integrity = conn.execute("PRAGMA quick_check").fetchone()[0]
    return {
        "pokemon": {row[0]: json.loads(row[1]) for row in rows},
        "cash": json.loads(cash[0]),
        "integrity": integrity,
    }


def check_boot(folder):
    driver = boot(folder)
    saved = on_disk(driver.services.db.db_path)
    assert driver.services.startup_finished
    assert sorted(saved["pokemon"]) == ["keeper", "second"]
    assert saved["cash"] == 4321 and saved["integrity"] == "ok"
    assert driver.services.main_pokemon.individual_id == "keeper"
    assert not any(
        widget.isVisible() and widget.windowTitle() == "Choose a Starter"
        for widget in driver.env.app.topLevelWidgets()
    )
    driver.services.db.close()
    return {"full_startup": True, "pokemon": 2, "cash": saved["cash"]}


def repair_race(folder):
    driver = boot(folder)
    seed(driver)
    db = driver.services.db
    from Ankimon.pyobj import database_manager, db_diagnostics
    from Ankimon.functions.mobile_sync import _attribute_xp_and_evs_to_companion
    import aqt.operations

    damaged = db.get_pokemon("keeper")
    damaged.pop("base_stats")
    db.save_pokemon(damaged)
    snapshot_ready, resume, writer_at_lock = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    writer_identity = []
    pending, notices = [], []
    original_connect, lifecycle_lock = sqlite3.connect, db._conn_lock

    class ObservedLock:
        def __enter__(self):
            if writer_identity == [threading.get_ident()]:
                writer_at_lock.set()
            return lifecycle_lock.__enter__()

        def __exit__(self, *args):
            return lifecycle_lock.__exit__(*args)

    class Snapshot(sqlite3.Connection):
        def iterdump(self):
            yield from super().iterdump()
            snapshot_ready.set()
            assert resume.wait(10), "Repair snapshot was not released"

    def connect(database, *args, **kwargs):
        if str(database) == str(db.db_path):
            kwargs["factory"] = Snapshot
        return original_connect(database, *args, **kwargs)

    class QueryOp:
        def __init__(self, *, op, success=None, **kwargs):
            self.op, self.success = op, success

        def without_collection(self):
            return self

        def run_in_background(self):
            if self.op.__name__ == "run_check":
                self.success(pool.submit(self.op, None).result(timeout=10))
            else:
                pending.append((pool.submit(self.op, None), self.success))

    def mobile_writer():
        writer_identity.append(threading.get_ident())
        db.set_config_value("trainer.cash", 9876)
        _attribute_xp_and_evs_to_companion(
            "keeper",
            7,
            {"atk": 2},
            driver.services.settings,
            battles_fought=1,
            db=db,
            logger=driver.services.logger,
        )

    driver.aqt.mw.reviewer.card = None
    driver.aqt.mw.unloadProfile = lambda *args: None
    with (
        ThreadPoolExecutor(max_workers=2) as pool,
        patch.object(aqt.operations, "QueryOp", QueryOp),
        patch.object(db, "_conn_lock", ObservedLock()),
        patch.object(database_manager.sqlite3, "connect", connect),
        patch.object(db_diagnostics, "askUser", return_value=True),
        patch.object(db_diagnostics, "showInfo", notices.append),
        patch.object(db_diagnostics, "showWarning", notices.append),
    ):
        db_diagnostics.trigger_database_diagnostics()
        assert len(pending) == 1 and snapshot_ready.wait(10)
        writer = pool.submit(mobile_writer)
        try:
            assert writer_at_lock.wait(5)
            try:
                writer.result(timeout=0.05)
            except TimeoutError:
                pass
            else:
                raise AssertionError("A writer passed the repair snapshot barrier")
        finally:
            resume.set()
        repair, success = pending[0]
        success(repair.result(timeout=10))
        writer.result(timeout=10)
    saved = on_disk(db.db_path)
    pokemon = saved["pokemon"]["keeper"]
    assert saved["cash"] == 9876 and pokemon["xp"] == 7
    assert pokemon["ev"]["atk"] == 2 and pokemon["pokemon_defeated"] == 1
    assert pokemon["friendship"] > 0 and pokemon["base_stats"]
    assert any("Repair Successful" in notice for notice in notices)
    db.close()
    return {"writer_waited": True, "cash": saved["cash"], "xp": pokemon["xp"]}


def interrupt_repair(folder, when):
    driver = boot(folder)
    target = driver.services.db.db_path
    original_replace = Path.replace

    def replace(source, destination):
        if Path(destination) == target and when == "before":
            os._exit(77)
        result = original_replace(source, destination)
        if Path(destination) == target and when == "after":
            os._exit(77)
        return result

    with patch.object(Path, "replace", replace):
        driver.services.db.repair_database()
    raise AssertionError("Repair publication boundary was not reached")


def legacy_interruption(folder):
    target = folder / "ankimon.db"
    backup = Path(str(target) + ".corrupt_backup")
    assert not target.exists() and backup.exists()
    before = backup.read_bytes()
    try:
        boot(folder)
    except RuntimeError as error:
        assert "repair" in str(error).lower() and str(backup) in str(error)
    else:
        raise AssertionError("Legacy repair interruption created a blank save")
    assert not target.exists() and backup.read_bytes() == before
    return {"legacy_missing_active_refused": True, "recovery_copy_preserved": True}


def child(mode, folder):
    if mode == "seed":
        driver = boot(folder)
        seed(driver)
        driver.services.db.close()
        return {"seeded": True}
    if mode == "race":
        return repair_race(folder)
    if mode == "boot":
        return check_boot(folder)
    if mode == "legacy":
        return legacy_interruption(folder)
    return interrupt_repair(folder, mode)


def run():
    def call(mode, folder, expected=0):
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), mode, str(folder)],
            cwd=ROOT,
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
            text=True,
            capture_output=True,
            timeout=45,
        )
        assert result.returncode == expected, (mode, result.stdout, result.stderr)
        summaries = [
            line[12:]
            for line in result.stdout.splitlines()
            if line.startswith("PROOF_RESULT")
        ]
        return json.loads(summaries[-1]) if summaries else {"exit": expected}

    with tempfile.TemporaryDirectory(prefix="ankimon-recovery-proof-") as directory:
        root = Path(directory)
        results = {"diagnostics_and_mobile_writer": call("race", root / "race")}
        call("seed", root / "seed")
        for boundary in ("before", "after"):
            profile = root / boundary
            shutil.copytree(root / "seed", profile)
            call(boundary, profile, expected=77)
            assert (profile / "ankimon.db").is_file()
            results["restart_" + boundary + "_publication"] = call("boot", profile)
        legacy = root / "legacy"
        shutil.copytree(root / "seed", legacy)
        (legacy / "ankimon.db").replace(legacy / "ankimon.db.corrupt_backup")
        results["legacy_interruption"] = call("legacy", legacy)
        print(json.dumps(results, indent=2))
        print("PASS: native repair writer ordering and full-startup recovery")


if __name__ == "__main__":
    if len(sys.argv) == 3:
        print("PROOF_RESULT" + json.dumps(child(sys.argv[1], Path(sys.argv[2]))))
    else:
        run()
