# Legacy Migration Retry Safety Design

## Context

The shared `LegacyMigration` runner commits some legacy sources before the
entire migration finishes. That is intentional: large saves should retain
verified work after a later source fails. The current checkpoints do not carry
enough identity information, however, and the collection checkpoint is written
only after items and badges succeed. A user who resumes gameplay after a
cancelled or failed dialog can therefore have live Pokemon changes reverted on
Retry, or have an as-yet-unmigrated team become impossible to match.

Inventory migration has a separate integrity gap. `items.id` is an integer
primary key, uncatalogued items currently receive SQLite's next positive row
ID, and catalogued items use their positive PokeAPI IDs. `INSERT OR REPLACE`
can consequently delete a previously verified uncatalogued stack. Per-write
read-back checks cannot detect a stack displaced by a later write.

## Required Outcomes

1. An uncatalogued item and a catalogued item with a colliding positive ID both
   survive migration with their expected quantities.
2. Inventory is verified as a complete batch after all writes and before
   commit. Any missing or mismatched stack rolls back the batch and leaves
   Phase 1 incomplete.
3. A team member using `species_id` matches the corresponding captured record
   using `id` even when IV data is present. Species aliases are normalized
   before the IV-aware comparison; matching must not fall back to a weaker
   identity.
4. A verified legacy collection retains a durable mapping from every legacy
   record to its assigned individual ID, including reused IDs and remapped
   duplicate IDs.
5. If migration is cancelled before the team is saved, Retry uses the durable
   mapping rather than mutable live Pokemon fields. A level change between
   attempts must not break team migration.
6. Collection and main-Pokemon checkpoints are committed immediately after
   their own save/read-back verification, independently of inventory and
   badges. A later item failure followed by live level changes or releases must
   not let Retry overwrite or recreate those Pokemon.
7. Existing successful migration, duplicate-ID, main-Pokemon, team, history,
   cancellation, and archiving behavior remains covered and passing.

## Design

### Collision-safe item identities

`AnkimonDB.save_item()` will assign newly encountered uncatalogued items
negative IDs, below the smallest existing negative ID. PokeAPI catalogue IDs
remain positive. Before inserting a catalogued item, the method will detect a
different row already occupying that catalogue ID and relocate the occupant to
a negative ID within the same SQLite transaction. This also protects databases
that already contain an uncatalogued item with an old automatically allocated
positive ID.

The existing item-name uniqueness contract and metadata lookup remain intact.
An existing uncatalogued item keeps its current ID during ordinary updates;
when that same item later becomes catalogued, it can move to the catalogue ID
without deleting another stack.

### Whole-batch inventory verification

`LegacyMigration.migrate_items()` will retain the immediate checks that localize
a failed write, then perform a second pass over every expected normalized item
name and quantity after all writes. It will commit only when the whole set is
present and correct. Any mismatch records an integrity issue, raises through
the existing step boundary, and rolls back every uncommitted item write.

### Normalized Pokemon species matching

A shared legacy species-key helper will read `species_id` or `id` and normalize
their values for comparison. `find_matching_captured()` and the reduced team
identity comparison will use that helper before comparing name, level, and IVs.
The IV-aware matcher remains the primary path, so two same-species Pokemon with
different IVs cannot be accidentally interchanged.

### Durable source identity checkpoints

The `migration_verified_collection` metadata value will become a versioned JSON
payload containing duplicate legacy IDs plus an ordered list of compact mapping
records. Each mapping record stores:

- the assigned individual ID;
- the original valid legacy individual ID, if one existed;
- canonical name, normalized species ID, level, and IVs.

Those fields reproduce the runner's existing strong matching identity without
copying an entire save into metadata. The assigned ID is recorded only after
the corresponding captured row passes read-back verification. A complete
collection mapping is checkpointed only when every collection entry succeeds.

A separate `migration_verified_main` checkpoint stores the equivalent compact
mapping for the verified main Pokemon, including a main Pokemon outside the
collection. This makes an id-less main record available to pending team
matching after live state changes.

On Retry, a valid checkpoint causes that source to be skipped regardless of the
broader Phase-1 marker. The compact legacy snapshots are restored as matching
candidates, while the runner reads the assigned IDs from the live database when
saving the team. Missing live rows are never recreated: a released Pokemon can
only make a legacy team reference fail explicitly, not resurrect the row.

Collection and main checkpoints are committed directly after their respective
final verification. Items and badges then run, and the existing `migrated`
Phase-1 marker is written only when all Phase-1 sources have succeeded. The
`migrated_phase2` marker and JSON archiving remain gated on complete success.

## Failure Handling

- Invalid or incomplete checkpoint payloads fail closed with a migration error;
  the runner does not guess identities or replay legacy records over live state.
- A collection entry that fails to save or verify prevents creation of the
  collection checkpoint.
- A main-Pokemon failure leaves a valid collection checkpoint intact and Retry
  attempts only the unverified main source.
- Inventory mismatches roll back the item transaction and leave both completion
  markers unset while preserving earlier source checkpoints.
- Team resolution always verifies that the mapped assigned ID still references
  a captured Pokemon before replacing the team.

## Verification

Tests will use the real `AnkimonDB`, disposable SQLite files, and the shared
migration runner. They will first fail against `a31c0b67` and then cover:

- uncatalogued/catalogued item-ID collision survival;
- deliberate loss of an earlier item during a later write, proving final batch
  verification and rollback;
- cancellation immediately before team migration, live level-up, then Retry;
- `species_id` versus `id` with matching non-empty IV dictionaries;
- item failure, live level-up and release, then Retry without reversion or
  resurrection.

After targeted migration tests pass, run the full Python suite, the Tier-1
`harness/check.py` gate, and the real migration probe required by the branch.
