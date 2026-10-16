"""Shared, Qt-free legacy JSON migration used by the database and upgrade dialog."""

import hashlib
import json
import uuid
from collections import Counter
from contextlib import contextmanager
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
            (
                "pokemon",
                "pokemon_expected",
                "pokemon_failed",
                "main",
                "items",
                "badges",
                "team",
                "history",
                "userdata",
            ),
            0,
        )
        self.expected_pokemon = {}
        self.collection = []
        self.main_candidate = None
        self.duplicate_ids = set()
        self.percent = 0
        self.collection_snapshots = {}

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
            self.pin_source(key, None)
            return
        self.report(percent, label)
        try:
            contents = path.read_bytes()
            data = json.loads(contents.decode("utf-8"))
            # Bind provenance before the first possible write, including partial
            # imports. Invalid JSON can still be repaired if nothing was read.
            self.pin_source(key, hashlib.sha256(contents).hexdigest())
            action(data)
        except _Cancelled:
            raise
        except Exception as exc:
            # save_pokemon commits each record. This only drops the current
            # uncommitted batch (e.g. items), never an earlier source's records.
            self.db._get_connection().rollback()
            self.error(f"{path.name}: {exc}")

    def pin_source(self, key, fingerprint):
        checkpoint = "migration_source:" + key
        saved = self.db.execute(
            "SELECT value FROM metadata WHERE key = ?", (checkpoint,)
        ).fetchone()
        if saved and json.loads(saved["value"]) != fingerprint:
            raise ValueError(f"{key} source changed; explicit reconciliation required")
        if not saved:
            self.save_checkpoint(checkpoint, fingerprint)

    def validate_sources(self):
        """Reject changed or removed inputs before trusting any saved identity."""
        pinned = {}
        for row in self.db.execute(
            "SELECT key, value FROM metadata WHERE key LIKE 'migration_source:%'"
        ).fetchall():
            key = row["key"].split(":", 1)[1]
            pinned[key] = json.loads(row["value"])
            path = self.paths.get(key)
            current = (
                hashlib.sha256(path.read_bytes()).hexdigest()
                if path is not None and path.is_file()
                else None
            )
            if current != pinned[key]:
                raise ValueError(
                    f"{key} source changed or is missing; explicit reconciliation "
                    "required. Restore the original source or seek recovery support; "
                    "do not delete migration checkpoints."
                )
        # Older checkpoints have ownership evidence but no source fingerprint.
        # Adopting today's file would incorrectly certify a repaired/replaced save.
        for key, checkpoints in (
            (
                "mypokemon",
                ("migration_verified_collection", "migration_collection_row:%"),
            ),
            ("mainpokemon", ("migration_verified_main",)),
            ("items", ("migration_verified_items",)),
            ("team", ("migration_verified_team",)),
        ):
            if key not in pinned and any(
                self.db.execute(
                    "SELECT 1 FROM metadata WHERE key LIKE ?", (checkpoint,)
                ).fetchone()
                for checkpoint in checkpoints
            ):
                raise ValueError(
                    f"{key}: older checkpoint has no source fingerprint; "
                    "explicit reconciliation required"
                )

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

    @contextmanager
    def atomic_batch(self):
        """Keep legacy save helpers' internal commits inside our transaction."""
        conn = self.db._get_connection()
        previous = conn._disable_commit
        try:
            with conn:
                conn._disable_commit = True
                yield
        finally:
            conn._disable_commit = previous

    def collection_row_key(self, index, original):
        return "migration_collection_row:" + self.generated_id(
            "collection", index, original
        )

    def load_collection_rows(self):
        for row in self.db.execute(
            "SELECT value FROM metadata WHERE key LIKE 'migration_collection_row:%'"
        ).fetchall():
            payload = json.loads(row["value"])
            candidate = self.validated_mapping_candidate(payload["record"])
            if not isinstance(payload.get("snapshot"), dict):
                raise ValueError(
                    "collection row checkpoint has no Pokemon snapshot; "
                    "explicit recovery decision required"
                )
            self.collection_snapshots[candidate["individual_id"]] = payload["snapshot"]

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
        # Older imports may have assigned random IDs. Pending entries must not
        # claim an ID already durably assigned to another source entry.
        reserved.update(self.collection_snapshots)
        main_index = None
        if self.main_candidate is not None:
            main_id = self.main_candidate["individual_id"]
            main_source = dict(
                self.main_candidate,
                individual_id=self.main_candidate.get(_LEGACY_INDIVIDUAL_ID),
            )
            if main_id not in reserved:
                # Main may have committed after a collection write failed. Its
                # immutable alias, not its live level or presence, proves the
                # overlap. Check all pending entries before choosing an owner.
                # Use the same main-to-collection rule as a fresh import. The
                # assigned ID is ownership evidence, not the main's source ID;
                # an ID-less main can also own an explicit collection alias.
                matches = [
                    index
                    for index, original in enumerate(entries)
                    if isinstance(original, dict)
                    and self.resolve_member(main_source, [original]) is not None
                    and not self.db.execute(
                        "SELECT 1 FROM metadata WHERE key = ?",
                        (self.collection_row_key(index, original),),
                    ).fetchone()
                ]
                if len(matches) > 1:
                    raise ValueError(
                        "ambiguous legacy main identity among collection entries; "
                        "explicit recovery decision required"
                    )
                if matches:
                    main_index = matches[0]
            # Explicit main identities remain distinct without a collection
            # claim (as in resolve_member). Existing collection claims also
            # reserve this ID; no twin may claim it through live matching.
            reserved.add(main_id)
        used = set()
        for index, original in enumerate(entries):
            self.check_cancelled()
            if not isinstance(original, dict):
                self.stats["pokemon_failed"] += 1
                self.error(
                    f"mypokemon.json: entry {index + 1} is not a Pokemon object; failed to import"
                )
                continue
            row_key = self.collection_row_key(index, original)
            saved_row = self.db.execute(
                "SELECT value FROM metadata WHERE key = ?", (row_key,)
            ).fetchone()
            if saved_row:
                # The row and identity were committed together. A later release
                # or level-up must survive even a partial collection Retry.
                payload = json.loads(saved_row["value"])
                candidate = self.validated_mapping_candidate(payload["record"])
                individual_id = candidate["individual_id"]
                if individual_id in used:
                    raise ValueError("collection row checkpoint reuses an assigned ID")
                used.add(individual_id)
                self.collection.append(candidate)
                self.collection_snapshots[individual_id] = payload["snapshot"]
                self.stats["pokemon"] += 1
                continue
            pokemon = dict(original)
            old_id = pokemon.get("individual_id")
            if index == main_index:
                pokemon["individual_id"] = self.main_candidate["individual_id"]
            elif not is_valid_individual_id(old_id) or old_id in used:
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
                protected_main = (
                    self.main_candidate is not None
                    and self.main_candidate["individual_id"] == individual_id
                )
                if (
                    protected_main
                    and self.resolve_member(main_source, [original]) is None
                ):
                    # A shared database ID alone is not ownership evidence:
                    # older retries may have assigned it to a different source.
                    # Compare immutable source identities, never live gameplay.
                    raise ValueError(
                        "collection identity conflicts with checkpointed main; "
                        "explicit recovery decision required"
                    )
                if (preserve_existing or protected_main) and not isinstance(
                    existing, dict
                ):
                    raise ValueError(
                        "unresolved legacy identity; explicit recovery decision required "
                        "before restoring a missing Pokemon"
                    )
                candidate = self.mapping_candidate(original, individual_id)
                if preserve_existing or protected_main:
                    # Old Phase-1 markers and main checkpoints prove prior
                    # ownership, not permission to restore or replace live rows.
                    pokemon = existing
                with self.atomic_batch():
                    if not (preserve_existing or protected_main):
                        if not self.db.save_pokemon(pokemon):
                            raise ValueError("save_pokemon returned False")
                    if (
                        not isinstance(pokemon, dict)
                        or self.db.get_pokemon(individual_id) != pokemon
                    ):
                        raise ValueError(
                            "saved Pokemon did not pass the read-back check"
                        )
                    self.save_checkpoint(
                        row_key, {"record": candidate, "snapshot": pokemon}
                    )
                self.collection.append(candidate)
                self.collection_snapshots[individual_id] = pokemon
                if isinstance(pokemon, dict):
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
        if (
            is_valid_individual_id(old_id)
            and exact
            and (
                old_id not in self.duplicate_ids
                or not member.get("name")
                or find_matching_captured(member, [exact])
            )
        ):
            return exact
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
        if not team and is_valid_individual_id(old_id) and exact is None:
            # A proven alias can own a reassigned row. Without that evidence,
            # a distinct explicit identity denotes a separate captured Pokemon,
            # even when its species, level and IVs happen to match another one.
            return None
        # Match the full legacy identity first: duplicate IDs may have been
        # reassigned during collection migration (including across a retry).
        match = find_matching_captured(member, candidates)
        # An exact duplicate ID rejected above must not bypass the source-field
        # checks here, including when a team member has not yet been imported.
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
        # One physical row can legitimately have several legacy identities.
        # Keep every alias; team resolution consumes the assigned ID as a unit.
        if self.main_candidate is not None:
            candidates.append(self.main_candidate)
        candidates.extend(
            p
            for p in self.db.get_all_pokemon()
            if isinstance(p, dict) and is_valid_individual_id(p.get("individual_id"))
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
        individual_id = member["individual_id"]
        existing = self.db.get_pokemon(individual_id)
        if match is None and individual_id in self.duplicate_ids:
            # Collection sources reserve duplicate identities even when every
            # write fails. Wait for a collection mapping before letting main
            # claim an ID that may belong to a different source entry.
            raise ValueError(
                "unresolved duplicate main identity; retry after resolving "
                "collection failures or seek explicit recovery"
            )
        owned = any(p["individual_id"] == individual_id for p in self.collection)
        if owned and (
            individual_id not in self.collection_snapshots
            or existing != self.collection_snapshots[individual_id]
        ):
            # Main may replace only the unchanged collection snapshot we own.
            # No snapshot (an older checkpoint) also means live state wins.
            if not isinstance(existing, dict):
                raise ValueError(
                    "main Pokemon was removed; explicit recovery decision required"
                )
            member = existing
        with self.atomic_batch():
            if (
                not self.db.save_main_pokemon(member)
                or self.db.get_main_pokemon() != member
            ):
                raise ValueError("main Pokemon failed the save/read-back check")
            self.main_candidate = self.mapping_candidate(original, individual_id)
            self.save_main_checkpoint()
        self.expected_pokemon[member["individual_id"]] = member
        self.stats["main"] = 1

    def migrate_items(self, data):
        with self.atomic_batch():
            self._migrate_items(data)

    def _migrate_items(self, data):
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
        self.save_checkpoint("migration_verified_items", {"count": len(totals)})
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
            available = [
                p for p in available if p["individual_id"] != match["individual_id"]
            ]
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
        if match is None or not isinstance(
            self.db.get_pokemon(match["individual_id"]), dict
        ):
            raise ValueError(
                "unresolved main identity; explicit recovery decision required"
            )
        self.main_candidate = self.mapping_candidate(member, match["individual_id"])
        self.stats["main"] = 1

    def verify_collection(self):
        missing = [
            individual_id
            for individual_id, expected in self.expected_pokemon.items()
            if self.db.get_pokemon(individual_id) != expected
        ]
        if missing:
            message = (
                f"pokemon: {len(missing)} saved entries failed final verification: "
                + ", ".join(missing)
            )
            self.stats.setdefault("integrity_issues", []).append(message)
            with self.atomic_batch():
                self.save_checkpoint(
                    "migration_unresolved_pokemon",
                    {key: self.expected_pokemon[key] for key in missing},
                )
                self.db.execute(
                    "DELETE FROM metadata WHERE key IN "
                    "('migration_verified_collection', 'migration_verified_main')"
                )
            self.error(message)

    def verify_unresolved(self):
        row = self.db.execute(
            "SELECT value FROM metadata WHERE key = 'migration_unresolved_pokemon'"
        ).fetchone()
        if row:
            self.expected_pokemon.update(json.loads(row["value"]))
            self.verify_collection()
            if not self.stats.get("integrity_issues"):
                self.db.execute(
                    "DELETE FROM metadata WHERE key = 'migration_unresolved_pokemon'"
                )
                self.db._get_connection().commit()

    def run(self):
        if self.db.is_migrated():
            return self.stats
        conn = self.db._get_connection()
        if conn.in_transaction or conn._disable_commit:
            self.error("Migration requires a connection with no pending transaction")
            return self.stats
        phase1_done = self.db.is_migrated_phase1()
        try:
            self.validate_sources()
            self.verify_unresolved()
            if self.stats.get("integrity_issues"):
                return self.stats
            self.load_collection_rows()
            # Restore main ownership before any pending collection writes.
            main_checkpoint = conn.execute(
                "SELECT value FROM metadata WHERE key = 'migration_verified_main'"
            ).fetchone()
            if main_checkpoint:
                self.restore_main_checkpoint(main_checkpoint["value"])
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
                if self.stats.get("integrity_issues"):
                    return self.stats
                if len(self.stats.get("errors", ())) == collection_errors:
                    self.save_collection_checkpoint()
                self.report(
                    50,
                    f"{self.stats['pokemon']} Pokemon verified; "
                    f"{self.stats['pokemon_failed']} failed",
                )

            if main_checkpoint:
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
                if self.stats.get("integrity_issues"):
                    return self.stats
                if len(self.stats.get("errors", ())) == main_errors and (
                    phase1_done or self.main_candidate is None
                ):
                    # migrate_main commits its row and checkpoint together.
                    # Empty sources / old-marker continuation have no row write.
                    self.save_main_checkpoint()

            if not phase1_done:
                inventory_checkpoint = conn.execute(
                    "SELECT value FROM metadata WHERE key = 'migration_verified_items'"
                ).fetchone()
                if inventory_checkpoint:
                    self.stats["items"] = json.loads(inventory_checkpoint["value"])[
                        "count"
                    ]
                else:
                    self.step("items", 58, "Migrating items...", self.migrate_items)
                self.step("badges", 61, "Migrating badges...", self.migrate_badges)
            self.verify_collection()
            if self.stats.get("errors"):
                # Keep the old marker: it is evidence that these sources may
                # predate gameplay. Removing it would turn the next Retry into
                # a fresh import and resurrect unresolved/released Pokemon.
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
            self.validate_sources()
            self.verify_collection()
            if self.stats.get("errors"):
                return self.stats
            conn.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('migrated_phase2', 'true')"
            )
            conn.commit()
        except _Cancelled:
            conn.rollback()
            self.stats["cancelled"] = True
        except Exception as exc:
            conn.rollback()
            try:
                self.error(f"Migration incomplete: {exc}")
            except _Cancelled:
                self.stats["cancelled"] = True
        return self.stats
