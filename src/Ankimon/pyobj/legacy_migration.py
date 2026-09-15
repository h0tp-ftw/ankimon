"""Shared, Qt-free legacy JSON migration used by the database and upgrade dialog."""

import json
import uuid
from collections import Counter
from pathlib import Path

from .database_manager import (
    aggregate_legacy_items,
    canonical_pokemon_name,
    find_matching_captured,
    is_valid_individual_id,
    legacy_species_id,
    normalize_legacy_item,
)


class _Cancelled(Exception):
    pass


_CHECKPOINT_VERSION = 2
_LEGACY_INDIVIDUAL_ID = "_legacy_individual_id"


class LegacyMigration:
    """Import each source durably; mark completion only after read-back checks.

    Sources are never changed here. Successfully committed records survive a
    later failure, and retries reuse their identities instead of making copies.
    The caller owns UI updates and archiving after a successful result.
    """

    def __init__(self, db, paths, progress=None, cancelled=None):
        self.db = db
        self.paths = {
            key: Path(value) if value else None for key, value in paths.items()
        }
        self.progress = progress
        self.cancelled = cancelled or (lambda: False)
        self.stats = dict.fromkeys(
            ("pokemon", "main", "items", "badges", "team", "history", "userdata"), 0
        )
        self.expected_pokemon = {}
        self.collection = []
        self.main_candidate = None
        self.duplicate_ids = set()
        self.percent = 0

    def report(self, percent, message):
        self.check_cancelled()
        self.percent = percent
        self.db._log("info", message)
        if self.progress:
            self.progress(percent, message)
        self.check_cancelled()

    def check_cancelled(self):
        if self.cancelled():
            raise _Cancelled()

    def error(self, message):
        self.stats.setdefault("errors", []).append(message)
        self.db._log("error", message)
        if self.progress:
            self.progress(self.percent, message)
        self.check_cancelled()

    def step(self, key, percent, label, action):
        path = self.paths.get(key)
        if path is None or not path.is_file():
            return
        self.report(percent, label)
        try:
            with path.open(encoding="utf-8") as source:
                data = json.load(source)
            action(data)
        except _Cancelled:
            raise
        except Exception as exc:
            # save_pokemon commits each record. This only drops the current
            # uncommitted batch (e.g. items), never an earlier source's records.
            self.db._get_connection().rollback()
            self.error(f"{path.name}: {exc}")

    @staticmethod
    def require_list(data):
        if not isinstance(data, list):
            raise ValueError(f"expected a JSON list, found {type(data).__name__}")
        return data

    @staticmethod
    def generated_id(source, index, record):
        # Stable across retries, including identical twins and duplicated IDs.
        # Source contents, not a machine-specific file path, define the identity.
        payload = json.dumps(record, sort_keys=True, ensure_ascii=True)
        return str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"ankimon-legacy:{source}:{index}:{payload}")
        )

    @staticmethod
    def mapping_candidate(original, individual_id):
        """Keep legacy matching fields separate from mutable live state."""
        legacy_id = original.get("individual_id")
        return {
            "individual_id": individual_id,
            _LEGACY_INDIVIDUAL_ID: (
                legacy_id if is_valid_individual_id(legacy_id) else None
            ),
            "name": canonical_pokemon_name(original.get("name")),
            "level": original.get("level"),
            "id": legacy_species_id(original),
            "iv": original.get("iv"),
        }

    def migrate_collection(self, data, preserve_existing=False):
        entries = self.require_list(data)
        self.stats["pokemon_expected"] = len(entries)
        self.stats["pokemon_failed"] = 0
        reusable = [
            p
            for p in self.db.get_all_pokemon()
            if isinstance(p, dict) and is_valid_individual_id(p.get("individual_id"))
        ]
        # An id-less entry must not claim a row explicitly referenced by a later
        # entry in this same save, even when the two Pokemon look identical.
        reserved = {
            p["individual_id"]
            for p in entries
            if isinstance(p, dict) and is_valid_individual_id(p.get("individual_id"))
        }
        counts = Counter(
            p.get("individual_id")
            for p in entries
            if isinstance(p, dict) and is_valid_individual_id(p.get("individual_id"))
        )
        self.duplicate_ids = {
            individual_id for individual_id, count in counts.items() if count > 1
        }
        generated = {
            index: self.generated_id("collection", index, p)
            for index, p in enumerate(entries)
            if isinstance(p, dict)
        }
        reserved.update(generated.values())
        used = set()
        for index, original in enumerate(entries):
            self.check_cancelled()
            if not isinstance(original, dict):
                self.stats["pokemon_failed"] += 1
                self.error(
                    f"mypokemon.json: entry {index + 1} is not a Pokemon object; failed to import"
                )
                continue
            pokemon = dict(original)
            old_id = pokemon.get("individual_id")
            if not is_valid_individual_id(old_id) or old_id in used:
                # A later identical twin may have committed while this entry
                # failed. Never steal that twin's deterministic identity.
                own_id = generated[index]
                candidates = [
                    p
                    for p in reusable
                    if p["individual_id"] not in used
                    and p["individual_id"] not in reserved
                ]
                match = next(
                    (p for p in reusable if p["individual_id"] == own_id), None
                )
                if match is None:
                    match = find_matching_captured(pokemon, candidates)
                pokemon["individual_id"] = match["individual_id"] if match else own_id
            individual_id = pokemon["individual_id"]
            if individual_id in used:
                self.stats["pokemon_failed"] += 1
                self.error(
                    f"mypokemon.json: entry {index + 1} failed: identity already used"
                )
                continue
            used.add(individual_id)
            try:
                existing = self.db.get_pokemon(individual_id)
                if preserve_existing and isinstance(existing, dict):
                    # An old Phase-1 marker may accompany a partial collection.
                    # Restore missing rows without reverting survivors' progress.
                    pokemon = existing
                elif not self.db.save_pokemon(pokemon):
                    raise ValueError("save_pokemon returned False")
                if self.db.get_pokemon(individual_id) != pokemon:
                    raise ValueError("saved Pokemon did not pass the read-back check")
                self.collection.append(self.mapping_candidate(original, individual_id))
                self.expected_pokemon[individual_id] = pokemon
                self.stats["pokemon"] += 1
            except Exception as exc:
                self.db._get_connection().rollback()
                self.stats["pokemon_failed"] += 1
                self.error(f"mypokemon.json: entry {index + 1} failed: {exc}")
            if index != len(entries) - 1 and index % 20 == 0:
                self.report(
                    5 + int(45 * (index + 1) / len(entries)),
                    f"Migrating Pokemon {index + 1}/{len(entries)}...",
                )

    def resolve_member(self, member, candidates, *, team=False):
        old_id = member.get("individual_id")
        exact = next((p for p in candidates if p.get("individual_id") == old_id), None)
        if exact and (
            old_id not in self.duplicate_ids
            or not member.get("name")
            or find_matching_captured(member, [exact])
        ):
            return exact
        if not team and is_valid_individual_id(old_id) and exact is None:
            # A distinct explicit identity denotes a separate captured Pokemon,
            # even when its species, level and IVs happen to match another one.
            return None
        mapped = [
            p
            for p in candidates
            if is_valid_individual_id(old_id) and p.get(_LEGACY_INDIVIDUAL_ID) == old_id
        ]
        if mapped:
            match = find_matching_captured(member, mapped)
            if match is not None:
                return match
            if not member.get("name"):
                return mapped[0]
        # Match the full legacy identity first: duplicate IDs may have been
        # reassigned during collection migration (including across a retry).
        match = find_matching_captured(member, candidates)
        if match is None and is_valid_individual_id(old_id):
            match = next(
                (p for p in candidates if p.get("individual_id") == old_id), None
            )
        if match is None and team and not member.get("iv"):
            # Some legacy team entries have no IVs and use species_id.
            def identity(p):
                return (
                    canonical_pokemon_name(p.get("name")),
                    str(p.get("level", "")),
                    legacy_species_id(p),
                )

            match = next(
                (p for p in candidates if identity(p) == identity(member)), None
            )
        return match

    def captured_candidates(self):
        candidates = list(self.collection)
        known = {p["individual_id"] for p in candidates}
        if (
            self.main_candidate is not None
            and self.main_candidate["individual_id"] not in known
        ):
            candidates.append(self.main_candidate)
            known.add(self.main_candidate["individual_id"])
        candidates.extend(
            p
            for p in self.db.get_all_pokemon()
            if isinstance(p, dict)
            and is_valid_individual_id(p.get("individual_id"))
            and p["individual_id"] not in known
        )
        return candidates

    def migrate_main(self, data):
        if not data:
            return
        member = data[0] if isinstance(data, list) else data
        if not isinstance(member, dict):
            raise ValueError("expected a main Pokemon object")
        original = dict(member)
        member = dict(original)
        candidates = self.captured_candidates()
        match = self.resolve_member(member, candidates)
        if match:
            member["individual_id"] = match["individual_id"]
        elif not is_valid_individual_id(member.get("individual_id")):
            member["individual_id"] = self.generated_id("main", 0, member)
        if (
            not self.db.save_main_pokemon(member)
            or self.db.get_main_pokemon() != member
        ):
            raise ValueError("main Pokemon failed the save/read-back check")
        self.main_candidate = self.mapping_candidate(original, member["individual_id"])
        self.expected_pokemon[member["individual_id"]] = member
        self.stats["main"] = 1

    def migrate_items(self, data):
        entries = self.require_list(data)
        totals = aggregate_legacy_items(entries)
        skipped = sum(normalize_legacy_item(entry) is None for entry in entries)
        if skipped:
            self.report(58, f"Skipped {skipped} unreadable item entries")
        for name, (quantity, extra) in totals.items():
            self.check_cancelled()
            if not self.db.add_item(name, quantity, extra_data=extra, commit=False):
                raise ValueError(f"failed to save item {name}")
            saved = self.db.get_item(name)
            if not saved or saved["quantity"] != quantity:
                message = (
                    f"items: {name} expected quantity {quantity}; saved stack differs"
                )
                self.stats.setdefault("integrity_issues", []).append(message)
                raise ValueError(message)
        # A later INSERT OR REPLACE can displace a stack that already passed
        # its immediate check, so validate the entire expected inventory only
        # after every write has run and before committing the batch.
        for name, (quantity, _extra) in totals.items():
            saved = self.db.get_item(name)
            if not saved or saved["quantity"] != quantity:
                message = (
                    f"items: {name} expected quantity {quantity}; saved stack differs"
                )
                self.stats.setdefault("integrity_issues", []).append(message)
                raise ValueError(message)
        self.db._get_connection().commit()
        self.stats["items"] = len(totals)

    def migrate_badges(self, data):
        for badge in self.require_list(data):
            self.check_cancelled()
            if isinstance(badge, (int, str)):
                badge_id, record = str(badge), {"achieved": True}
            elif isinstance(badge, dict):
                badge_id = str(badge.get("id", badge.get("badge_id", "")))
                record = dict(badge, achieved=True)
            else:
                raise ValueError("unreadable badge entry")
            if not badge_id or not self.db.save_badge(badge_id, record):
                raise ValueError(f"failed to save badge {badge_id}")
            saved = self.db.get_badge(badge_id)
            if not saved or not saved["achieved"]:
                raise ValueError(f"badge {badge_id} failed the read-back check")
            self.stats["badges"] += 1

    def migrate_team(self, data):
        conn = self.db._get_connection()
        if conn.execute(
            "SELECT value FROM metadata WHERE key = 'migration_verified_team'"
        ).fetchone():
            # A later source may have failed after the team was committed.
            # Preserve subsequent team changes and member level-ups on Retry.
            self.stats["team"] = len(self.db.get_team())
            return
        available = self.captured_candidates()
        team = []
        for index, member in enumerate(self.require_list(data)):
            self.check_cancelled()
            if not isinstance(member, dict):
                raise ValueError(f"team member {index + 1} is not an object")
            match = self.resolve_member(member, available, team=True)
            if match is None or not isinstance(
                self.db.get_pokemon(match["individual_id"]), dict
            ):
                raise ValueError(f"team member {index + 1} has no captured Pokemon")
            available.remove(match)
            team.append({"individual_id": match["individual_id"]})
        with conn:
            if not self.db.save_team(team, commit=False) or self.db.get_team() != team:
                raise ValueError("team failed the save/read-back check")
            conn.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('migration_verified_team', 'true')"
            )
        self.stats["team"] = len(team)

    def migrate_history(self, data):
        entries = self.require_list(data)
        existing = self.db.execute(
            "SELECT individual_id, data FROM pokemon_history"
        ).fetchall()
        reusable = []
        for row in existing:
            record = self.db._deobfuscate(row["data"])
            if isinstance(record, dict):
                reusable.append((row["individual_id"], record))
        generated = {
            index: self.generated_id("history", index, p)
            for index, p in enumerate(entries)
            if isinstance(p, dict)
        }
        reserved = set(generated.values()) | {
            p["individual_id"]
            for p in entries
            if isinstance(p, dict) and is_valid_individual_id(p.get("individual_id"))
        }
        used = set()
        for index, original in enumerate(entries):
            self.check_cancelled()
            if not isinstance(original, dict):
                raise ValueError(f"history entry {index + 1} is not an object")
            record = dict(original)
            individual_id = record.get("individual_id")
            if not is_valid_individual_id(individual_id) or individual_id in used:
                individual_id = generated[index]
                if not any(row_id == individual_id for row_id, _ in reusable):
                    # The old migration generated SQL IDs without storing them
                    # inside the JSON. Reuse those committed rows one-for-one.
                    payload = {k: v for k, v in record.items() if k != "individual_id"}
                    match = next(
                        (
                            row_id
                            for row_id, saved in reusable
                            if row_id not in used
                            and row_id not in reserved
                            and {k: v for k, v in saved.items() if k != "individual_id"}
                            == payload
                        ),
                        None,
                    )
                    if match:
                        individual_id = match
                record["individual_id"] = individual_id
            if individual_id in used:
                raise ValueError(
                    f"history entry {index + 1} has an identity already used"
                )
            used.add(individual_id)
            row = self.db.execute(
                "SELECT data FROM pokemon_history WHERE individual_id = ?",
                (individual_id,),
            ).fetchone()
            if row is None:
                if not self.db.add_to_history(record):
                    raise ValueError(f"failed to save history entry {index + 1}")
                row = self.db.execute(
                    "SELECT data FROM pokemon_history WHERE individual_id = ?",
                    (individual_id,),
                ).fetchone()
            saved = self.db._deobfuscate(row["data"]) if row else None
            if isinstance(saved, dict):
                saved = dict(saved, individual_id=individual_id)
            if saved != record:
                raise ValueError(
                    f"history entry {index + 1} failed the read-back check"
                )
            self.stats["history"] += 1
            if index % 50 == 0 or index == len(entries) - 1:
                self.report(
                    71 + int(20 * (index + 1) / len(entries)),
                    f"Migrating history {index + 1}/{len(entries)}...",
                )

    def migrate_userdata(self, data):
        if isinstance(data, list) and all(isinstance(entry, dict) for entry in data):
            data = {key: value for entry in data for key, value in entry.items()}
        if not isinstance(data, dict):
            raise ValueError("expected a settings object or list of objects")
        for key, value in data.items():
            self.check_cancelled()
            if not self.db.set_user_data(key, value):
                raise ValueError(f"failed to save setting {key}")
            self.stats["userdata"] += 1

    def migrate_rate(self, data):
        if not isinstance(data, dict):
            raise ValueError("expected a rating settings object")
        if data.get("rate_this") in (True, "true"):
            if not self.db.set_user_data("rate_this", True):
                raise ValueError("failed to save rating preference")

    @staticmethod
    def validated_mapping_candidate(record):
        if not isinstance(record, dict):
            raise ValueError("migration identity checkpoint contains a non-object")
        individual_id = record.get("individual_id")
        if not is_valid_individual_id(individual_id):
            raise ValueError("migration identity checkpoint has an invalid assigned ID")
        legacy_id = record.get(_LEGACY_INDIVIDUAL_ID)
        if legacy_id is not None and not is_valid_individual_id(legacy_id):
            raise ValueError("migration identity checkpoint has an invalid legacy ID")
        return {
            "individual_id": individual_id,
            _LEGACY_INDIVIDUAL_ID: legacy_id,
            "name": canonical_pokemon_name(record.get("name")),
            "level": record.get("level"),
            "id": legacy_species_id(record),
            "iv": record.get("iv"),
        }

    def save_checkpoint(self, key, payload):
        conn = self.db._get_connection()
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
            (key, json.dumps(payload, sort_keys=True)),
        )
        conn.commit()

    def save_collection_checkpoint(self):
        self.save_checkpoint(
            "migration_verified_collection",
            {
                "version": _CHECKPOINT_VERSION,
                "duplicate_ids": sorted(self.duplicate_ids),
                "records": self.collection,
            },
        )

    def restore_collection_checkpoint(self, value):
        payload = json.loads(value)
        if (
            not isinstance(payload, dict)
            or payload.get("version") != _CHECKPOINT_VERSION
        ):
            raise ValueError(
                "migration collection checkpoint has an unsupported format"
            )
        duplicate_ids = payload.get("duplicate_ids")
        records = payload.get("records")
        if not isinstance(duplicate_ids, list) or not all(
            is_valid_individual_id(value) for value in duplicate_ids
        ):
            raise ValueError(
                "migration collection checkpoint has invalid duplicate IDs"
            )
        if not isinstance(records, list):
            raise ValueError("migration collection checkpoint has invalid records")
        candidates = [self.validated_mapping_candidate(record) for record in records]
        assigned = [candidate["individual_id"] for candidate in candidates]
        if len(assigned) != len(set(assigned)):
            raise ValueError("migration collection checkpoint reuses an assigned ID")
        self.duplicate_ids = set(duplicate_ids)
        self.collection = candidates

    def save_main_checkpoint(self):
        self.save_checkpoint(
            "migration_verified_main",
            {"version": _CHECKPOINT_VERSION, "record": self.main_candidate},
        )

    def restore_main_checkpoint(self, value):
        payload = json.loads(value)
        if (
            not isinstance(payload, dict)
            or payload.get("version") != _CHECKPOINT_VERSION
        ):
            raise ValueError("migration main checkpoint has an unsupported format")
        record = payload.get("record")
        self.main_candidate = (
            None if record is None else self.validated_mapping_candidate(record)
        )

    def preserve_existing_main(self, data):
        """Checkpoint a legacy main identity without replacing live progress."""
        if not data:
            return
        member = data[0] if isinstance(data, list) else data
        if not isinstance(member, dict):
            raise ValueError("expected a main Pokemon object")
        candidates = self.captured_candidates()
        match = self.resolve_member(member, candidates)
        if match is None and is_valid_individual_id(member.get("individual_id")):
            saved = self.db.get_pokemon(member["individual_id"])
            if isinstance(saved, dict):
                match = saved
        if match is None:
            current = self.db.get_main_pokemon()
            if isinstance(current, dict):
                match = current
        if match is None or not isinstance(
            self.db.get_pokemon(match["individual_id"]), dict
        ):
            raise ValueError("verified main Pokemon has no captured Pokemon")
        self.main_candidate = self.mapping_candidate(member, match["individual_id"])
        self.stats["main"] = 1

    def verify_collection(self):
        missing = [
            individual_id
            for individual_id, expected in self.expected_pokemon.items()
            if self.db.get_pokemon(individual_id) != expected
        ]
        if missing:
            message = f"pokemon: {len(missing)} saved entries failed final verification"
            self.stats.setdefault("integrity_issues", []).append(message)
            self.db.execute(
                "DELETE FROM metadata WHERE key IN "
                "('migrated', 'migration_verified_collection', 'migration_verified_main')"
            )
            self.db._get_connection().commit()
            self.error(message)

    def run(self):
        if self.db.is_migrated():
            return self.stats
        conn = self.db._get_connection()
        if conn.in_transaction or conn._disable_commit:
            self.error("Migration requires a connection with no pending transaction")
            return self.stats
        phase1_done = self.db.is_migrated_phase1()
        try:
            collection_checkpoint = conn.execute(
                "SELECT value FROM metadata WHERE key = 'migration_verified_collection'"
            ).fetchone()
            if collection_checkpoint:
                self.restore_collection_checkpoint(collection_checkpoint["value"])
                self.stats["pokemon"] = self.db.get_pokemon_count()
                self.report(
                    50,
                    "Previously verified collection preserved; continuing remaining migration.",
                )
            else:
                collection_errors = len(self.stats.get("errors", ()))
                self.step(
                    "mypokemon",
                    5,
                    "Loading Pokemon collection...",
                    lambda data: self.migrate_collection(
                        data, preserve_existing=phase1_done
                    ),
                )
                self.verify_collection()
                if len(self.stats.get("errors", ())) == collection_errors:
                    self.save_collection_checkpoint()
                self.report(
                    50,
                    f"{self.stats['pokemon']} Pokemon verified; "
                    f"{self.stats['pokemon_failed']} failed",
                )

            main_checkpoint = conn.execute(
                "SELECT value FROM metadata WHERE key = 'migration_verified_main'"
            ).fetchone()
            if main_checkpoint:
                self.restore_main_checkpoint(main_checkpoint["value"])
                if self.main_candidate is not None and isinstance(
                    self.db.get_pokemon(self.main_candidate["individual_id"]), dict
                ):
                    self.stats["main"] = 1
                self.report(55, "Previously verified main Pokemon preserved.")
            else:
                main_errors = len(self.stats.get("errors", ()))
                self.step(
                    "mainpokemon",
                    52,
                    "Migrating main Pokemon...",
                    self.preserve_existing_main if phase1_done else self.migrate_main,
                )
                self.verify_collection()
                if len(self.stats.get("errors", ())) == main_errors:
                    self.save_main_checkpoint()

            if not phase1_done:
                self.step("items", 58, "Migrating items...", self.migrate_items)
                self.step("badges", 61, "Migrating badges...", self.migrate_badges)
            self.verify_collection()
            if self.stats.get("errors"):
                # Old versions could mark a partial Phase 1 as completed. Keep
                # independently verified source checkpoints, but never retain
                # the broader marker while any source still failed.
                conn.execute("DELETE FROM metadata WHERE key = 'migrated'")
                conn.commit()
                return self.stats
            self.report(65, "Collection migration verified.")
            conn.execute("INSERT OR REPLACE INTO metadata VALUES ('migrated', 'true')")
            conn.commit()

            self.step("team", 66, "Migrating team...", self.migrate_team)
            self.step(
                "history", 71, "Migrating release history...", self.migrate_history
            )
            self.step("data", 92, "Migrating user settings...", self.migrate_userdata)
            self.step("rate", 94, "Migrating rating preference...", self.migrate_rate)
            self.verify_collection()
            if self.stats.get("errors"):
                return self.stats
            self.report(95, "All migrated data verified.")
            conn.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('migrated_phase2', 'true')"
            )
            conn.commit()
        except _Cancelled:
            conn.rollback()
            self.stats["cancelled"] = True
        except Exception as exc:
            conn.rollback()
            self.error(f"Migration incomplete: {exc}")
        return self.stats
