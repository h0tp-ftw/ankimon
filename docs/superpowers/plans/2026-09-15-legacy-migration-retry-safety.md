# Legacy Migration Retry Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make legacy migration inventory and Pokemon identity handling lossless across collisions, cancellations, failures, and gameplay between retries.

**Architecture:** Keep the shared Qt-free `LegacyMigration` runner as the single migration path. Strengthen `AnkimonDB` item and Pokemon normalization primitives, then persist compact source-identity checkpoints before later migration sources so Retry never rebuilds identity from mutable live state.

**Tech Stack:** Python 3, SQLite, pytest, pytest-qt/PyQt6, Ankimon Tier-1 and Tier-2 harnesses.

**Spec:** `docs/superpowers/specs/2026-09-15-legacy-migration-retry-safety-design.md`

## Global Constraints

- Use the real `AnkimonDB` and disposable SQLite files in regression tests.
- Keep the migration core free of `aqt` and top-level Qt imports.
- Do not weaken level or IV identity checks when normalizing `id` and `species_id`.
- Preserve `migrated` as the whole Phase-1 marker and `migrated_phase2` as the complete migration marker.
- Never restore a released Pokemon or overwrite gameplay progress from legacy JSON on Retry.
- Leave `.claude_probe_test.txt` and all user/runtime data untouched.
- Produce one implementation commit on `fix/856-legacy-migration`, then push it to `origin/fix/856-legacy-migration`.

---

### Task 1: Make inventory writes collision-safe and verify the whole batch

**Files:**
- Modify: `src/Ankimon/pyobj/database_manager.py:1537-1651`
- Modify: `src/Ankimon/pyobj/legacy_migration.py:254-272`
- Test: `tests/test_migration_recovery.py`

**Interfaces:**
- Consumes: `AnkimonDB.add_item(name, quantity, extra_data=None, commit=False)` and `aggregate_legacy_items(entries)`.
- Produces: collision-safe `AnkimonDB.save_item(...) -> bool`; `LegacyMigration.migrate_items(data)` that commits only after every expected stack passes final read-back.

- [x] **Step 1: Add the three failing inventory regression tests**

```python
from Ankimon.pyobj import database_manager


def _write_item_catalogue(tmp_path):
    catalogue = tmp_path / "items.csv"
    catalogue.write_text(
        "id,identifier,category_id,cost,fling_power,fling_effect_id\n"
        "1,master-ball,34,0,,\n",
        encoding="utf-8",
    )
    return catalogue


def test_uncatalogued_item_does_not_collide_with_catalogue_id(migration, tmp_path):
    db, dialog, paths, _ = migration
    paths["mypokemon_path"].write_text("[]")
    paths["mainpokemon_path"].write_text("[]")
    paths["items_path"].write_text('["custom-item", "master-ball"]')
    paths["team_path"].write_text("[]")
    with patch.object(database_manager, "csv_file_items_cost", _write_item_catalogue(tmp_path)):
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
    with patch.object(database_manager, "csv_file_items_cost", _write_item_catalogue(tmp_path)):
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

    with patch.object(database_manager, "csv_file_items_cost", _write_item_catalogue(tmp_path)), patch.object(
        db, "add_item", side_effect=lose_prior_stack
    ):
        run(dialog)
    assert not dialog.migration_successful
    assert not db.is_migrated_phase1()
    assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
```

- [x] **Step 2: Run all three tests and verify the expected RED state**

Run:

```bash
QT_QPA_PLATFORM=offscreen uv run --offline --with pytest --with pytest-qt --with PyQt6 --with requests python -m pytest \
  tests/test_migration_recovery.py::test_uncatalogued_item_does_not_collide_with_catalogue_id \
  tests/test_migration_recovery.py::test_catalogued_item_relocates_existing_positive_id_occupant \
  tests/test_migration_recovery.py::test_inventory_final_verification_rolls_back_late_stack_loss -q
```

Expected: the first test finds only one item; the second loses the old occupant;
the third reports migration success instead of rolling back.

- [x] **Step 3: Allocate negative uncatalogued IDs and relocate occupied catalogue IDs**

In `AnkimonDB.save_item`, resolve an existing row by name, then allocate and protect IDs before the existing `INSERT OR REPLACE`:

```python
def next_uncatalogued_id():
    row = cursor.execute("SELECT MIN(id) AS min_id FROM items").fetchone()
    minimum = row["min_id"]
    return -1 if minimum is None or minimum >= 0 else minimum - 1

existing_id = row["id"] if row else None
if item_id is None:
    item_id = existing_id if existing_id is not None else next_uncatalogued_id()
else:
    occupied = cursor.execute(
        "SELECT item_name FROM items WHERE id = ? AND item_name <> ?",
        (item_id, item_name),
    ).fetchone()
    if occupied:
        cursor.execute(
            "UPDATE items SET id = ? WHERE id = ?",
            (next_uncatalogued_id(), item_id),
        )
```

Keep relocation and insertion in the same transaction and retain existing metadata fallback behavior.

- [x] **Step 4: Add final whole-batch item verification before commit**

After all `add_item(..., commit=False)` calls:

```python
for name, (quantity, _extra) in totals.items():
    saved = self.db.get_item(name)
    if not saved or saved["quantity"] != quantity:
        message = f"items: {name} expected quantity {quantity}; saved stack differs"
        self.stats.setdefault("integrity_issues", []).append(message)
        raise ValueError(message)
self.db._get_connection().commit()
```

The existing `step()` exception boundary performs the rollback.

- [x] **Step 5: Run the two tests and the item/database regression set GREEN**

Run:

```bash
QT_QPA_PLATFORM=offscreen uv run --offline --with pytest --with pytest-qt --with PyQt6 --with requests python -m pytest \
  tests/test_migration_recovery.py::test_uncatalogued_item_does_not_collide_with_catalogue_id \
  tests/test_migration_recovery.py::test_catalogued_item_relocates_existing_positive_id_occupant \
  tests/test_migration_recovery.py::test_inventory_final_verification_rolls_back_late_stack_loss \
  tests/test_database_manager.py -q
```

Expected: all selected tests pass with both stacks preserved and the injected late loss rolled back.

### Task 2: Normalize species aliases inside the IV-aware matcher

**Files:**
- Modify: `src/Ankimon/pyobj/database_manager.py:385-493`
- Modify: `src/Ankimon/pyobj/legacy_migration.py:187-219`
- Test: `tests/test_migration_recovery.py`

**Interfaces:**
- Consumes: legacy Pokemon dicts that may carry `id` or `species_id`.
- Produces: `legacy_species_id(record: dict) -> str`; alias-aware `find_matching_captured(...)` and team fallback identity.

- [x] **Step 1: Add the failing IV-aware alias test**

```python
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
    assert db.get_team() == [{"individual_id": db.get_all_pokemon()[0]["individual_id"]}]
```

- [x] **Step 2: Run the test and verify RED**

Run:

```bash
QT_QPA_PLATFORM=offscreen uv run --offline --with pytest --with pytest-qt --with PyQt6 --with requests python -m pytest \
  tests/test_migration_recovery.py::test_team_species_id_alias_matches_collection_id_with_ivs -q
```

Expected: migration is incomplete with `team member 1 has no captured Pokemon`.

- [x] **Step 3: Add one shared species-key normalizer and use it in strong matching**

```python
def legacy_species_id(record: Dict[str, Any]) -> str:
    value = record.get("species_id", record.get("id"))
    return "" if value is None else str(value)
```

Replace `candidate.get("id") == main_pokemon.get("id")` in
`find_matching_captured()` with
`legacy_species_id(candidate) == legacy_species_id(main_pokemon)`. Import and
use the same helper in the reduced no-IV team identity tuple. Do not remove the
IV comparison or broaden the `not member.get("iv")` fallback.

- [x] **Step 4: Run the alias test and existing identity tests GREEN**

Run:

```bash
QT_QPA_PLATFORM=offscreen uv run --offline --with pytest --with pytest-qt --with PyQt6 --with requests python -m pytest \
  tests/test_migration_recovery.py::test_team_species_id_alias_matches_collection_id_with_ivs \
  tests/test_migration_recovery.py::test_explicit_main_and_team_ids_distinguish_identical_twins \
  tests/test_migration_recovery.py::test_distinct_main_with_same_species_and_level_preserves_both \
  tests/test_migration_recovery.py::test_unique_main_id_survives_a_level_change -q
```

Expected: all four pass.

### Task 3: Checkpoint source identities before later phases and reuse them for team Retry

**Files:**
- Modify: `src/Ankimon/pyobj/legacy_migration.py:29-252,416-503`
- Test: `tests/test_migration_recovery.py`

**Interfaces:**
- Consumes: verified collection/main records and metadata rows `migration_verified_collection` / `migration_verified_main`.
- Produces: version-2 compact mapping payloads and immutable legacy matching candidates restored on Retry.

- [x] **Step 1: Add the pending-team and earlier-item-failure RED tests**

```python
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
    captured = db.get_all_pokemon()[0]
    captured["level"] = 11
    db.save_pokemon(captured)
    run(dialog)
    assert dialog.migration_successful, dialog.log_area.toPlainText()
    assert db.get_team() == [{"individual_id": captured["individual_id"]}]
    assert db.get_pokemon(captured["individual_id"])["level"] == 11


def test_item_failure_retry_preserves_level_and_release(migration):
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
    rows = sorted(db.get_all_pokemon(), key=lambda row: row["level"])
    survivor, released = rows
    survivor["level"] = 11
    db.save_main_pokemon(survivor)
    db.delete_pokemon(released["individual_id"])
    run(dialog)
    assert dialog.migration_successful, dialog.log_area.toPlainText()
    assert db.get_pokemon_count() == 1
    assert db.get_pokemon(survivor["individual_id"])["level"] == 11
    assert db.get_main_pokemon()["level"] == 11
    assert db.get_pokemon(released["individual_id"]) is None
```

- [x] **Step 2: Run both tests and verify RED**

Run:

```bash
QT_QPA_PLATFORM=offscreen uv run --offline --with pytest --with pytest-qt --with PyQt6 --with requests python -m pytest \
  tests/test_migration_recovery.py::test_pending_team_retry_uses_checkpointed_identity_after_level_up \
  tests/test_migration_recovery.py::test_item_failure_retry_preserves_level_and_release -q
```

Expected: the pending team cannot find its level-10 snapshot after the level-up; the item-failure Retry restores two legacy Pokemon and reverts the survivor.

- [x] **Step 3: Build compact immutable mapping candidates**

Add `self.main_candidate = None` and a static mapping constructor:

```python
@staticmethod
def mapping_candidate(original, individual_id):
    legacy_id = original.get("individual_id")
    return {
        "individual_id": individual_id,
        "_legacy_individual_id": legacy_id if is_valid_individual_id(legacy_id) else None,
        "name": canonical_pokemon_name(original.get("name")),
        "level": original.get("level"),
        "id": legacy_species_id(original),
        "iv": original.get("iv"),
    }
```

Append a collection candidate only after its row passes read-back. Set the main
candidate only after `save_main_pokemon` passes read-back. Include these compact
candidates in `captured_candidates()` ahead of live database rows.

- [x] **Step 4: Persist and strictly restore versioned checkpoints**

Use versioned JSON payloads:

```python
collection_payload = {
    "version": 2,
    "duplicate_ids": sorted(self.duplicate_ids),
    "records": self.collection,
}
main_payload = {"version": 2, "record": self.main_candidate}
```

Validate `version == 2`, that `records` is a list, and that every assigned ID is
a valid unique string. Invalid payloads raise `ValueError` and fail closed.
Restore the candidates into memory without reading or writing their mutable live
Pokemon data.

- [x] **Step 5: Resolve original explicit IDs through mapping candidates**

Before live/full-field fallback in `resolve_member`, consider candidates whose
`_legacy_individual_id` equals the team member's old ID. A unique mapping wins
by identity; duplicate mappings continue using `find_matching_captured()` and
the existing ordered queue semantics. The returned candidate's assigned
`individual_id` must still exist in `captured_pokemon` before team save.

- [x] **Step 6: Move checkpoint commits to source boundaries**

Refactor `run()` in this order:

```python
restore collection checkpoint or migrate collection
verify collection; return on errors
commit migration_verified_collection immediately

restore main checkpoint or migrate main
verify collection/main; return on errors
commit migration_verified_main immediately

if Phase 1 incomplete: migrate items and badges
verify current-run Pokemon; return on errors
commit migrated=true

migrate team/history/userdata/rate
verify; commit migrated_phase2=true only on full success
```

When either source checkpoint already exists, skip importing that source even
if `migrated` is still false. Report preservation, but do not add checkpointed
records to `expected_pokemon`, because a later Retry must tolerate intentional
live releases and progression. Commit the collection checkpoint before the
final cancellable collection-summary callback so a cancel at that exact source
boundary cannot leave verified rows without their identity mapping.

- [x] **Step 7: Run the two tests and all migration recovery tests GREEN**

Run:

```bash
QT_QPA_PLATFORM=offscreen uv run --offline --with pytest --with pytest-qt --with PyQt6 --with requests python -m pytest tests/test_migration_recovery.py -q
```

Expected: all original and new migration recovery tests pass.

### Task 4: Audit, verify, commit, and push the PR

**Files:**
- Modify: `docs/superpowers/plans/2026-09-15-legacy-migration-retry-safety.md` (mark completed steps)
- Verify: all modified production/test files and repository gates

**Interfaces:**
- Consumes: completed Tasks 1-3 and the seven required outcomes in the design spec.
- Produces: one reviewed implementation commit pushed to `origin/fix/856-legacy-migration`.

- [x] **Step 1: Run focused migration and integrity coverage**

```bash
QT_QPA_PLATFORM=offscreen uv run --offline --with pytest --with pytest-qt --with PyQt6 --with requests python -m pytest \
  tests/test_migration_recovery.py tests/test_migration_legacy_save.py tests/test_migration_dialog.py tests/test_database_manager.py -q
```

Expected: all selected tests pass.

- [x] **Step 2: Run the Tier-1 repository gate**

```bash
python3 harness/check.py
```

Expected: every probe, smoke scenario, and regression test passes.

- [x] **Step 3: Run the full pytest suite in the cached test environment**

```bash
QT_QPA_PLATFORM=offscreen uv run --offline --with pytest --with pytest-qt --with PyQt6 --with requests --with markdown python -m pytest tests/ -q
```

Expected: all tests pass. If another declared repository dependency is missing,
add it to the disposable `uv run --with` environment rather than to the addon.

- [x] **Step 4: Run the real migration probe**

```bash
QT_QPA_PLATFORM=offscreen uv run --offline --with PyQt6 --with requests --with markdown python -m harness.checks.probe_real_migration
```

Expected: `probe_real_migration: OK` with no traceback.

- [x] **Step 5: Review the final diff against each pasted issue**

Confirm manually that the diff contains: negative uncatalogued IDs and occupied
ID relocation; final inventory batch verification; alias normalization before
IV matching; persisted collection/main mappings; checkpoint commits before
items; pending-team reuse; and no startup/gameplay restriction workaround.

- [x] **Step 6: Fold implementation into the existing unpushed design commit**

```bash
git add docs/superpowers src/Ankimon/pyobj/database_manager.py src/Ankimon/pyobj/legacy_migration.py tests/test_migration_recovery.py
git commit --amend -m "fix(migration): preserve retries without data loss (#856)"
```

Do not stage `.claude_probe_test.txt`.

- [x] **Step 7: Push the reviewed PR branch**

```bash
git push origin fix/856-legacy-migration
```

Expected: the remote branch advances from `a31c0b67` to the amended implementation commit.
