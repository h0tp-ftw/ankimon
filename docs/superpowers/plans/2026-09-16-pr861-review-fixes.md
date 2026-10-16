# PR #861 migration integrity corrections

The review counterexamples define the intended behavior. Implement inline on the
existing PR branch, using real SQLite and the shipped dialog for regressions.

- [x] Reproduce overlapping collection/main writes, pending-main cancellation,
  ambiguous old-marker recovery, alias matching, absent sources, null species,
  error-report cancellation, and inventory replay in regression tests.
- [x] Commit each collection row with its assigned identity and original snapshot
  in one transaction. Retry consumes this provenance without replaying the row.
  Pending main reconciliation may replace only an unchanged imported snapshot;
  changed live data wins. Existing source checkpoints remain readable and protect
  their rows even at the opposite source boundary.
- [x] Keep old Phase-1 provenance across failures. Never insert a missing legacy
  row in this recovery mode; report unresolved identities and retain sources for
  an explicit recovery decision. Do not weaken level/IV matching.
- [x] Retain collection, main and live aliases; consume all aliases of an assigned
  ID together when resolving a team.
- [x] Initialize summary counters, fall back from null species_id, catch
  cancellation inside generic error handling, and checkpoint verified inventory
  in the same transaction as the batch.
- [x] Exercise the real Qt dialog probe, full pytest suite and Tier-1 harness;
  review transaction failure paths and update PR scope/validation text.

No runtime user saves or archived legacy sources are modified by these tests.
Completed migrations remain closed. Ambiguous historical saves need inspection;
Retry alone is not an explicit recovery decision.

Validation: 54 recovery tests passed; full suite 1,833 passed, 41 skipped and
9 subtests passed. All 13 Tier-1 checks and the expanded real-Qt migration probe
passed. CI Ruff lint/format checks passed. The full suite required execution
outside the sandbox for unrelated WebEngine and multimedia subprocesses.

Independent review additionally reproduced two provenance failures: clearing an
old Phase-1 marker during final read-back failure, and a pending idless entry
claiming a later entry's checkpointed random ID. Both have real database/dialog
regressions and independently verified fixes.

## Follow-up review of 0ae3b2f2

- [x] Reproduce repaired partial sources (ID-less, progressed, released), replaced
  and missing verified sources, and reopened-database verification failures using
  the shipped database/dialog rather than transcribed SQL.
- [x] Pin source bytes before writes; reject changed or removed sources before
  trusting checkpoints. Retain provenance and require explicit reconciliation for
  older checkpoints without fingerprints. Recheck at the final archive boundary.
- [x] Persist failed verification identities and expected records; recheck them
  before any retry writes, without restoring missing rows automatically.
- [x] Reproduce competing item writers, serialize allocation before the identity
  read, use a name-targeted upsert, and roll back a failed relocation locally while
  retaining caller transactions and commit=False behavior.
- [x] Extend the real-Qt probe and document the conservative recovery policy and
  historical-save limitations in `docs/legacy_migration_recovery.md`.
- [x] Complete full pytest, Tier-1, real-Qt, formatting and independent review.
  Commit the corrections inline on the existing PR branch.

Follow-up validation: 1,846 pytest passes, 41 skips and 9 passing subtests; all
13 Tier-1 checks; real-Qt migration and boot probes; Ruff lint and format checks.
The full pytest suite passed outside the sandbox because unrelated Chromium and
multimedia tests cannot run under its restrictions. Independent review also
verified 160 simultaneous item inserts and found the missing-files startup bypass;
the bypass now has a failing-before/passing-after regression and a guard against
treating an incomplete migration as a fresh install.
