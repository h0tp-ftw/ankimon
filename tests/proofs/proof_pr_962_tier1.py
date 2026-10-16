"""Independent PR #962 proof: real SQLite payment and live EV publication.

Run directly or with the PR review proof runner. The bag's production action
method is compiled from its source to avoid importing Qt into Tier 1; only
display callbacks are replaced. PokemonObject and all database writes are real.
"""

from __future__ import annotations

import ast
from contextlib import closing
import copy
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class RecordingLogger:
    def __init__(self):
        self.messages = []

    def log(self, level, message):
        self.messages.append((level, message))

    def log_and_showinfo(self, level, message):
        self.messages.append((level, message))


def _action_without_qt(services):
    from Ankimon.functions.ev_items import apply_ev_item

    filename = ROOT / "src/Ankimon/pyobj/item_window.py"
    tree = ast.parse(filename.read_text(encoding="utf-8"))
    item_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ItemWindow"
    )
    action = next(
        node
        for node in item_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "_use_ev_item"
    )
    namespace = {
        "__name__": "Ankimon.pyobj.item_window",
        "__package__": "Ankimon.pyobj",
        "copy": copy,
        "services": services,
        "apply_ev_item": apply_ev_item,
        "play_effect_sound": lambda *_: None,
    }
    exec(
        compile(ast.Module(body=[action], type_ignores=[]), str(filename), "exec"),
        namespace,
    )
    return namespace["_use_ev_item"]


def _disk_main(db):
    with closing(sqlite3.connect(db.db_path)) as conn:
        row = conn.execute(
            "SELECT data FROM captured_pokemon WHERE is_main = 1"
        ).fetchone()
    return db._deobfuscate(row[0]) if row else None


def _replace_on_other_connection(db, record):
    with closing(sqlite3.connect(db.db_path)) as conn:
        conn.execute(
            "UPDATE captured_pokemon SET data = ? WHERE individual_id = ?",
            (db._obfuscate(record), record["individual_id"]),
        )
        conn.commit()


def run_proof():
    from harness.driver import Driver

    with tempfile.TemporaryDirectory(prefix="ankimon-pr962-independent-") as directory:
        driver = Driver(
            user_path=directory,
            seed={
                "main": {"species": "Mewtwo", "level": 50, "hp": 20},
                "box": [{"species": "Mew", "level": 25}],
            },
            first_encounter=False,
            settings_overrides={"audio.sounds": False, "audio.sound_effects": False},
        )
        from Ankimon.functions.mobile_sync import _mobile_sync_lock

        services = driver.services
        db, main = services.db, services.main_pokemon
        action = _action_without_qt(services)
        logger = RecordingLogger()
        publications = []
        bag = SimpleNamespace(
            main_pokemon=main,
            logger=logger,
            settings_obj=services.settings,
            ev_items={"protein", "health-candy-xl"},
            _refresh_ev_training_state=lambda *args: publications.append(args),
            _refresh_bag=lambda: None,
        )

        def use(item="protein"):
            return action(bag, item)

        def stock(quantity=1, item="protein"):
            db.save_item(None, item, quantity, extra_data={"source": "proof"})

        # A newly saved reward is authoritative even if the object is behind.
        # Combat HP and statuses can instead be newer than the saved record.
        main.hp = main.current_hp = 20
        main.battle_status = "par"
        main.stat_stages["atk"] = 2
        main.volatile_status.add("substitute")
        rewarded = copy.deepcopy(db.get_main_pokemon())
        rewarded.update(xp=47, friendship=9, custom_metadata={"preserved": [1, 2]})
        rewarded["ev"]["atk"] = 3
        rewarded["hp"] = rewarded["current_hp"] = 99
        db.save_main_pokemon(rewarded)
        stock()
        assert use() == (True, 10)
        saved = _disk_main(db)
        assert saved["ev"]["atk"] == main.ev["atk"] == 13
        assert saved["xp"] == main.xp == 47
        assert saved["friendship"] == main.friendship == 9
        assert saved["custom_metadata"] == {"preserved": [1, 2]}
        assert saved["hp"] == saved["current_hp"] == main.hp == 20
        assert main.battle_status == "par" and main.stat_stages["atk"] == 2
        assert "substitute" in main.volatile_status
        assert db.get_item("protein") is None

        # Increasing maximum HP must preserve both wounds and fainted HP.
        for hp in (20, 0):
            main.update_stats(ev={key: 0 for key in main.ev}, hp=hp, current_hp=hp)
            db.save_main_pokemon(main.to_dict())
            old_maximum = main.max_hp
            stock(item="health-candy-xl")
            assert use("health-candy-xl") == (True, 252)
            assert main.max_hp > old_maximum
            assert main.hp == main.current_hp == _disk_main(db)["hp"] == hp

        # A database selection change cannot charge or reselect an old window.
        before_main = copy.deepcopy(main.to_dict())
        bench = next(
            p for p in db.get_all_pokemon() if p["individual_id"] != main.individual_id
        )
        db.save_main_pokemon(bench)
        stock()
        assert use() == (False, 0)
        assert _disk_main(db)["individual_id"] == bench["individual_id"]
        assert main.to_dict() == before_main
        assert db.get_item("protein")["quantity"] == 1
        db.save_main_pokemon(main.to_dict())

        # Separate connections change stock and rewards between candidate
        # preparation and writer-lock acquisition. Neither can be overwritten.
        transaction = db.consume_item_and_save_main_pokemon
        before_live = copy.deepcopy(main.to_dict())
        before_saved = copy.deepcopy(_disk_main(db))

        def empty_stock(item, record, **kwargs):
            with closing(sqlite3.connect(db.db_path)) as conn:
                conn.execute("DELETE FROM items WHERE item_name = ?", (item,))
                conn.commit()
            return transaction(item, record, **kwargs)

        with patch.object(db, "consume_item_and_save_main_pokemon", new=empty_stock):
            assert use() == (False, 0)
        assert _disk_main(db) == before_saved and main.to_dict() == before_live

        stock()
        concurrent = copy.deepcopy(before_saved)
        concurrent.update(xp=123, friendship=29)

        def reward_first(item, record, **kwargs):
            _replace_on_other_connection(db, concurrent)
            return transaction(item, record, **kwargs)

        with patch.object(db, "consume_item_and_save_main_pokemon", new=reward_first):
            assert use() == (False, 0)
        assert _disk_main(db) == concurrent
        assert main.to_dict() == before_live
        assert db.get_item("protein")["quantity"] == 1

        # Abort after the stock decrement and ensure later commits do not
        # accidentally make any part of the failed payment durable.
        db.execute(
            "CREATE TEMP TRIGGER pr962_fail_update BEFORE UPDATE OF data "
            "ON captured_pokemon BEGIN SELECT RAISE(ABORT, 'proof failure'); END"
        ).close()
        publication_count = len(publications)
        assert use() == (False, 0)
        db.execute("DROP TRIGGER pr962_fail_update").close()
        db.set_user_data("unrelated_successful_write", True)
        assert _disk_main(db) == concurrent
        assert main.to_dict() == before_live
        assert db.get_item("protein")["quantity"] == 1
        assert len(publications) == publication_count
        assert not db._get_connection().in_transaction

        # A genuine failed COMMIT (reader lock) must also roll everything back.
        conn = db._get_connection()
        conn.execute("PRAGMA busy_timeout = 25").close()
        with closing(sqlite3.connect(db.db_path)) as reader:
            reader.execute("BEGIN")
            reader.execute("SELECT * FROM items").fetchall()
            assert use() == (False, 0)
            reader.rollback()
        assert _disk_main(db) == concurrent
        assert db.get_item("protein")["quantity"] == 1
        assert not conn.in_transaction

        # Once SQLite commits, a live-publication failure must report success
        # and must not promise that the item and save were unchanged.
        before_attack = concurrent["ev"]["atk"]
        original_update = type(main).update_stats

        def fail_live_only(pokemon, **data):
            if pokemon is main:
                raise RuntimeError("postcommit live publication fault")
            return original_update(pokemon, **data)

        logger.messages.clear()
        with patch.object(type(main), "update_stats", new=fail_live_only):
            assert use() == (True, 10), "a committed payment was reported as refused"
        assert db.get_item("protein") is None
        assert _disk_main(db)["ev"]["atk"] == before_attack + 10
        assert any(level == "warning" for level, _ in logger.messages)
        assert not any("unchanged" in message.lower() for _, message in logger.messages)
        assert _mobile_sync_lock.acquire(blocking=False), (
            "item action leaked mobile lock"
        )
        _mobile_sync_lock.release()
        db.close()

    print(
        "PASS PR #962 Tier 1: real SQLite stock/reward races, stale main, HP, "
        "SQL and COMMIT rollback, postcommit publication reporting"
    )
    return True


if __name__ == "__main__":
    raise SystemExit(0 if run_proof() else 1)
