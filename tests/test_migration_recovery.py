"""Exercise the shipped migration with real SQLite and disposable legacy saves."""

import json
from contextlib import nullcontext
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


def test_stale_phase1_marker_requires_explicit_recovery_for_missing_collection(
    migration,
):
    db, dialog, _, box = migration
    survivor = dict(box[0], individual_id="survivor")
    db.save_main_pokemon(survivor)
    db.execute("INSERT OR REPLACE INTO metadata VALUES ('migrated', 'true')")
    db._get_connection().commit()
    run(dialog)
    assert not dialog.migration_successful
    assert db.get_all_pokemon() == [survivor]
    assert db.is_migrated_phase1()
    assert "explicit recovery" in dialog.log_area.toPlainText().lower()
    run(dialog)
    assert not dialog.migration_successful
    assert db.get_all_pokemon() == [survivor]


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


def overlap_sources(migration):
    db, dialog, paths, box = migration
    captured = dict(box[0], individual_id="main", level=10)
    other = dict(box[1], individual_id="other", level=5)
    main = dict(captured, level=11)
    for key, data in {
        "mypokemon": [captured, other],
        "mainpokemon": [main],
        "team": [],
    }.items():
        paths[f"{key}_path"].write_text(json.dumps(data))
    return db, dialog, paths, captured, other, main


def test_overlap_collection_failure_retry_preserves_newer_main(migration):
    db, dialog, _, _, _, main = overlap_sources(migration)
    save = db.save_pokemon
    with patch.object(
        db,
        "save_pokemon",
        side_effect=lambda p: False if p["individual_id"] == "other" else save(p),
    ):
        run(dialog)
    assert not dialog.migration_successful
    run(dialog)
    assert dialog.migration_successful, dialog.log_area.toPlainText()
    assert db.get_main_pokemon() == main


def pending_collection_with_verified_main(
    migration,
    *,
    collection_explicit=False,
    main_explicit=False,
    twins=False,
    team=False,
    failure="save",
):
    from Ankimon.pyobj.legacy_migration import LegacyMigration

    db, _, paths, box = migration
    pokemon = dict(box[0], level=10)
    main = dict(pokemon)
    if collection_explicit:
        pokemon["individual_id"] = "main"
    if main_explicit:
        main["individual_id"] = "main"
    paths["mypokemon_path"].write_text(json.dumps([pokemon] * (2 if twins else 1)))
    paths["mainpokemon_path"].write_text(json.dumps([main]))
    team_member = (
        {"individual_id": pokemon["individual_id"]} if team == "id-only" else pokemon
    )
    paths["team_path"].write_text(json.dumps([team_member] if team else []))
    sources = {key.removesuffix("_path"): path for key, path in paths.items()}
    original_bytes = {key: path.read_bytes() for key, path in sources.items()}
    if failure == "insert":
        db.execute("""
            CREATE TRIGGER fail_collection BEFORE INSERT ON captured_pokemon
            WHEN NEW.is_main = 0
            BEGIN SELECT RAISE(ABORT, 'injected collection insert failure'); END
        """)
    elif failure == "checkpoint":
        db.execute("""
            CREATE TRIGGER fail_collection BEFORE INSERT ON metadata
            WHEN NEW.key LIKE 'migration_collection_row:%'
            BEGIN SELECT RAISE(ABORT, 'injected collection checkpoint failure'); END
        """)
    try:
        with (
            patch.object(db, "save_pokemon", return_value=False)
            if failure == "save"
            else nullcontext()
        ):
            stats = LegacyMigration(db, sources).run()
    finally:
        if failure != "save":
            db.execute("DROP TRIGGER fail_collection")
            db._get_connection().commit()
    assert stats.get("errors")
    assert stats["main"] == 1
    assert not db.is_migrated_phase1()
    assert not db.execute(
        "SELECT 1 FROM metadata WHERE key LIKE 'migration_collection_row:%'"
    ).fetchone()
    assert db.execute(
        "SELECT 1 FROM metadata WHERE key = 'migration_verified_main'"
    ).fetchone()
    assert db.get_all_pokemon() == [db.get_main_pokemon()]
    return sources, original_bytes, db.get_main_pokemon()


@pytest.mark.parametrize("collection_explicit", [False, True])
@pytest.mark.parametrize("main_explicit", [False, True])
@pytest.mark.parametrize("team", [False, True])
@pytest.mark.parametrize("failure", ["save", "insert", "checkpoint"])
@pytest.mark.parametrize("state", ["unchanged", "progressed", "released"])
def test_pending_collection_retry_uses_verified_main_identity(
    migration, collection_explicit, main_explicit, team, failure, state
):
    from Ankimon.pyobj.legacy_migration import LegacyMigration

    db, _, _, _ = migration
    sources, original_bytes, main = pending_collection_with_verified_main(
        migration,
        collection_explicit=collection_explicit,
        main_explicit=main_explicit,
        team=team,
        failure=failure,
    )
    if state == "progressed":
        main = dict(main, level=20, nickname="Progress")
        assert db.save_main_pokemon(main)
    elif state == "released":
        db.add_to_history(main)
        assert db.delete_pokemon(main["individual_id"])
    db.close()
    reopened = AnkimonDB(db_path=db.db_path)
    try:
        for _ in range(2):
            stats = LegacyMigration(reopened, sources).run()
            rows = reopened.get_all_pokemon()
            # An explicit main absent from the collection is a separate Pokemon.
            distinct_main = main_explicit and not collection_explicit
            if distinct_main:
                collection = [p for p in rows if p["individual_id"] != "main"]
                assert len(collection) == 1
                assert collection[0]["level"] == 10
                assert reopened.get_pokemon("main") == (
                    None if state == "released" else main
                )
                assert not stats.get("errors"), stats
                assert reopened.is_migrated()
                if team:
                    assert reopened.get_team() == [
                        {"individual_id": collection[0]["individual_id"]}
                    ]
            elif state == "released":
                assert rows == []
                assert stats.get("errors"), stats
                assert stats["pokemon"] == 0
                assert not reopened.is_migrated()
                assert not reopened.execute(
                    "SELECT 1 FROM metadata WHERE key LIKE 'migration_collection_row:%'"
                ).fetchone()
            else:
                assert rows == [main]
                assert not stats.get("errors"), stats
                assert reopened.is_migrated()
                if team:
                    assert reopened.get_team() == [
                        {"individual_id": main["individual_id"]}
                    ]
            assert {
                key: path.read_bytes() for key, path in sources.items()
            } == original_bytes
    finally:
        reopened.close()


def test_mixed_collection_alias_survives_reopen_for_pending_id_only_team(migration):
    from Ankimon.pyobj.legacy_migration import LegacyMigration

    db, _, _, _ = migration
    sources, original_bytes, main = pending_collection_with_verified_main(
        migration, collection_explicit=True, team="id-only", failure="checkpoint"
    )
    assert main["individual_id"] != "main"
    main = dict(main, level=20, nickname="Progress")
    assert db.save_main_pokemon(main)
    # First retry checkpoints the collection alias, but team saving still fails.
    with patch.object(db, "save_team", return_value=False):
        stats = LegacyMigration(db, sources).run()
    assert stats.get("errors")
    assert db.get_all_pokemon() == [main]
    assert not db.is_migrated()
    db.close()
    reopened = AnkimonDB(db_path=db.db_path)
    try:
        stats = LegacyMigration(reopened, sources).run()
        assert not stats.get("errors"), stats
        assert reopened.is_migrated()
        assert reopened.get_all_pokemon() == [main]
        assert reopened.get_team() == [{"individual_id": main["individual_id"]}]
        assert {key: path.read_bytes() for key, path in sources.items()} == original_bytes
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "collection_explicit,main_explicit", [(False, False), (True, False), (True, True)]
)
def test_pending_collection_missing_main_dialog_preserves_sources(
    migration, collection_explicit, main_explicit
):
    db, dialog, _, _ = migration
    sources, original_bytes, main = pending_collection_with_verified_main(
        migration, collection_explicit=collection_explicit, main_explicit=main_explicit
    )
    db.add_to_history(main)
    assert db.delete_pokemon(main["individual_id"])
    for _ in range(2):
        run(dialog)
        assert not dialog.migration_successful
        assert "explicit recovery" in dialog.log_area.toPlainText().lower()
        assert not db.is_migrated()
        assert db.get_all_pokemon() == []
        assert {
            key: path.read_bytes() for key, path in sources.items()
        } == original_bytes
        assert not (sources["mypokemon"].parent / "json").exists()


@pytest.mark.parametrize("progressed", [False, True])
@pytest.mark.parametrize("collection_explicit", [False, True])
def test_pending_identical_twins_cannot_guess_verified_main_owner(
    migration, progressed, collection_explicit
):
    db, dialog, _, _ = migration
    sources, original_bytes, main = pending_collection_with_verified_main(
        migration, twins=True, collection_explicit=collection_explicit
    )
    if progressed:
        main = dict(main, level=20)
        assert db.save_main_pokemon(main)
    for _ in range(2):
        run(dialog)
        assert not dialog.migration_successful
        assert "explicit recovery" in dialog.log_area.toPlainText().lower()
        assert db.get_all_pokemon() == [main]
        assert not db.is_migrated()
        assert {
            key: path.read_bytes() for key, path in sources.items()
        } == original_bytes


def test_pending_idless_collection_does_not_claim_distinct_explicit_main(migration):
    db, dialog, paths, box = migration
    main = dict(box[0], individual_id="main")
    paths["mypokemon_path"].write_text(json.dumps([box[0]]))
    paths["mainpokemon_path"].write_text(json.dumps([main]))
    paths["team_path"].write_text("[]")
    with patch.object(db, "save_pokemon", return_value=False):
        run(dialog)
    assert not dialog.migration_successful
    assert db.get_all_pokemon() == [main]
    run(dialog)
    assert dialog.migration_successful, dialog.log_area.toPlainText()
    assert db.get_pokemon_count() == 2
    assert db.get_main_pokemon() == main


@pytest.mark.parametrize("include_main", [False, True])
@pytest.mark.parametrize("collection_explicit", [False, True])
@pytest.mark.parametrize(
    "difference",
    [{"id": 1, "name": "Bulbasaur"}, {"level": 5}, {"iv": {"hp": 7}}],
    ids=["species", "level", "ivs"],
)
def test_pending_collection_matches_main_fields_not_missing_ids(
    migration, include_main, collection_explicit, difference
):
    db, dialog, paths, box = migration
    main = dict(box[0], level=10)
    other = dict(main, **difference)
    if collection_explicit:
        other["individual_id"] = "other"
    entries = [other, main] if include_main else [other]
    paths["mypokemon_path"].write_text(json.dumps(entries))
    paths["mainpokemon_path"].write_text(json.dumps([main]))
    paths["team_path"].write_text(json.dumps(entries))
    with patch.object(db, "save_pokemon", return_value=False):
        run(dialog)
    assert not dialog.migration_successful
    live = dict(db.get_main_pokemon(), level=20)
    assert db.save_main_pokemon(live)
    run(dialog)
    assert dialog.migration_successful, dialog.log_area.toPlainText()
    assert db.get_pokemon_count() == 2
    assert db.get_main_pokemon() == live
    saved_other = next(
        p
        for p in db.get_all_pokemon()
        if p["individual_id"] != live["individual_id"]
    )
    assert {k: v for k, v in saved_other.items() if k != "individual_id"} == {
        k: v for k, v in other.items() if k != "individual_id"
    }
    expected_team = [{"individual_id": saved_other["individual_id"]}]
    if include_main:
        expected_team.append({"individual_id": live["individual_id"]})
    assert db.get_team() == expected_team


@pytest.mark.parametrize("committed", [False, True])
def test_pending_twin_respects_collection_claim_on_main(migration, committed):
    db, dialog, paths, box = migration
    # The later entry either explicitly reserves the main ID, or already owns
    # it through a committed ID-less row checkpoint.
    main = box[0] if committed else dict(box[0], individual_id="main")
    paths["mypokemon_path"].write_text(json.dumps([box[0], main]))
    paths["mainpokemon_path"].write_text(json.dumps([main]))
    paths["team_path"].write_text("[]")
    save = db.save_pokemon
    attempts = 0

    def fail_pending(pokemon):
        nonlocal attempts
        attempts += 1
        return save(pokemon) if committed and attempts == 2 else False

    with patch.object(db, "save_pokemon", side_effect=fail_pending):
        run(dialog)
    assert not dialog.migration_successful
    live = dict(db.get_main_pokemon(), level=20)
    assert db.save_main_pokemon(live)
    run(dialog)
    assert dialog.migration_successful, dialog.log_area.toPlainText()
    assert db.get_pokemon_count() == 2
    assert db.get_main_pokemon() == live
    assert sorted(p["level"] for p in db.get_all_pokemon()) == [1, 20]


def test_null_collection_snapshot_is_not_verification_evidence(migration):
    db, dialog, _, _, _, _ = overlap_sources(migration)
    with patch.object(db, "save_badge", return_value=False):
        run(dialog)
    row = db.execute(
        "SELECT key, value FROM metadata WHERE key LIKE 'migration_collection_row:%'"
    ).fetchone()
    payload = json.loads(row["value"])
    payload["snapshot"] = None
    db.execute(
        "UPDATE metadata SET value = ? WHERE key = ?",
        (json.dumps(payload), row["key"]),
    )
    db._get_connection().commit()
    run(dialog)
    assert not dialog.migration_successful
    assert not db.is_migrated()


def test_pending_main_after_collection_cancel_preserves_progress(migration):
    db, dialog, _, _, _, _ = overlap_sources(migration)

    def cancel():
        if "Pokemon verified" in dialog.status_label.text():
            dialog.cancelled = True

    with patch.object(QApplication, "processEvents", side_effect=cancel):
        dialog._run_migration()
    assert not dialog.migration_successful
    live = dict(db.get_pokemon("main"), level=20, nickname="Progress")
    db.save_pokemon(live)
    run(dialog)
    assert dialog.migration_successful, dialog.log_area.toPlainText()
    assert db.get_main_pokemon() == live


@pytest.mark.parametrize("released", [False, True])
def test_old_phase1_marker_never_reimports_ambiguous_rows(migration, released):
    db, dialog, paths, captured, other, _ = overlap_sources(migration)
    if released:
        db.save_pokemon(other)
        db.add_to_history(captured)
    else:
        captured.pop("individual_id")
        paths["mypokemon_path"].write_text(json.dumps([captured]))
        db.save_pokemon(dict(captured, individual_id="old-random-id", level=20))
    paths["mainpokemon_path"].write_text("[]")
    db.execute("INSERT INTO metadata VALUES ('migrated', 'true')")
    db._get_connection().commit()
    before = db.get_all_pokemon()
    for _ in range(2):
        run(dialog)
        assert not dialog.migration_successful
        assert db.get_all_pokemon() == before
        assert paths["mypokemon_path"].exists()
        assert "explicit recovery" in dialog.log_area.toPlainText().lower()


@pytest.mark.parametrize("retry", [False, True])
def test_idless_team_matches_newer_main_alias(migration, retry):
    db, dialog, paths, _, _, main = overlap_sources(migration)
    team = dict(main)
    team.pop("individual_id")
    paths["team_path"].write_text(json.dumps([team]))
    if retry:
        with patch.object(db, "save_badge", return_value=False):
            run(dialog)
        assert not dialog.migration_successful
    run(dialog)
    assert dialog.migration_successful, dialog.log_area.toPlainText()
    assert db.get_team() == [{"individual_id": "main"}]


def test_two_aliases_cannot_fill_two_team_slots(migration):
    db, dialog, paths, captured, _, main = overlap_sources(migration)
    captured.pop("individual_id")
    main.pop("individual_id")
    paths["team_path"].write_text(json.dumps([captured, main]))
    run(dialog)
    assert not dialog.migration_successful
    assert db.get_team() == []
    assert paths["team_path"].exists()


@pytest.mark.parametrize("source", ["mainpokemon", "items", "badges"])
def test_missing_collection_succeeds_first_attempt(migration, source):
    db, dialog, paths, _ = migration
    for key, path in paths.items():
        if key != f"{source}_path":
            path.unlink()
    run(dialog)
    assert dialog.migration_successful, dialog.log_area.toPlainText()
    assert db.is_migrated()


def test_none_species_alias_falls_back_to_id(migration):
    db, dialog, paths, box = migration
    assert database_manager.legacy_species_id({"id": 25, "species_id": None}) == "25"
    paths["mypokemon_path"].write_text(json.dumps([box[0]]))
    paths["mainpokemon_path"].write_text("[]")
    paths["team_path"].write_text(json.dumps([dict(box[0], species_id=None)]))
    run(dialog)
    assert dialog.migration_successful, dialog.log_area.toPlainText()
    assert len(db.get_team()) == 1


def test_cancel_during_generic_error_reporting_returns_cancelled(migration):
    from Ankimon.pyobj.legacy_migration import LegacyMigration

    db, _, _, _ = migration
    cancelled = False

    def progress(_, message):
        nonlocal cancelled
        if message.startswith("Migration incomplete:"):
            cancelled = True

    runner = LegacyMigration(db, {}, progress, lambda: cancelled)
    with patch.object(
        runner, "save_collection_checkpoint", side_effect=RuntimeError("disk full")
    ):
        stats = runner.run()
    assert stats["cancelled"]
    assert not db.is_migrated()


def test_inventory_checkpoint_preserves_consumption_after_badge_failure(migration):
    db, dialog, paths, _ = migration
    paths["items_path"].write_text(json.dumps([{"item": "potion", "quantity": 5}]))
    with patch.object(db, "save_badge", return_value=False):
        run(dialog)
    assert not dialog.migration_successful
    db.add_item("potion", 3)
    run(dialog)
    assert dialog.migration_successful, dialog.log_area.toPlainText()
    assert db.get_item("potion")["quantity"] == 3


@pytest.mark.parametrize(
    "checkpoint",
    [
        "migration_collection_row:",
        "migration_verified_main",
        "migration_verified_items",
    ],
)
def test_checkpoint_failure_rolls_back_owned_writes(migration, checkpoint):
    db, dialog, paths, captured, _, _ = overlap_sources(migration)
    paths["mypokemon_path"].write_text(json.dumps([captured]))
    if checkpoint == "migration_collection_row:":
        paths["mainpokemon_path"].write_text("[]")
    conn = db._get_connection()
    execute = conn.execute

    def fail_checkpoint(sql, parameters=()):
        if "INSERT" in sql and parameters and str(parameters[0]).startswith(checkpoint):
            raise RuntimeError("injected checkpoint failure")
        return execute(sql, parameters)

    with patch.object(conn, "execute", side_effect=fail_checkpoint):
        run(dialog)
    assert not dialog.migration_successful
    assert not conn.in_transaction
    assert not conn._disable_commit
    if checkpoint == "migration_collection_row:":
        assert db.get_pokemon("main") is None
    elif checkpoint == "migration_verified_main":
        assert db.get_pokemon("main") == captured
        assert db.get_main_pokemon() is None
    else:
        assert db.get_item("pass-orb") is None
    assert not conn.execute(
        "SELECT 1 FROM metadata WHERE key LIKE ?", (checkpoint + "%",)
    ).fetchone()
    run(dialog)
    assert dialog.migration_successful, dialog.log_area.toPlainText()


@pytest.mark.parametrize("checkpoint", ["collection", "main"])
def test_older_source_checkpoint_requires_reconciliation_without_overwrite(
    migration, checkpoint
):
    from Ankimon.pyobj.legacy_migration import LegacyMigration

    db, dialog, _, captured, _, main = overlap_sources(migration)
    runner = LegacyMigration(db, {})
    if checkpoint == "collection":
        runner.collection = [runner.mapping_candidate(captured, "main")]
        runner.save_collection_checkpoint()
        db.save_pokemon(dict(main, level=20))
    else:
        runner.main_candidate = runner.mapping_candidate(main, "main")
        runner.save_main_checkpoint()
        db.save_main_pokemon(main)
    before = db.get_pokemon("main")
    run(dialog)
    assert not dialog.migration_successful
    assert "reconciliation" in dialog.log_area.toPlainText().lower()
    assert db.get_pokemon("main") == before


def test_pending_main_never_resurrects_released_collection_row(migration):
    db, dialog, paths, captured, _, _ = overlap_sources(migration)

    def cancel():
        if "Pokemon verified" in dialog.status_label.text():
            dialog.cancelled = True

    with patch.object(QApplication, "processEvents", side_effect=cancel):
        dialog._run_migration()
    db.add_to_history(captured)
    db.delete_pokemon("main")
    run(dialog)
    assert not dialog.migration_successful
    assert db.get_pokemon("main") is None
    assert paths["mainpokemon_path"].exists()


def test_partial_collection_retry_preserves_row_progress_and_release(migration):
    db, dialog, paths, captured, other, _ = overlap_sources(migration)
    third = dict(other, individual_id="third")
    paths["mypokemon_path"].write_text(json.dumps([captured, other, third]))
    paths["mainpokemon_path"].write_text("[]")
    save = db.save_pokemon
    with patch.object(
        db,
        "save_pokemon",
        side_effect=lambda p: False if p["individual_id"] == "third" else save(p),
    ):
        run(dialog)
    live = dict(db.get_pokemon("main"), level=20)
    db.save_pokemon(live)
    db.delete_pokemon("other")
    run(dialog)
    assert dialog.migration_successful, dialog.log_area.toPlainText()
    assert db.get_pokemon("main") == live
    assert db.get_pokemon("other") is None
    assert db.get_pokemon("third") == third


def test_retry_reserves_random_ids_owned_by_later_collection_entries(migration):
    db, dialog, paths, box = migration
    source = box[0]
    first = dict(source, individual_id="old-a")
    second = dict(source, individual_id="old-b")
    db.save_pokemon(first)
    db.save_pokemon(second)
    paths["mypokemon_path"].write_text(json.dumps([source, source]))
    paths["mainpokemon_path"].write_text("[]")
    paths["team_path"].write_text("[]")
    save = db.save_pokemon
    with patch.object(
        db,
        "save_pokemon",
        side_effect=lambda p: False if p["individual_id"] == "old-a" else save(p),
    ):
        run(dialog)
    assert not dialog.migration_successful
    db.delete_pokemon("old-a")
    progressed = dict(second, nickname="Progress")
    db.save_pokemon(progressed)
    run(dialog)
    assert dialog.migration_successful, dialog.log_area.toPlainText()
    assert db.get_pokemon("old-b") == progressed
    assert db.get_pokemon_count() == 2


def test_final_readback_failure_retains_old_marker_recovery_mode(migration):
    db, dialog, paths, captured, _, _ = overlap_sources(migration)
    db.save_pokemon(captured)
    paths["mainpokemon_path"].write_text("[]")
    db.execute("INSERT INTO metadata VALUES ('migrated', 'true')")
    db._get_connection().commit()
    progress = dialog._update_progress

    def change_live_row(percent, message):
        progress(percent, message)
        if "Pokemon verified" in message:
            db.save_pokemon(dict(captured, level=20))

    with patch.object(dialog, "_update_progress", side_effect=change_live_row):
        run(dialog)
    assert not dialog.migration_successful
    assert db.is_migrated_phase1()
    run(dialog)
    assert not dialog.migration_successful
    assert db.get_pokemon_count() == 1
    assert db.get_pokemon("main")["level"] == 20


@pytest.mark.parametrize("variant", ["idless", "progressed", "released"])
def test_repaired_partial_source_requires_reconciliation(migration, variant):
    db, dialog, paths, box = migration
    first, second = dict(box[0]), dict(box[1])
    if variant != "idless":
        second["individual_id"] = "second"
    paths["mypokemon_path"].write_text(json.dumps([first, "bad", second]))
    paths["mainpokemon_path"].write_text("[]")
    paths["team_path"].write_text("[]")
    run(dialog)
    assert not dialog.migration_successful
    assert db.get_pokemon_count() == 2
    if variant == "progressed":
        db.save_pokemon(dict(second, level=99, nickname="Keep me"))
    elif variant == "released":
        db.delete_pokemon("second")
    before = db.get_all_pokemon()
    paths["mypokemon_path"].write_text(json.dumps([first, second]))
    run(dialog)
    assert not dialog.migration_successful
    assert not db.is_migrated()
    assert db.get_all_pokemon() == before
    assert paths["mypokemon_path"].exists()
    assert "reconciliation" in dialog.log_area.toPlainText().lower()


@pytest.mark.parametrize("replacement", ["expanded", "missing"])
def test_checkpointed_source_replacement_is_not_archived(migration, replacement):
    db, dialog, paths, box = migration
    paths["mypokemon_path"].write_text(json.dumps([box[0]]))
    paths["mainpokemon_path"].write_text("[]")
    paths["team_path"].write_text("[]")

    def cancel():
        if "Pokemon verified" in dialog.status_label.text():
            dialog.cancelled = True

    with patch.object(QApplication, "processEvents", side_effect=cancel):
        dialog._run_migration()
    assert not dialog.migration_successful
    if replacement == "expanded":
        paths["mypokemon_path"].write_text(json.dumps(box[:2]))
    else:
        paths["mypokemon_path"].unlink()
    run(dialog)
    assert not dialog.migration_successful
    assert not db.is_migrated()
    assert db.get_pokemon_count() == 1
    assert not (paths["mypokemon_path"].parent / "json").exists()


@pytest.mark.parametrize("fault", ["missing", "changed"])
def test_known_verification_failure_survives_database_reopen(migration, fault):
    from Ankimon.pyobj.legacy_migration import LegacyMigration

    db, _, paths, box = migration
    first = dict(box[0], individual_id="first")
    second = dict(box[1], individual_id="second")
    paths["mypokemon_path"].write_text(json.dumps([first, second]))
    sources = {"mypokemon": paths["mypokemon_path"]}

    def corrupt_after_checkpoint(_, message):
        if "Pokemon verified" in message:
            if fault == "missing":
                db.delete_pokemon("second")
            else:
                db.save_pokemon(dict(second, level=99))

    stats = LegacyMigration(db, sources, corrupt_after_checkpoint).run()
    assert stats.get("integrity_issues")
    assert not db.is_migrated()
    db.close()
    reopened = AnkimonDB(db_path=db.db_path)
    try:
        before = reopened.get_all_pokemon()
        retry = LegacyMigration(reopened, sources).run()
        assert retry.get("integrity_issues")
        assert not reopened.is_migrated()
        assert reopened.get_all_pokemon() == before
        # Repair is an explicit external action; Retry must never restore it.
        reopened.save_pokemon(second)
        repaired = LegacyMigration(reopened, sources).run()
        assert not repaired.get("errors"), repaired
        assert reopened.is_migrated()
    finally:
        reopened.close()


def test_source_replacement_at_archive_boundary_stays_unresolved(migration):
    db, dialog, paths, box = migration
    paths["mypokemon_path"].write_text(json.dumps([box[0]]))
    paths["mainpokemon_path"].write_text("[]")
    paths["team_path"].write_text("[]")
    progress = dialog._update_progress

    def replace_before_archive(percent, message):
        progress(percent, message)
        if percent == 96:
            paths["mypokemon_path"].write_text(json.dumps(box[:2]))

    with patch.object(dialog, "_update_progress", side_effect=replace_before_archive):
        run(dialog)
    assert not dialog.migration_successful
    assert not db.is_migrated()
    assert paths["mypokemon_path"].exists()
    assert not (paths["mypokemon_path"].parent / "json").exists()
    run(dialog)
    assert not dialog.migration_successful
    assert db.get_pokemon_count() == 1


def test_missing_all_sources_cannot_bypass_retry_at_startup(migration):
    from Ankimon.pyobj import migration_dialog

    db, dialog, paths, box = migration
    paths["mypokemon_path"].write_text(json.dumps([box[0], "bad"]))
    run(dialog)
    assert not dialog.migration_successful
    for path in paths.values():
        path.unlink()
    # Exercise the real entry point without blocking on a modal event loop.
    with patch.object(migration_dialog.MigrationDialog, "exec", new=run):
        successful = migration_dialog.show_migration_dialog_if_needed(db, **paths)
    assert not successful
    assert not db.is_migrated()
    assert db.get_pokemon_count() == 1
