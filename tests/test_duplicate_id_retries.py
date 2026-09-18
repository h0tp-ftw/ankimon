"""Duplicate legacy aliases must retain source ownership across partial imports."""

import hashlib
import json

import pytest

from Ankimon.pyobj.database_manager import AnkimonDB


@pytest.fixture
def duplicate_sources(tmp_path):
    pikachu = dict(
        id=25, name="Pikachu", level=10, iv={"hp": 31}, individual_id="duplicate"
    )
    bulbasaur = dict(
        id=1, name="Bulbasaur", level=10, iv={"hp": 17}, individual_id="duplicate"
    )
    sources = {}
    for name, data in {
        "mypokemon": [pikachu, bulbasaur],
        "mainpokemon": [bulbasaur],
        "team": [pikachu, bulbasaur],
    }.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(data))
        sources[name] = path
    return sources, pikachu, bulbasaur


def test_main_resolves_reassigned_legacy_alias():
    from Ankimon.pyobj.legacy_migration import LegacyMigration

    source = dict(
        id=1, name="Bulbasaur", level=10, iv={"hp": 17}, individual_id="duplicate"
    )
    candidate = LegacyMigration.mapping_candidate(source, "reassigned")
    runner = LegacyMigration(None, {})
    assert runner.resolve_member(source, [candidate]) == candidate


@pytest.mark.parametrize("team", [False, True])
@pytest.mark.parametrize("reassigned", [False, True])
@pytest.mark.parametrize(
    "difference",
    [
        {"id": 25, "name": "Pikachu"},
        {"level": 20},
        {"iv": {"hp": 31}},
    ],
    ids=["species", "level", "ivs"],
)
def test_duplicate_alias_requires_matching_source_fields(team, reassigned, difference):
    from Ankimon.pyobj.legacy_migration import LegacyMigration

    source = dict(
        id=1, name="Bulbasaur", level=10, iv={"hp": 17}, individual_id="duplicate"
    )
    candidate = LegacyMigration.mapping_candidate(
        dict(source, **difference), "reassigned" if reassigned else "duplicate"
    )
    runner = LegacyMigration(None, {})
    runner.duplicate_ids = {"duplicate"}
    assert runner.resolve_member(source, [candidate], team=team) is None


@pytest.mark.parametrize("team", [False, True])
@pytest.mark.parametrize("main_explicit", [False, True])
@pytest.mark.parametrize(
    "failure,failed_index",
    [
        (None, None),
        ("insert", 0),
        ("checkpoint", 0),
        ("insert", 1),
        ("checkpoint", 1),
    ],
)
def test_duplicate_id_partial_retry_preserves_both_species(
    tmp_path, duplicate_sources, failure, failed_index, main_explicit, team
):
    from Ankimon.pyobj.legacy_migration import LegacyMigration

    sources, pikachu, bulbasaur = duplicate_sources
    if not main_explicit:
        sources["mainpokemon"].write_text(
            json.dumps([{k: v for k, v in bulbasaur.items() if k != "individual_id"}])
        )
    if not team:
        sources["team"].write_text("[]")
    original_bytes = {key: path.read_bytes() for key, path in sources.items()}
    db = AnkimonDB(db_path=tmp_path / "ankimon.db")
    try:
        runner = LegacyMigration(db, sources)
        if failure:
            failed_source = [pikachu, bulbasaur][failed_index]
        if failure == "insert":
            failed_id = (
                "duplicate"
                if failed_index == 0
                else runner.generated_id("collection", failed_index, failed_source)
            )
            db.execute(f"""
                CREATE TRIGGER fail_first BEFORE INSERT ON captured_pokemon
                WHEN NEW.individual_id = '{failed_id}' AND NEW.is_main = 0
                BEGIN SELECT RAISE(ABORT, 'injected first row failure'); END
            """)
        elif failure == "checkpoint":
            key = runner.collection_row_key(failed_index, failed_source)
            db.execute(f"""
                CREATE TRIGGER fail_first BEFORE INSERT ON metadata
                WHEN NEW.key = '{key}'
                BEGIN SELECT RAISE(ABORT, 'injected first checkpoint failure'); END
            """)
        stats = runner.run()
        if failure:
            assert stats.get("errors"), stats
            assert not db.is_migrated()
            if failed_index == 0:
                assert db.get_all_pokemon() == [db.get_main_pokemon()]
                assert db.get_main_pokemon()["id"] == 1
                assert db.get_main_pokemon()["individual_id"] != "duplicate"
            else:
                assert db.get_pokemon("duplicate") == pikachu
            db.execute("DROP TRIGGER fail_first")
            db._get_connection().commit()
        else:
            assert not stats.get("errors"), stats
        db.close()
        db = AnkimonDB(db_path=tmp_path / "ankimon.db")
        for _ in range(2):
            stats = LegacyMigration(db, sources).run()
            assert not stats.get("errors"), stats
            assert db.is_migrated()
            rows = db.get_all_pokemon()
            assert sorted(p["id"] for p in rows) == [1, 25]
            by_species = {p["id"]: p for p in rows}
            for original in [pikachu, bulbasaur]:
                saved = by_species[original["id"]]
                assert {k: v for k, v in saved.items() if k != "individual_id"} == {
                    k: v for k, v in original.items() if k != "individual_id"
                }
            assert db.get_main_pokemon() == by_species[1]
            assert db.get_team() == (
                [
                    {"individual_id": by_species[p["id"]]["individual_id"]}
                    for p in [pikachu, bulbasaur]
                ]
                if team
                else []
            )
            checkpoints = db.execute(
                "SELECT value FROM metadata WHERE key LIKE 'migration_collection_row:%'"
            ).fetchall()
            assert len(checkpoints) == 2
            for row in checkpoints:
                payload = json.loads(row["value"])
                assert str(payload["record"]["id"]) == str(payload["snapshot"]["id"])
                assert payload["record"]["name"] == payload["snapshot"]["name"].lower()
            assert {
                key: path.read_bytes() for key, path in sources.items()
            } == original_bytes
    finally:
        db.close()


@pytest.mark.parametrize("team", [False, True])
@pytest.mark.parametrize("main_index", [0, 1])
@pytest.mark.parametrize("failure", ["insert", "checkpoint"])
def test_duplicate_id_retry_after_all_collection_entries_fail(
    tmp_path, duplicate_sources, failure, main_index, team
):
    from Ankimon.pyobj.legacy_migration import LegacyMigration

    sources, pikachu, bulbasaur = duplicate_sources
    originals = [pikachu, bulbasaur]
    sources["mainpokemon"].write_text(json.dumps([originals[main_index]]))
    if not team:
        sources["team"].write_text("[]")
    original_bytes = {key: path.read_bytes() for key, path in sources.items()}
    db = AnkimonDB(db_path=tmp_path / "ankimon.db")
    try:
        if failure == "insert":
            db.execute("""
                CREATE TRIGGER fail_collection BEFORE INSERT ON captured_pokemon
                WHEN NEW.is_main = 0
                BEGIN SELECT RAISE(ABORT, 'injected collection failure'); END
            """)
        else:
            db.execute("""
                CREATE TRIGGER fail_collection BEFORE INSERT ON metadata
                WHEN NEW.key LIKE 'migration_collection_row:%'
                BEGIN SELECT RAISE(ABORT, 'injected collection checkpoint failure'); END
            """)
        stats = LegacyMigration(db, sources).run()
        assert stats["pokemon_failed"] == 2
        assert stats.get("errors"), stats
        assert not db.is_migrated()
        db.execute("DROP TRIGGER fail_collection")
        db._get_connection().commit()
        # Reopen on every attempt: ownership must survive process boundaries,
        # and further retries must neither add copies nor change team aliases.
        for _ in range(3):
            db.close()
            db = AnkimonDB(db_path=tmp_path / "ankimon.db")
            stats = LegacyMigration(db, sources).run()
            assert not stats.get("errors"), stats
            assert db.is_migrated()
            rows = db.get_all_pokemon()
            assert sorted(p["id"] for p in rows) == [1, 25]
            by_species = {p["id"]: p for p in rows}
            for original in originals:
                saved = by_species[original["id"]]
                assert saved == dict(original, individual_id=saved["individual_id"])
            assert db.get_main_pokemon() == by_species[originals[main_index]["id"]]
            assert db.get_team() == (
                [
                    {"individual_id": by_species[p["id"]]["individual_id"]}
                    for p in originals
                ]
                if team
                else []
            )
            checkpoints = db.execute(
                "SELECT value FROM metadata WHERE key LIKE 'migration_collection_row:%'"
            ).fetchall()
            assert len(checkpoints) == 2
            for row in checkpoints:
                payload = json.loads(row["value"])
                assert str(payload["record"]["id"]) == str(payload["snapshot"]["id"])
                assert payload["record"]["name"] == payload["snapshot"]["name"].lower()
            assert {
                key: path.read_bytes() for key, path in sources.items()
            } == original_bytes
    finally:
        db.close()


@pytest.mark.parametrize("state", ["unchanged", "progressed", "released"])
def test_pending_collection_cannot_claim_unrelated_checkpointed_main(
    tmp_path, duplicate_sources, state
):
    from Ankimon.pyobj.legacy_migration import LegacyMigration

    sources, pikachu, bulbasaur = duplicate_sources
    original_bytes = {key: path.read_bytes() for key, path in sources.items()}
    db = AnkimonDB(db_path=tmp_path / "ankimon.db")
    try:
        # Recreate the partial state written by the old runner: collection entry
        # two owns a reassigned row, while main incorrectly owns "duplicate".
        runner = LegacyMigration(db, sources)
        for key, path in sources.items():
            runner.pin_source(key, hashlib.sha256(path.read_bytes()).hexdigest())
        reassigned = dict(
            bulbasaur, individual_id=runner.generated_id("collection", 1, bulbasaur)
        )
        assert db.save_pokemon(reassigned)
        runner.save_checkpoint(
            runner.collection_row_key(1, bulbasaur),
            {
                "record": runner.mapping_candidate(
                    bulbasaur, reassigned["individual_id"]
                ),
                "snapshot": reassigned,
            },
        )
        assert db.save_main_pokemon(bulbasaur)
        runner.main_candidate = runner.mapping_candidate(bulbasaur, "duplicate")
        runner.save_main_checkpoint()
        if state == "progressed":
            # Even live contents matching the pending entry cannot prove ownership.
            assert db.save_main_pokemon(pikachu)
        elif state == "released":
            assert db.add_to_history(bulbasaur)
            assert db.delete_pokemon("duplicate")
        before = db.get_all_pokemon()
        db.close()
        db = AnkimonDB(db_path=tmp_path / "ankimon.db")
        for _ in range(2):
            stats = LegacyMigration(db, sources).run()
            assert stats.get("errors"), stats
            assert not db.is_migrated()
            assert db.get_all_pokemon() == before
            assert not db.execute(
                "SELECT 1 FROM metadata WHERE key = ?",
                (runner.collection_row_key(0, pikachu),),
            ).fetchone()
            assert {
                key: path.read_bytes() for key, path in sources.items()
            } == original_bytes
    finally:
        db.close()
