# Save transfer safety after #797

This follow-up addresses the transfer and migration review of #797, including
the inherited backup defect tracked in #798.

## Import and recovery

Import prepares a private, verified save for the next **full Anki restart**.
It never replaces the database used by the current game objects. Choosing
**Keep Editing** during shutdown, a shutdown exception, and shutdown-triggered
sync therefore leave the current runtime and save together. The menu action
**Cancel Pending Save Import** discards a prepared import for the active mode.

Before constructing database managers or game objects at the next process
start, Ankimon captures the final current save using SQLite's backup API,
verifies it, and installs the prepared snapshot. Failed startup attempts cannot
retry through an add-on reload in that process. A token in the installed save
prevents a crash immediately after replacement from repeating the import over
later progress.

Recovery copies live in `ankimon_recovery/pre-import-<token>/` beside the active
database. The preparation message displays the reserved location, and
**Browse Pre-import Recovery Saves…** opens the folder. Routine backup retention
does not delete these copies. Import a recovery `.db` through the same import
flow to restore it. Failed installation retries retain earlier recovery copies.

Imports require leaderboard sign-in again. Incoming current and legacy
credentials are removed; explicit empty authentication settings prevent legacy
local configuration from restoring another account. Destination JSON migration
does not run over an imported save. The review watermark is rebased when the
destination collection opens, before mobile detection, including reviews that
arrived during shutdown sync.

Backup Manager restore uses the same staged, next-process installation path and
captures the final current save before replacement. Because a Backup Manager
snapshot is private local recovery material rather than a portable import, its
leaderboard credentials are retained.

## Export and media preservation

Export snapshots, removes credentials, vacuums, and verifies in private temporary
storage. Only sanitised bytes enter the destination directory, including its
temporary file. Completion statistics come from the actual exported snapshot.

At profile open, both `ankimon.db` and `ankimonDEV.db` in `collection.media` are
captured before Anki can start automatic media sync. Verified copies include
committed WAL contents and use content-derived filenames under
`ankimon-media-recovery/` beside `collection.media`, not inside the media folder.
That keeps newly-created recovery databases out of AnkiWeb media sync while
still leaving old underscore-prefixed migration copies readable for backwards
compatibility. Only saves in the active mode are compared for recovery.

If SQLite cannot read a source, Ankimon retains a labelled **unverified** ZIP of
the raw database and its available sidecars in the same local recovery
directory. This is recovery material, not a guarantee that the archive contains
a consistent save. Existing copies and raw archives must match their content
before reuse. Damaged files remain untouched. If even raw capture fails, media
sync pauses for that profile in memory until capture succeeds. Sync preferences
are not changed. Close anything locking the files and restart Anki to retry.

Preservation status is separate from the feature-removal announcement. Worker
or dispatch failures remain retryable and visible; they do not start a full
comparison scan on the GUI thread. Recovery paths are recorded in the Ankimon
log. No original media file is deleted.

## Review disposition and validation limits

All seven safety findings were accepted: import lifecycle, export credentials,
pre-sync capture, consistent safety backups, accurate notices, both save modes,
and WAL-aware preservation. General backups also use verified SQLite snapshots
of the exact active path. No compatibility workaround for `uses_collection=False`
was needed.

A richer recovery browser with trainer/count summaries and cancellable transfer
progress UI remain separate UX work. Aggregate counters never establish that
one collection contains another.

Regression coverage uses real SQLite files, WAL transactions, injected filesystem
failures, fresh subprocesses, malformed pending metadata, custom database paths,
and assertions that new recovery files stay outside `collection.media`.

A real Anki 25.09.2 session with a temporary profile also exercised an unfinished
Add Cards note and **Keep Editing** during import shutdown. The original save
remained writable; a subsequent full process loaded the imported trainer and
retained the final old progress in recovery. Dialog choices were automated;
Anki's actual editor and close lifecycle ran. Authenticated AnkiWeb conflict
resolution and Windows file locking remain outside this validation.
