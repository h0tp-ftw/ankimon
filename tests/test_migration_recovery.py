"""Exercise the shipped migration with real SQLite and disposable legacy saves."""

import json
from unittest.mock import patch

import pytest
from PyQt6.QtWidgets import QApplication

from Ankimon.pyobj import database_manager
from Ankimon.pyobj.database_manager import AnkimonDB
from Ankimon.pyobj.migration_dialog import MigrationDialog


@pytest.fixture
def migration(tmp_path, qapp):
    box = [
        {
            "id": 25,
            "name": "Pikachu",
            "level": i + 1,
            "iv": {"hp": i % 32},
            "ev": {"hp": i},
            "stats": {"hp": 35, "atk": 55},
            "attacks": ["thunderbolt"],
            "ability": "Static",
        }
        for i in range(158)
    ]
    contents = {
        "mypokemon": box,
        "mainpokemon": [box[0]],
        "items": ["pass-orb", "old-gateau", "escape-rope", "pass-orb"],
        "badges": list(range(1, 15)),
        "team": [box[0], box[1]],
    }
    paths = {}
    for name, data in contents.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        paths[f"{name}_path"] = path
    db = AnkimonDB(db_path=tmp_path / "ankimon.db")
    dialog = MigrationDialog(db, **paths)
    yield db, dialog, paths, box
    dialog.deleteLater()
    db.close()


def run(dialog):
    with patch.object(QApplication, "processEvents"):
        dialog._run_migration()


def _write_item_catalogue(tmp_path):
    catalogue = tmp_path / "items.csv"
    catalogue.write_text(
        "id,identifier,category_id,cost,fling_power,fling_effect_id\n"
        "1,master-ball,34,0,,\n",
        encoding="utf-8",
    )
    return catalogue


def assert_preserved(db, box):
    rows = db.get_all_pokemon()
    assert len(rows) == 158
    by_level = {row["level"]: row for row in rows}
    for original in box:
        assert {
            k: v for k, v in by_level[original["level"]].items() if k != "individual_id"
        } == original
    assert db.get_main_pokemon()["individual_id"] == by_level[1]["individual_id"]


def test_dialog_preserves_158_pokemon_and_string_inventory(migration):
    db, dialog, paths, box = migration
    run(dialog)
    assert dialog.migration_successful, dialog.log_area.toPlainText()
    assert_preserved(db, box)
    assert db.get_item("pass-orb")["quantity"] == 2
    assert db.get_item("escape-rope")["quantity"] == 1
    assert len(db.get_team()) == 2
    assert "158 Pokemon" in dialog.log_area.toPlainText()
    assert not paths["mypokemon_path"].exists()
    assert (
        json.loads((paths["mypokemon_path"].parent / "json/mypokemon.json").read_text())
        == box
    )


def test_uncatalogued_item_does_not_collide_with_catalogue_id(migration, tmp_path):
    db, dialog, paths, _ = migration
    paths["mypokemon_path"].write_text("[]")
    paths["mainpokemon_path"].write_text("[]")
    paths["items_path"].write_text('["custom-item", "master-ball"]')
    paths["team_path"].write_text("[]")

    with patch.object(
        database_manager, "csv_file_items_cost", _write_item_catalogue(tmp_path)
    ):
        run(dialog)

    assert dialog.migration_successful, dialog.log_area.toPlainText()
    assert db.get_item("custom-item")["quantity"] == 1
    assert db.get_item("master-ball")["quantity"] == 1
    assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 2


def test_catalogued_item_relocates_existing_positive_id_occupant(migration, tmp_path):
    db, dialog, paths, _ = migration
    db.save_item(1, "custom-item", 3)
    paths["mypokemon_path"].write_text("[]")
    paths["mainpokemon_path"].write_text("[]")
    paths["items_path"].write_text('["master-ball"]')
    paths["team_path"].write_text("[]")

    with patch.object(
        database_manager, "csv_file_items_cost", _write_item_catalogue(tmp_path)
    ):
        run(dialog)

    assert dialog.migration_successful, dialog.log_area.toPlainText()
    assert db.get_item("custom-item")["quantity"] == 3
    assert db.get_item("custom-item")["id"] < 0
    assert db.get_item("master-ball")["id"] == 1


def test_inventory_final_verification_rolls_back_late_stack_loss(migration, tmp_path):
    db, dialog, paths, _ = migration
    paths["mypokemon_path"].write_text("[]")
    paths["mainpokemon_path"].write_text("[]")
    paths["items_path"].write_text('["custom-item", "master-ball"]')
    paths["team_path"].write_text("[]")
    original = db.add_item

    def lose_prior_stack(name, *args, **kwargs):
        saved = original(name, *args, **kwargs)
        if name == "master-ball":
            db.execute("DELETE FROM items WHERE item_name = ?", ("custom-item",))
        return saved

    with (
        patch.object(
            database_manager, "csv_file_items_cost", _write_item_catalogue(tmp_path)
        ),
        patch.object(db, "add_item", side_effect=lose_prior_stack),
    ):
        run(dialog)

    assert not dialog.migration_successful
    assert not db.is_migrated_phase1()
    assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0


def test_false_pokemon_save_never_completes_or_archives(migration):
    db, dialog, paths, _ = migration
    with patch.object(db, "save_pokemon", return_value=False):
        run(dialog)
    assert not dialog.migration_successful
    assert not db.is_migrated_phase1()
    assert not db.is_migrated()
    assert paths["mypokemon_path"].exists()
    assert "158" in dialog.log_area.toPlainText()
    assert "failed" in dialog.log_area.toPlainText().lower()


def test_claimed_success_without_persisted_rows_is_incomplete(migration):
    db, dialog, paths, _ = migration
    # Simulates a swallowed write/commit failure; a truthy return is insufficient.
    with patch.object(db, "save_pokemon", return_value=True):
        run(dialog)
    assert not dialog.migration_successful
    assert not db.is_migrated_phase1()
    assert paths["mypokemon_path"].exists()


def test_items_failure_then_retry_preserves_committed_collection(migration):
    db, dialog, paths, box = migration
    original = db.add_item

    def fail_second(name, *args, **kwargs):
        if name == "old-gateau":
            raise RuntimeError("injected items failure")
        return original(name, *args, **kwargs)

    with patch.object(db, "add_item", side_effect=fail_second):
        run(dialog)
    assert not dialog.migration_successful
    assert paths["mypokemon_path"].exists()
    assert not db.is_migrated_phase1()
    assert db.get_item("pass-orb") is None
    reopened = AnkimonDB(db_path=db.db_path)
    assert_preserved(reopened, box)
    reopened.close()
    ids = {p["individual_id"] for p in db.get_all_pokemon()}

    run(dialog)
    assert dialog.migration_successful, dialog.log_area.toPlainText()
    assert_preserved(db, box)
    assert {p["individual_id"] for p in db.get_all_pokemon()} == ids
    assert db.get_item("pass-orb")["quantity"] == 2


def test_stale_phase1_marker_does_not_hide_missing_collection(migration):
    db, dialog, _, box = migration
    survivor = dict(box[0], individual_id="survivor")
    db.save_main_pokemon(survivor)
    db.execute("INSERT OR REPLACE INTO metadata VALUES ('migrated', 'true')")
    db._get_connection().commit()
    run(dialog)
    assert dialog.migration_successful, dialog.log_area.toPlainText()
    assert_preserved(db, box)
    assert db.get_main_pokemon()["individual_id"] == "survivor"
    assert "158 Pokemon" in dialog.log_area.toPlainText()


@pytest.mark.parametrize("contents", [{"unexpected": "wrapper"}, "bad", ["bad"], None])
def test_unreadable_collection_is_never_archived(migration, contents):
    db, dialog, paths, _ = migration
    paths["mypokemon_path"].write_text(json.dumps(contents))
    run(dialog)
    assert not dialog.migration_successful
    assert not db.is_migrated()
    assert paths["mypokemon_path"].exists()
    assert "mypokemon.json" in dialog.log_area.toPlainText()


def test_cancel_then_retry_resets_cancellation(migration):
    db, dialog, paths, box = migration

    def cancel():
        dialog.cancelled = True

    with patch.object(QApplication, "processEvents", side_effect=cancel):
        dialog._run_migration()
    assert not dialog.migration_successful
    assert not db.is_migrated()
    assert paths["mypokemon_path"].exists()
    run(dialog)
    assert dialog.migration_successful, dialog.log_area.toPlainText()
    assert_preserved(db, box)


def test_corrupt_captured_row_does_not_break_team_matching(migration):
    db, dialog, _, _ = migration
    db.execute(
        "INSERT INTO captured_pokemon (individual_id, data) VALUES (?, ?)",
        ("corrupt", db._obfuscate("not a Pokemon")),
    )
    db._get_connection().commit()
    run(dialog)
    assert dialog.migration_successful, dialog.log_area.toPlainText()
    assert [db.get_pokemon(p["individual_id"])["level"] for p in db.get_team()] == [
        1,
        2,
    ]


def test_team_species_id_alias_matches_collection_id_with_ivs(migration):
    db, dialog, paths, box = migration
    captured = dict(box[0])
    team_member = dict(captured)
    team_member["species_id"] = team_member.pop("id")
    paths["mypokemon_path"].write_text(json.dumps([captured]))
    paths["mainpokemon_path"].write_text("[]")
    paths["team_path"].write_text(json.dumps([team_member]))

    run(dialog)

    assert dialog.migration_successful, dialog.log_area.toPlainText()
    assert db.get_team() == [
        {"individual_id": db.get_all_pokemon()[0]["individual_id"]}
    ]


def test_phase2_failure_keeps_sources_and_history_retry_is_idempotent(migration):
    db, dialog, paths, box = migration
    history = paths["mypokemon_path"].parent / "pokemon_history.json"
    history.write_text(json.dumps([box[2], box[2]]))
    dialog.history_path = history
    data = history.parent / "data.json"
    data.write_text('{"trainer_name": "Example"}')
    dialog.data_path = data
    original = db.set_user_data

    def fail_setting(key, *args):
        if key == "trainer_name":
            raise RuntimeError("injected settings failure")
        return original(key, *args)

    with patch.object(db, "set_user_data", side_effect=fail_setting):
        run(dialog)
    assert not dialog.migration_successful
    assert not db.is_migrated()
    assert history.exists()
    run(dialog)
    assert dialog.migration_successful, dialog.log_area.toPlainText()
    assert len(db.get_history()) == 2
    assert db.get_user_data("trainer_name") == "Example"


def test_explicit_main_and_team_ids_distinguish_identical_twins(migration):
    db, dialog, paths, box = migration
    twins = [dict(box[0], individual_id="first"), dict(box[0], individual_id="second")]
    paths["mypokemon_path"].write_text(json.dumps(twins))
    paths["mainpokemon_path"].write_text(json.dumps([twins[1]]))
    paths["team_path"].write_text(json.dumps([twins[1]]))
    run(dialog)
    assert dialog.migration_successful, dialog.log_area.toPlainText()
    assert db.get_main_pokemon()["individual_id"] == "second"
    assert db.get_team() == [{"individual_id": "second"}]


def test_team_can_reference_main_outside_collection(migration):
    db, dialog, paths, box = migration
    main = dict(box[0], id=6, name="Charizard", individual_id="separate-main")
    paths["mainpokemon_path"].write_text(json.dumps([main]))
    paths["team_path"].write_text(json.dumps([{"individual_id": "separate-main"}]))
    run(dialog)
    assert dialog.migration_successful, dialog.log_area.toPlainText()
    assert db.get_pokemon_count() == 159
    assert db.get_team() == [{"individual_id": "separate-main"}]


def test_distinct_main_with_same_species_and_level_preserves_both(migration):
    db, dialog, paths, box = migration
    captured = dict(box[0], individual_id="captured", iv={"hp": 1}, nickname="Keep me")
    main = dict(box[0], individual_id="main", iv={"hp": 31}, nickname="Main")
    paths["mypokemon_path"].write_text(json.dumps([captured]))
    paths["mainpokemon_path"].write_text(json.dumps([main]))
    paths["team_path"].write_text(json.dumps([main, captured]))
    run(dialog)
    assert dialog.migration_successful, dialog.log_area.toPlainText()
    assert db.get_pokemon_count() == 2
    assert db.get_pokemon("captured") == captured
    assert db.get_main_pokemon() == main
    assert db.get_team() == [{"individual_id": "main"}, {"individual_id": "captured"}]


def test_duplicate_ids_reuse_the_same_repaired_rows_on_retry(migration):
    db, dialog, paths, box = migration
    twins = [
        dict(box[0], individual_id="duplicate"),
        dict(box[0], individual_id="duplicate"),
    ]
    paths["mypokemon_path"].write_text(json.dumps(twins))
    paths["team_path"].write_text(json.dumps(twins))
    with patch.object(db, "save_badge", side_effect=RuntimeError("disk full")):
        run(dialog)
    assert not dialog.migration_successful
    before = {p["individual_id"] for p in db.get_all_pokemon()}
    run(dialog)
    assert dialog.migration_successful, dialog.log_area.toPlainText()
    assert {p["individual_id"] for p in db.get_all_pokemon()} == before
    assert len(before) == 2
    assert len({p["individual_id"] for p in db.get_team()}) == 2


def test_retry_preserves_identical_twins_when_first_save_failed(migration):
    db, dialog, paths, box = migration
    twins = [box[0], box[0]]
    paths["mypokemon_path"].write_text(json.dumps(twins))
    paths["mainpokemon_path"].write_text("[]")
    paths["team_path"].write_text(json.dumps(twins))
    save = db.save_pokemon
    attempts = 0

    def fail_first(pokemon):
        nonlocal attempts
        attempts += 1
        return False if attempts == 1 else save(pokemon)

    with patch.object(db, "save_pokemon", side_effect=fail_first):
        run(dialog)
    assert not dialog.migration_successful
    assert db.get_pokemon_count() == 1
    run(dialog)
    assert dialog.migration_successful, dialog.log_area.toPlainText()
    assert db.get_pokemon_count() == 2
    assert len({p["individual_id"] for p in db.get_team()}) == 2


def test_distinct_explicit_main_id_is_preserved_for_identical_pokemon(migration):
    db, dialog, paths, box = migration
    captured = dict(box[0], individual_id="captured")
    main = dict(box[0], individual_id="main")
    paths["mypokemon_path"].write_text(json.dumps([captured]))
    paths["mainpokemon_path"].write_text(json.dumps([main]))
    paths["team_path"].write_text("[]")
    run(dialog)
    assert dialog.migration_successful, dialog.log_area.toPlainText()
    assert db.get_pokemon_count() == 2
    assert db.get_main_pokemon() == main
    assert db.get_pokemon("captured") == captured


def test_retry_reuses_idless_history_written_by_old_migration(migration):
    db, dialog, paths, box = migration
    # Older add_to_history created a SQL ID without putting it in the JSON blob.
    assert db.add_to_history(box[0])
    history = paths["mypokemon_path"].parent / "pokemon_history.json"
    history.write_text(json.dumps([box[0], box[0]]))
    dialog.history_path = history
    run(dialog)
    assert dialog.migration_successful, dialog.log_area.toPlainText()
    assert len(db.get_history()) == 2


def test_history_processes_queued_cancel_before_import_finishes(migration):
    from PyQt6.QtCore import QTimer

    db, dialog, paths, box = migration
    history = paths["mypokemon_path"].parent / "pokemon_history.json"
    history.write_text(json.dumps([box[0]] * 100))
    dialog.history_path = history
    original = db.add_to_history
    writes = 0

    def queue_cancel(pokemon):
        nonlocal writes
        writes += 1
        if writes == 1:
            QTimer.singleShot(0, lambda: setattr(dialog, "cancelled", True))
        return original(pokemon)

    with patch.object(db, "add_to_history", side_effect=queue_cancel):
        dialog._run_migration()
    assert not dialog.migration_successful
    assert not db.is_migrated()
    assert writes < 100
    assert history.exists()


def test_unique_main_id_survives_a_level_change(migration):
    db, dialog, paths, box = migration
    first = dict(box[0], individual_id="first", level=20, nickname="First")
    second = dict(first, individual_id="second", level=21, nickname="Second")
    main = dict(first, level=21)
    paths["mypokemon_path"].write_text(json.dumps([first, second]))
    paths["mainpokemon_path"].write_text(json.dumps([main]))
    paths["team_path"].write_text(json.dumps([main]))
    run(dialog)
    assert dialog.migration_successful, dialog.log_area.toPlainText()
    assert db.get_main_pokemon() == main
    assert db.get_pokemon("second") == second
    assert db.get_team() == [{"individual_id": "first"}]


def test_archiving_preserves_an_existing_legacy_backup(migration):
    _, dialog, paths, box = migration
    archive = paths["mypokemon_path"].parent / "json"
    archive.mkdir()
    original_backup = archive / "mypokemon.json"
    original_backup.write_text("untouched earlier backup")
    run(dialog)
    assert dialog.migration_successful, dialog.log_area.toPlainText()
    assert original_backup.read_text() == "untouched earlier backup"
    additional = list(archive.glob("mypokemon-*.json"))
    assert len(additional) == 1
    assert json.loads(additional[0].read_text()) == box


def test_verified_phase1_retry_does_not_restore_a_released_pokemon(migration):
    db, dialog, paths, box = migration
    pokemon = dict(box[0], individual_id="released")
    paths["mypokemon_path"].write_text(json.dumps([pokemon]))
    paths["mainpokemon_path"].write_text("[]")
    paths["team_path"].write_text("[]")
    data = paths["mypokemon_path"].parent / "data.json"
    data.write_text("invalid JSON")
    dialog.data_path = data
    run(dialog)
    assert not dialog.migration_successful
    assert db.is_migrated_phase1()
    assert db.add_to_history(pokemon)
    db.delete_pokemon("released")
    data.write_text("{}")
    run(dialog)
    assert dialog.migration_successful, dialog.log_area.toPlainText()
    assert db.get_pokemon_count() == 0


def test_pending_team_retry_uses_checkpointed_identity_after_level_up(migration):
    db, dialog, paths, box = migration
    legacy = dict(box[9])
    paths["mypokemon_path"].write_text(json.dumps([legacy]))
    paths["mainpokemon_path"].write_text("[]")
    paths["team_path"].write_text(json.dumps([legacy]))

    def cancel_at_team():
        if dialog.status_label.text() == "Migrating team...":
            dialog.cancelled = True

    with patch.object(QApplication, "processEvents", side_effect=cancel_at_team):
        dialog._run_migration()

    assert not dialog.migration_successful
    assert db.is_migrated_phase1()
    captured = db.get_all_pokemon()[0]
    captured["level"] = 11
    assert db.save_pokemon(captured)

    run(dialog)

    assert dialog.migration_successful, dialog.log_area.toPlainText()
    assert db.get_team() == [{"individual_id": captured["individual_id"]}]
    assert db.get_pokemon(captured["individual_id"])["level"] == 11


def test_item_failure_retry_preserves_main_level_and_release(migration):
    db, dialog, paths, box = migration
    paths["mypokemon_path"].write_text(json.dumps([box[9], box[19]]))
    paths["mainpokemon_path"].write_text(json.dumps([box[9]]))
    paths["team_path"].write_text("[]")
    original = db.add_item

    def fail_second(name, *args, **kwargs):
        if name == "old-gateau":
            raise RuntimeError("injected items failure")
        return original(name, *args, **kwargs)

    with patch.object(db, "add_item", side_effect=fail_second):
        run(dialog)

    assert not dialog.migration_successful
    assert not db.is_migrated_phase1()
    survivor, released = sorted(db.get_all_pokemon(), key=lambda row: row["level"])
    survivor["level"] = 11
    assert db.save_main_pokemon(survivor)
    assert db.delete_pokemon(released["individual_id"])

    run(dialog)

    assert dialog.migration_successful, dialog.log_area.toPlainText()
    assert db.get_pokemon_count() == 1
    assert db.get_pokemon(survivor["individual_id"])["level"] == 11
    assert db.get_main_pokemon()["level"] == 11
    assert db.get_pokemon(released["individual_id"]) is None


def test_collection_summary_cancel_retry_preserves_progress_and_release(migration):
    db, dialog, paths, box = migration
    paths["mypokemon_path"].write_text(json.dumps([box[9], box[19]]))
    paths["mainpokemon_path"].write_text("[]")
    paths["team_path"].write_text("[]")

    def cancel_at_collection_summary():
        if "Pokemon verified" in dialog.status_label.text():
            dialog.cancelled = True

    with patch.object(
        QApplication, "processEvents", side_effect=cancel_at_collection_summary
    ):
        dialog._run_migration()

    assert not dialog.migration_successful
    assert not db.is_migrated_phase1()
    survivor, released = sorted(db.get_all_pokemon(), key=lambda row: row["level"])
    survivor["level"] = 11
    assert db.save_pokemon(survivor)
    assert db.delete_pokemon(released["individual_id"])

    run(dialog)

    assert dialog.migration_successful, dialog.log_area.toPlainText()
    assert db.get_pokemon_count() == 1
    assert db.get_pokemon(survivor["individual_id"])["level"] == 11
    assert db.get_pokemon(released["individual_id"]) is None


def test_verified_team_retry_preserves_idless_member_after_level_up(migration):
    db, dialog, paths, box = migration
    paths["mypokemon_path"].write_text(json.dumps([box[0]]))
    paths["team_path"].write_text(json.dumps([box[0]]))
    data = paths["mypokemon_path"].parent / "data.json"
    data.write_text("invalid JSON")
    dialog.data_path = data
    run(dialog)
    assert not dialog.migration_successful
    main = db.get_main_pokemon()
    main["level"] += 1
    db.save_main_pokemon(main)
    team = db.get_team()
    data.write_text("{}")
    run(dialog)
    assert dialog.migration_successful, dialog.log_area.toPlainText()
    assert db.get_main_pokemon() == main
    assert db.get_team() == team


def test_team_checkpoint_failure_rolls_back_team_replacement(migration):
    db, dialog, _, box = migration
    old = dict(box[0], name="Charizard", id=6, individual_id="old-team")
    db.save_pokemon(old)
    old_team = [{"individual_id": "old-team"}]
    db.save_team(old_team)
    conn = db._get_connection()
    execute = conn.execute

    def fail_checkpoint(sql, *args):
        if "INSERT" in sql and "migration_verified_team" in sql:
            raise RuntimeError("injected checkpoint failure")
        return execute(sql, *args)

    with patch.object(conn, "execute", side_effect=fail_checkpoint):
        run(dialog)
    assert not dialog.migration_successful
    assert db.get_team() == old_team
    assert not conn.execute(
        "SELECT 1 FROM metadata WHERE key = 'migration_verified_team'"
    ).fetchone()
    assert not conn.in_transaction
