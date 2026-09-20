# Legacy migration retry safety

`LegacyMigration` is the shared Qt-free runner for the database entry point and
upgrade dialog. Verified work survives a later source failure or cancellation.
Retry may import pending records, but must preserve subsequent gameplay changes
and releases. Tests and harness tooling stay outside the shipped `src/Ankimon/`.

## Identity ownership

Collection and main mappings store an assigned individual ID separately from the
original legacy ID, canonical name, normalized species ID, level, and IVs. These
immutable matching fields remain valid after the live Pokemon changes. Generated
IDs depend on source contents and entry position, preserving identical twins and
remapped duplicate IDs across retries.

Fresh main imports and pending collection reconciliation use `resolve_member()`:
an explicit main ID denotes a separate Pokemon unless the collection claims that
ID; an ID-less main can match either an ID-less or explicit collection entry by
species, name, level, and IVs. Missing IDs never constitute an exact ID match.
On Retry, the main checkpoint's original ID is used for this decision, not its
generated assigned ID.

A checkpointed main can own one compatible pending collection entry. Multiple
compatible entries require explicit recovery; Retry does not guess. IDs already
reserved by other collection entries or checkpoints cannot be claimed by a twin.
An explicit collection alias mapped to a generated main ID remains available for
team resolution. Resolving a team slot consumes all aliases for its assigned ID,
and the live row must exist before the team can be saved.

## Permitted retry writes

- A committed collection entry is skipped, even if its Pokemon has since changed
  or been released. Pending entries cannot reuse its assigned identity.
- A pending collection entry mapped to a checkpointed main preserves the live
  row. If that row is missing, migration stops instead of recreating it.
- A pending main may replace an unchanged collection snapshot it owns. Later live
  changes take precedence; a missing owned row requires explicit recovery.
- A verified main, inventory, or team source is skipped. Item consumption and team
  changes after a partial import survive Retry.
- An older Phase-1 marker without row snapshots permits conservative continuation:
  existing identities can be reconciled without overwriting live data, but missing
  or unresolvable Pokemon require recovery. The marker remains present on failure.

## Checkpoints and verification

Each collection row commits atomically with its identity mapping and imported
snapshot. The main row and its mapping also commit together. A complete collection
checkpoint is written only when every entry has succeeded. Unsupported checkpoints,
invalid assigned IDs, and null collection snapshots fail closed.

The runner fingerprints each parsed source before its first possible write and
validates pinned fingerprints before trusting checkpoints and before completion.
Changed or removed sources, including formatting-only edits, require explicit
reconciliation. Older ownership checkpoints without fingerprints cannot certify
the current source. JSON that failed parsing before any import can be repaired.

Read-back checks verify writes immediately and at source/completion boundaries.
Final Pokemon verification failures persist as expected rows in metadata; Retry
cannot clear them by trusting earlier checkpoints. Cancellation rolls back the
current transaction while keeping previously verified work.

The Phase-1 marker follows successful collection, main, inventory, and badges.
Full completion follows team, history, settings, and final verification. The dialog
checks fingerprints again after its last progress callback and archives only on
complete success. The runner never modifies source files.

## Inventory integrity

Ordinary `AnkimonDB.save_item()` writes and migration share collision-safe item
allocation. New uncatalogued items receive negative IDs; catalogue IDs remain
positive. A conflicting occupant moves to a negative ID in the same transaction.
Existing uncatalogued IDs remain stable during ordinary updates. A writer lock
protects allocation, and a savepoint makes relocation and upsert atomic within a
caller's transaction.

Inventory migration verifies every normalized stack after all writes, then commits
the entire batch with its checkpoint. A missing or mismatched stack rolls back the
batch without undoing previously verified Pokemon sources.

## Recovery and validation

Completed migrations are not reopened automatically. Changed sources, ambiguous
ownership, missing pending identities, and unresolved verification failures require
inspection of the original files and current database. Deleting checkpoints loses
the evidence protecting live progress. See the [recovery guide](../../legacy_migration_recovery.md).

`tests/test_migration_recovery.py` exercises real `AnkimonDB` and disposable SQLite
files, including independent collection/main ID formats, team aliases, database
reopening, level-ups, releases, twins, rollback, and source preservation.
`harness/checks/probe_real_migration.py` exercises the real Qt dialog and archive
boundary; `python3 harness/check.py` supplies the zero-dependency Tier-1 gate.
