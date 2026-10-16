"""Tier 2: click through a failed migration and Retry with real Qt and SQLite.

Run: python3 -m harness.checks.probe_real_migration [screenshot-directory]
All saves and screenshots use disposable directories outside the shipped add-on.
"""

import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

from harness.real_env import start_real_session


def main():
    session = start_real_session()
    from Ankimon.pyobj.database_manager import AnkimonDB
    from Ankimon.pyobj.migration_dialog import MigrationDialog
    from harness.fixtures import build_pokemon

    output = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else Path(tempfile.mkdtemp(prefix="ankimon-migration-qa-"))
    )
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ankimon-legacy-save-") as directory:
        root = Path(directory)
        box = []
        for i in range(158):
            pokemon = build_pokemon(
                {
                    "species": "Pikachu",
                    "level": i % 80 + 5,
                    "iv": {"hp": i % 32, "atk": i // 32},
                }
            ).to_dict()
            pokemon.pop("individual_id", None)
            box.append(pokemon)
        box[0]["individual_id"] = "main"
        newer_main = dict(box[0], level=box[0]["level"] + 1)
        team_main = {k: v for k, v in newer_main.items() if k != "individual_id"}
        sources = {
            "mypokemon": box,
            "mainpokemon": [newer_main],
            "items": ["pass-orb", "old-gateau", "pass-orb"],
            "badges": list(range(1, 15)),
            "team": [team_main, box[1]],
        }
        paths = {}
        for name, data in sources.items():
            path = root / f"{name}.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            paths[f"{name}_path"] = path
        db = AnkimonDB(db_path=root / "ankimon.db")
        dialog = MigrationDialog(db, **paths)
        dialog.show()
        session.app.processEvents()
        original = db.add_item

        def fail_item(name, *args, **kwargs):
            if name == "old-gateau":
                raise RuntimeError("Injected inventory write failure")
            return original(name, *args, **kwargs)

        with patch.object(db, "add_item", side_effect=fail_item):
            dialog.start_button.click()
        assert not dialog.migration_successful
        assert db.get_pokemon_count() == 158
        assert not db.is_migrated()
        assert db.get_item("pass-orb") is None
        assert paths["mypokemon_path"].is_file()
        assert dialog.start_button.isEnabled()
        assert not dialog.continue_button.isVisible()
        session.app.processEvents()
        assert dialog.grab().save(str(output / "migration-incomplete.png"))

        dialog.start_button.click()
        assert dialog.migration_successful, dialog.log_area.toPlainText()
        assert db.get_pokemon_count() == 158
        assert db.is_migrated()
        assert db.get_item("pass-orb")["quantity"] == 2
        assert len(db.get_team()) == 2
        assert db.get_main_pokemon() == newer_main
        assert db.get_team()[0] == {"individual_id": "main"}
        assert json.loads((root / "json/mypokemon.json").read_text()) == box
        assert dialog.continue_button.isVisible()
        assert not dialog.start_button.isVisible()
        session.app.processEvents()
        assert dialog.grab().save(str(output / "migration-complete.png"))
        dialog.continue_button.click()
        assert dialog.result() == dialog.DialogCode.Accepted
        db.close()
    probe_retry_boundaries(session, output)
    print(
        f"probe_real_migration: OK — 158 Pokemon preserved after failure/retry; screenshots: {output}"
    )


def probe_retry_boundaries(session, output):
    from Ankimon.pyobj.database_manager import AnkimonDB
    from Ankimon.pyobj.migration_dialog import MigrationDialog
    from harness.fixtures import build_pokemon

    for scenario in (
        "collection-failure",
        "pending-main",
        "old-marker",
        "repaired-source",
        "known-verification-failure",
        "pending-idless-progress",
        "pending-idless-release",
        "pending-explicit-release",
        "pending-mixed-unchanged",
        "pending-mixed-progress",
        "pending-mixed-release",
        "duplicate-insert",
        "duplicate-checkpoint",
        "duplicate-second-insert",
        "duplicate-second-checkpoint",
        "duplicate-both-insert",
        "duplicate-both-checkpoint",
    ):
        with tempfile.TemporaryDirectory(prefix="ankimon-overlap-") as directory:
            root = Path(directory)
            captured = build_pokemon({"species": "Pikachu", "level": 10}).to_dict()
            captured["individual_id"] = "main"
            other = dict(captured, individual_id="other")
            main = dict(captured, level=11)
            paths = {}
            for name, data in {
                "mypokemon": [captured, other],
                "mainpokemon": [main],
                "items": [],
                "badges": [],
                "team": [],
            }.items():
                path = root / f"{name}.json"
                path.write_text(json.dumps(data), encoding="utf-8")
                paths[f"{name}_path"] = path
            db = AnkimonDB(db_path=root / "ankimon.db")
            dialog = MigrationDialog(db, **paths)
            dialog.show()
            session.app.processEvents()
            if scenario == "collection-failure":
                save = db.save_pokemon

                def fail_other(pokemon):
                    return (
                        False if pokemon["individual_id"] == "other" else save(pokemon)
                    )

                with patch.object(db, "save_pokemon", side_effect=fail_other):
                    dialog.start_button.click()
                assert not dialog.migration_successful
                dialog.start_button.click()
                assert dialog.migration_successful, dialog.log_area.toPlainText()
                assert db.get_main_pokemon() == main
            elif scenario == "pending-main":
                progress = dialog._update_progress

                def cancel_at_summary(percent, message):
                    progress(percent, message)
                    if "Pokemon verified" in message:
                        dialog.cancelled = True

                with patch.object(
                    dialog, "_update_progress", side_effect=cancel_at_summary
                ):
                    dialog.start_button.click()
                assert not dialog.migration_successful
                live = dict(db.get_pokemon("main"), level=20)
                db.save_pokemon(live)
                dialog.start_button.click()
                assert dialog.migration_successful, dialog.log_area.toPlainText()
                assert db.get_main_pokemon() == live
            elif scenario == "old-marker":
                db.save_pokemon(other)
                db.add_to_history(captured)
                db.execute("INSERT INTO metadata VALUES ('migrated', 'true')")
                db._get_connection().commit()
                for _ in range(2):
                    dialog.start_button.click()
                    assert not dialog.migration_successful
                    assert db.get_all_pokemon() == [other]
                    assert paths["mypokemon_path"].exists()
                    assert "explicit recovery" in dialog.log_area.toPlainText().lower()
            elif scenario == "repaired-source":
                paths["mypokemon_path"].write_text(json.dumps([captured, "bad", other]))
                dialog.start_button.click()
                assert not dialog.migration_successful
                live = dict(other, level=99, nickname="Keep me")
                db.save_pokemon(live)
                paths["mypokemon_path"].write_text(json.dumps([captured, other]))
                dialog.start_button.click()
                assert not dialog.migration_successful
                assert not db.is_migrated()
                assert db.get_pokemon("other") == live
                assert db.get_pokemon_count() == 2
                assert paths["mypokemon_path"].exists()
                assert "reconciliation" in dialog.log_area.toPlainText().lower()
            elif scenario.startswith("duplicate-"):
                from Ankimon.pyobj.legacy_migration import LegacyMigration

                captured["individual_id"] = "duplicate"
                other = build_pokemon({"species": "Bulbasaur", "level": 10}).to_dict()
                other["individual_id"] = "duplicate"
                paths["mypokemon_path"].write_text(json.dumps([captured, other]))
                paths["mainpokemon_path"].write_text(json.dumps([other]))
                paths["team_path"].write_text(json.dumps([captured, other]))
                original_bytes = {key: path.read_bytes() for key, path in paths.items()}
                failed_index = 1 if "second" in scenario else 0
                fail_both = "both" in scenario
                failed_source = [captured, other][failed_index]
                runner = LegacyMigration(db, {})
                if scenario.endswith("insert"):
                    failed_id = (
                        runner.generated_id("collection", 1, other)
                        if failed_index
                        else "duplicate"
                    )
                    db.execute(f"""
                        CREATE TRIGGER fail_first BEFORE INSERT ON captured_pokemon
                        WHEN ({int(fail_both)} OR NEW.individual_id = '{failed_id}')
                             AND NEW.is_main = 0
                        BEGIN SELECT RAISE(ABORT, 'injected collection failure'); END
                    """)
                else:
                    key = runner.collection_row_key(failed_index, failed_source)
                    db.execute(f"""
                        CREATE TRIGGER fail_first BEFORE INSERT ON metadata
                        WHEN NEW.key = '{key}' OR
                             ({int(fail_both)} AND NEW.key LIKE 'migration_collection_row:%')
                        BEGIN SELECT RAISE(ABORT, 'injected checkpoint failure'); END
                    """)
                dialog.start_button.click()
                assert not dialog.migration_successful
                assert not db.is_migrated()
                assert db.get_pokemon_count() == (0 if fail_both else 1)
                if fail_both:
                    assert db.get_main_pokemon() is None
                    live = None
                elif failed_index:
                    assert db.get_all_pokemon() == [captured]
                    assert db.get_main_pokemon() is None
                    live = None
                else:
                    live = dict(db.get_main_pokemon(), level=20)
                    assert live["id"] == other["id"]
                    assert live["individual_id"] != "duplicate"
                db.execute("DROP TRIGGER fail_first")
                db._get_connection().commit()
                if live is not None:
                    assert db.save_main_pokemon(live)
                db.close()
                db = AnkimonDB(db_path=root / "ankimon.db")
                dialog.db = db
                dialog.start_button.click()
                assert dialog.migration_successful, dialog.log_area.toPlainText()
                assert db.is_migrated()
                assert db.get_pokemon_count() == 2
                assert db.get_pokemon("duplicate") == captured
                if live is None:
                    live = dict(
                        other, individual_id=db.get_main_pokemon()["individual_id"]
                    )
                    assert live["individual_id"] != "duplicate"
                assert db.get_main_pokemon() == live
                assert db.get_team() == [
                    {"individual_id": "duplicate"},
                    {"individual_id": live["individual_id"]},
                ]
                for key, path in paths.items():
                    assert not path.exists()
                    assert (root / "json" / path.name).read_bytes() == original_bytes[
                        key
                    ]
            elif scenario.startswith("pending-"):
                if "idless" in scenario:
                    captured.pop("individual_id")
                main = dict(captured)
                if "mixed" in scenario:
                    main.pop("individual_id")
                    paths["team_path"].write_text(json.dumps([captured]))
                paths["mypokemon_path"].write_text(json.dumps([captured]))
                paths["mainpokemon_path"].write_text(json.dumps([main]))
                original_bytes = {key: path.read_bytes() for key, path in paths.items()}
                with patch.object(db, "save_pokemon", return_value=False):
                    dialog.start_button.click()
                assert not dialog.migration_successful
                live = db.get_main_pokemon()
                assert live is not None
                if scenario.endswith("release"):
                    db.add_to_history(live)
                    assert db.delete_pokemon(live["individual_id"])
                elif scenario.endswith("progress"):
                    live = dict(live, level=20)
                    assert db.save_main_pokemon(live)
                db.close()
                db = AnkimonDB(db_path=root / "ankimon.db")
                dialog.db = db
                dialog.start_button.click()
                if scenario.endswith("release"):
                    assert not dialog.migration_successful
                    assert not db.is_migrated()
                    assert db.get_all_pokemon() == []
                    assert "explicit recovery" in dialog.log_area.toPlainText().lower()
                    assert {
                        key: path.read_bytes() for key, path in paths.items()
                    } == original_bytes
                    assert not (root / "json").exists()
                    assert dialog.start_button.isEnabled()
                    assert not dialog.continue_button.isVisible()
                else:
                    assert dialog.migration_successful, dialog.log_area.toPlainText()
                    assert db.get_all_pokemon() == [live]
                    assert db.is_migrated()
                    if "mixed" in scenario:
                        assert db.get_team() == [
                            {"individual_id": live["individual_id"]}
                        ]
                    assert (
                        root / "json/mypokemon.json"
                    ).read_bytes() == original_bytes["mypokemon_path"]
            else:
                paths["mainpokemon_path"].write_text("[]")
                progress = dialog._update_progress

                def lose_after_checkpoint(percent, message):
                    progress(percent, message)
                    if "Pokemon verified" in message:
                        db.delete_pokemon("other")

                with patch.object(
                    dialog, "_update_progress", side_effect=lose_after_checkpoint
                ):
                    dialog.start_button.click()
                assert not dialog.migration_successful
                db.close()
                db = AnkimonDB(db_path=root / "ankimon.db")
                dialog.db = db
                dialog.start_button.click()
                assert not dialog.migration_successful
                assert not db.is_migrated()
                assert db.get_pokemon("other") is None
                assert paths["mypokemon_path"].exists()
                assert "final verification" in dialog.log_area.toPlainText().lower()
            session.app.processEvents()
            assert dialog.grab().save(str(output / f"migration-{scenario}.png"))
            dialog.close()
            db.close()


if __name__ == "__main__":
    main()
