# Legacy migration recovery (#856 / #861)

Retry preserves imported identities, later gameplay changes, and intentional
releases. It is safe to retry the **same source files** after a transient write
failure or cancellation.

Once a source has been read for import, its SHA-256 fingerprint is stored before
any records can be written. Changing or removing that source makes migration
stop with an explicit reconciliation error. This includes removing a malformed
entry from a partially imported collection, replacing a previously verified
collection, and formatting-only edits. Nothing is automatically replayed or
archived in this case. Restore the exact original source or seek support to
reconcile the legacy save with the current database. **Do not delete migration
checkpoints:** they protect progress and released Pokémon. A file that failed
JSON parsing before any records were processed can still be corrected and retried.

Older incomplete migrations with row/source checkpoints but no fingerprints also
require reconciliation: those checkpoints establish ownership, but cannot prove
which source file was read. The older Phase-1-only marker retains its existing
conservative recovery behavior for missing Pokémon.

A Pokémon that fails final verification is recorded durably by identity and
expected contents. Reopening the database and clicking Retry does not clear this
failure or restore the Pokémon. Retry succeeds only once the affected record has
been repaired, or the discrepancy has been explicitly reconciled with support.
This is separate from releasing a Pokémon after a successfully verified import;
that release remains respected.

If the main Pokémon was verified but its collection entry is still pending,
Retry uses the saved legacy main identity even after a level-up. A missing main
row or an ambiguous match between identical pending entries requires an explicit
recovery decision; Retry neither creates a replacement nor archives the sources.
A null collection-row snapshot is not valid verification evidence. This does not
change release handling for entries with valid committed collection checkpoints.

The dialog rechecks the fingerprints after its last progress event before
archiving. If the source changed, it preserves the files and leaves migration
incomplete.

These safeguards do not automatically repair an already-completed, incorrect
migration. The original #856 reporter's save was unavailable; regression tests
using disposable saves do not establish recovery of that historical database.
