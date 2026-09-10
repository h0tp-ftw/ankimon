"""
Regression tests for migrating legacy JSON saves into SQLite.

Older Ankimon versions wrote items.json as a flat list of strings, Pokémon
without an individual_id (or with junk in it), quantities stored as strings,
and a mainpokemon.json that duplicates a captured Pokémon. Both migration
paths — ``AnkimonDB.migrate_from_json`` (harness/tests) and the
``MigrationDialog`` users see on upgrade — must absorb all of that without
crashing, without duplicating the starter, and without doubling anything when
the user clicks Retry after a partial failure.
"""

import json
from unittest.mock import patch

import pytest
from PyQt6.QtWidgets import QApplication

from Ankimon.pyobj.database_manager import (
    AnkimonDB,
    aggregate_legacy_items,
    canonical_pokemon_name,
    coerce_item_quantity,
    is_valid_individual_id,
    normalize_legacy_item,
)
from Ankimon.pyobj.migration_dialog import MigrationDialog


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


def _pokemon(name, species_id, level, **overrides):
    record = {
        "name": name, "nickname": None, "gender": "M", "level": level, "id": species_id,
        "ability": "Blaze", "type": ["Fire", "Flying"],
        "stats": {"hp": 78, "atk": 84, "def": 78, "spa": 109, "spd": 85, "spe": 100},
        "ev": {"hp": 56, "atk": 73, "def": 63, "spa": 35, "spd": 31, "spe": 43},
        "iv": {"hp": 5, "atk": 11, "def": 2, "spa": 23, "spd": 9, "spe": 31},
        "attacks": ["flamethrower", "airslash"], "base_experience": 267,
        "growth_rate": "medium-slow",
    }
    record.update(overrides)
    return record


def _write_save(tmp_path, mypokemon, mainpokemon, items, badges):
    paths = {
        "mypokemon_path": tmp_path / "mypokemon.json",
        "mainpokemon_path": tmp_path / "mainpokemon.json",
        "items_path": tmp_path / "items.json",
        "badges_path": tmp_path / "badges.json",
    }
    paths["mypokemon_path"].write_text(json.dumps(mypokemon), encoding="utf-8")
    paths["mainpokemon_path"].write_text(json.dumps(mainpokemon), encoding="utf-8")
    paths["items_path"].write_text(json.dumps(items), encoding="utf-8")
    paths["badges_path"].write_text(json.dumps(badges), encoding="utf-8")
    return paths


def _clear_migration_flags(db):
    conn = db._get_connection()
    conn.execute("DELETE FROM metadata WHERE key IN ('migrated', 'migrated_phase2')")
    conn.commit()


# ---------------------------------------------------------------- pure helpers


@pytest.mark.parametrize(
    "value, expected",
    [
        (3, 3), ("2", 2), (" 7 ", 7), (2.0, 2),
        (None, None), (0, None), (-1, None), (True, None), (False, None),
        ("many", None), ("", None), (1.5, None), ([2], None), ({"n": 2}, None),
    ],
)
def test_coerce_item_quantity(value, expected):
    assert coerce_item_quantity(value) == expected


@pytest.mark.parametrize(
    "item, expected",
    [
        ("potion", ("potion", 1, None)),
        ("  pp-max ", ("pp-max", 1, None)),
        ("Potion", ("potion", 1, None)),
        ({"item": "PP-Max"}, ("pp-max", 1, {"item": "PP-Max"})),
        ("", None),
        ({"item": "potion", "quantity": "2"}, ("potion", 2, {"item": "potion", "quantity": "2"})),
        ({"name": "ether", "amount": 3}, ("ether", 3, {"name": "ether", "amount": 3})),
        ({"item_name": "elixir"}, ("elixir", 1, {"item_name": "elixir"})),
        ({"item": "potion", "quantity": None}, None),
        ({"item": "potion", "quantity": 0}, None),
        ({"item": "potion", "quantity": -4}, None),
        ({"quantity": 2}, None),
        ({"item": "", "quantity": 2}, None),
        (42, None), (None, None), ([], None),
    ],
)
def test_normalize_legacy_item(item, expected):
    assert normalize_legacy_item(item) == expected


def test_aggregate_legacy_items_folds_duplicates_and_drops_junk():
    totals = aggregate_legacy_items(
        ["potion", {"item": "potion", "quantity": "2"}, "wide-lens", "wide-lens", None, 7, {"item": "ether", "quantity": 0}]
    )
    assert totals == {"potion": (3, {"item": "potion", "quantity": "2"}), "wide-lens": (2, None)}
    assert aggregate_legacy_items({"potion": 2}) == {}
    assert aggregate_legacy_items(None) == {}


def test_aggregate_legacy_items_folds_case_variants():
    totals = aggregate_legacy_items(["Potion", "potion", {"item": "POTION", "quantity": 2}])
    assert totals == {"potion": (4, {"item": "POTION", "quantity": 2})}


@pytest.mark.parametrize(
    "value, expected",
    [("abc", True), (" x ", True), ("", False), ("   ", False), (None, False),
     (5, False), (["a"], False), ({"id": "a"}, False)],
)
def test_is_valid_individual_id(value, expected):
    assert is_valid_individual_id(value) is expected


def test_canonical_pokemon_name():
    assert canonical_pokemon_name("Mr-Mime") == canonical_pokemon_name("mr mime") == "mrmime"
    assert canonical_pokemon_name(None) == ""


# --------------------------------------------------- AnkimonDB.migrate_from_json


def test_migrate_from_json_legacy_files(tmp_path):
    """Legacy save: no individual_ids, flat string items, starter duplicated in mainpokemon.json."""
    db = AnkimonDB(db_path=tmp_path / "ankimon.db")
    charizard = _pokemon("Charizard", 6, 59)
    pikachu = _pokemon("Pikachu", 25, 25, nickname="Sparky", ability="Static", type=["Electric"],
                       iv={"hp": 15, "atk": 15, "def": 15, "spa": 15, "spd": 15, "spe": 15})
    paths = _write_save(tmp_path, [charizard, pikachu], [charizard],
                        ["potion", "pp-max", "wide-lens", "wide-lens"], [1, 2, 3, 4])

    stats = db.migrate_from_json(**paths)

    assert stats["pokemon"] == 2
    assert stats["main"] == 1
    assert "integrity_issues" not in stats
    assert db.get_pokemon_count() == 2
    main_p = db.get_main_pokemon()
    assert main_p is not None
    assert main_p["name"] == "Charizard"
    assert main_p["level"] == 59
    assert db.get_item("wide-lens")["quantity"] == 2
    assert db.get_item("potion")["quantity"] == 1
    assert db.execute("SELECT COUNT(*) FROM badges").fetchone()[0] == 4


def test_migrate_from_json_skips_string_entries_and_repairs_junk_ids(tmp_path):
    """String entries in mypokemon/mainpokemon are skipped; list/dict/empty ids never raise."""
    db = AnkimonDB(db_path=tmp_path / "ankimon.db")
    box = [
        "not-a-pokemon",
        _pokemon("Charizard", 6, 59, individual_id=["not", "a", "string"]),
        _pokemon("Pikachu", 25, 25, individual_id={"nested": True}),
        _pokemon("Bulbasaur", 1, 5, individual_id=""),
        _pokemon("Squirtle", 7, 5, individual_id="keep-me"),
    ]
    paths = _write_save(tmp_path, box, ["also-a-string"], ["potion"], [1])

    stats = db.migrate_from_json(**paths)

    assert stats["pokemon"] == 4
    assert stats["main"] == 0
    assert "integrity_issues" not in stats
    ids = [p["individual_id"] for p in db.get_all_pokemon()]
    assert len(ids) == 4 and len(set(ids)) == 4
    assert all(isinstance(i, str) and i for i in ids)
    assert "keep-me" in ids


def test_migrate_from_json_matches_main_by_canonical_name(tmp_path):
    """A case- or hyphen-only name difference must not create a second captured row."""
    db = AnkimonDB(db_path=tmp_path / "ankimon.db")
    captured = _pokemon("Mr-Mime", 122, 30, individual_id="mime-1")
    main = _pokemon("mr mime", 122, 30)  # legacy mainpokemon.json: no individual_id
    paths = _write_save(tmp_path, [captured], [main], [], [])

    stats = db.migrate_from_json(**paths)

    assert stats["main"] == 1
    assert db.get_pokemon_count() == 1
    assert db.get_main_pokemon()["individual_id"] == "mime-1"


def test_migrate_from_json_coerces_item_quantities(tmp_path):
    """Quantities are normalised to positive ints; junk entries are skipped, not written."""
    db = AnkimonDB(db_path=tmp_path / "ankimon.db")
    items = [
        {"item": "potion", "quantity": "2"},
        {"item": "potion", "quantity": 2.0},
        {"item": "ether", "quantity": None},
        {"item": "elixir", "quantity": 0},
        {"item": "revive", "quantity": -1},
        {"item": "rare-candy", "quantity": True},
        {"name": "pp-max"},
        "",
        42,
    ]
    paths = _write_save(tmp_path, [_pokemon("Charizard", 6, 59)], [], items, [])

    stats = db.migrate_from_json(**paths)

    assert db.get_item("potion")["quantity"] == 4
    assert db.get_item("pp-max")["quantity"] == 1
    for name in ("ether", "elixir", "revive", "rare-candy"):
        assert db.get_item(name) is None, name
    assert db.execute("SELECT SUM(quantity) FROM items").fetchone()[0] == 5
    assert stats["items"] == 2
    assert "integrity_issues" not in stats


def test_migrate_from_json_flags_lost_item_quantity(tmp_path):
    """The integrity check compares total quantity, so a lost stack is reported."""
    db = AnkimonDB(db_path=tmp_path / "ankimon.db")
    paths = _write_save(tmp_path, [], [], [{"item": "potion", "quantity": 5}], [])
    original = db.add_item

    def lossy_add_item(item_name, quantity=1, extra_data=None, commit=True):
        return original(item_name, 1, extra_data=extra_data, commit=commit)

    with patch.object(db, "add_item", side_effect=lossy_add_item):
        stats = db.migrate_from_json(**paths)

    assert any(issue.startswith("items:") for issue in stats.get("integrity_issues", []))


def test_migrate_from_json_case_variants_share_one_row(tmp_path):
    db = AnkimonDB(db_path=tmp_path / "ankimon.db")
    paths = _write_save(tmp_path, [], [], ["Potion", "potion", {"item": "POTION", "quantity": 2}], [])

    stats = db.migrate_from_json(**paths)

    assert db.get_item("potion")["quantity"] == 4
    assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1
    assert "integrity_issues" not in stats


def test_migrate_from_json_failed_items_step_leaves_phase1_unmarked(tmp_path):
    """A failure inside Phase 1 must not write the marker, or Retry would skip the phase for good."""
    db = AnkimonDB(db_path=tmp_path / "ankimon.db")
    charizard = _pokemon("Charizard", 6, 59)
    paths = _write_save(tmp_path, [charizard], [charizard], ["potion", "wide-lens"], [1])
    original = db.add_item
    calls = {"n": 0}

    def flaky_add_item(item_name, quantity=1, extra_data=None, commit=True):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("disk full")
        return original(item_name, quantity, extra_data=extra_data, commit=commit)

    with patch.object(db, "add_item", side_effect=flaky_add_item):
        stats = db.migrate_from_json(**paths)

    assert any(err.startswith("items.json:") for err in stats["errors"])
    assert not db.is_migrated_phase1()
    assert not db.is_migrated()
    assert db.get_item("potion") is None  # the partial batch was rolled back
    assert db.get_pokemon_count() == 1  # committed sources stay put

    stats = db.migrate_from_json(**paths)  # the retry

    assert "errors" not in stats
    assert db.is_migrated()
    assert db.get_pokemon_count() == 1
    assert db.get_main_pokemon()["name"] == "Charizard"
    assert db.get_item("potion")["quantity"] == 1
    assert db.get_item("wide-lens")["quantity"] == 1


def test_migrate_from_json_retry_does_not_double_anything(tmp_path):
    """Re-running Phase 1 (what Retry does) must not duplicate Pokémon or stack items twice."""
    db = AnkimonDB(db_path=tmp_path / "ankimon.db")
    charizard = _pokemon("Charizard", 6, 59)
    paths = _write_save(tmp_path, [charizard, _pokemon("Pikachu", 25, 25)], [charizard],
                        ["wide-lens", "wide-lens", {"item": "potion", "quantity": 3}], [1, 2])

    first = db.migrate_from_json(**paths)
    _clear_migration_flags(db)
    second = db.migrate_from_json(**paths)

    assert first["pokemon"] == second["pokemon"] == 2
    assert db.get_pokemon_count() == 2
    assert db.get_main_pokemon()["name"] == "Charizard"
    assert db.get_item("wide-lens")["quantity"] == 2
    assert db.get_item("potion")["quantity"] == 3
    assert "integrity_issues" not in second


# ------------------------------------------------------------ MigrationDialog


def _dialog(db, paths):
    return MigrationDialog(db, **paths)


def test_migration_dialog_legacy_files(qapp, tmp_path):
    db = AnkimonDB(db_path=tmp_path / "ankimon.db")
    charizard = _pokemon("Charizard", 6, 59)
    paths = _write_save(tmp_path, [charizard], [charizard], ["potion", "wide-lens", "wide-lens"], [1, 2])
    dialog = _dialog(db, paths)

    with patch("PyQt6.QtWidgets.QApplication.processEvents"):
        dialog._run_migration()

    assert dialog.migration_successful
    assert db.get_pokemon_count() == 1
    main_p = db.get_main_pokemon()
    assert main_p is not None
    assert main_p["name"] == "Charizard"
    assert db.get_item("wide-lens")["quantity"] == 2


def test_migration_dialog_canonical_match_and_junk_ids(qapp, tmp_path):
    db = AnkimonDB(db_path=tmp_path / "ankimon.db")
    box = [
        "stray-string",
        _pokemon("Mr-Mime", 122, 30, individual_id=["junk"]),
        _pokemon("Pikachu", 25, 25, individual_id=None),
    ]
    main = _pokemon("MR MIME", 122, 30)
    items = [{"item": "potion", "quantity": "2"}, {"item": "ether", "quantity": None}, "potion"]
    paths = _write_save(tmp_path, box, [main], items, [1])
    dialog = _dialog(db, paths)

    with patch("PyQt6.QtWidgets.QApplication.processEvents"):
        dialog._run_migration()

    assert dialog.migration_successful, dialog.log_area.toPlainText()
    assert db.get_pokemon_count() == 2
    assert canonical_pokemon_name(db.get_main_pokemon()["name"]) == "mrmime"
    assert db.get_item("potion")["quantity"] == 3
    assert db.get_item("ether") is None
    assert "Skipped 1 unreadable item entries" in dialog.log_area.toPlainText()


def test_migration_dialog_retry_after_partial_failure_does_not_double(qapp, tmp_path):
    """Items commit in Step 3 and the Phase-1 flag is only written after Step 4.

    If Step 4 fails, Retry re-runs Phase 1 on a DB that already holds the
    items and Pokémon — it must upsert, not append.
    """
    db = AnkimonDB(db_path=tmp_path / "ankimon.db")
    charizard = _pokemon("Charizard", 6, 59)
    paths = _write_save(tmp_path, [charizard, _pokemon("Pikachu", 25, 25)], [charizard],
                        ["wide-lens", "wide-lens", {"item": "potion", "quantity": 3}], [1, 2])
    dialog = _dialog(db, paths)

    with patch("PyQt6.QtWidgets.QApplication.processEvents"), \
            patch.object(db, "save_badge", side_effect=RuntimeError("disk full")):
        dialog._run_migration()
    assert not dialog.migration_successful
    assert db.get_item("wide-lens")["quantity"] == 2
    assert not db.is_migrated_phase1()

    with patch("PyQt6.QtWidgets.QApplication.processEvents"):
        dialog._run_migration()  # what the Retry button does

    assert dialog.migration_successful, dialog.log_area.toPlainText()
    assert db.get_pokemon_count() == 2
    assert db.get_main_pokemon()["name"] == "Charizard"
    assert db.get_item("wide-lens")["quantity"] == 2
    assert db.get_item("potion")["quantity"] == 3
