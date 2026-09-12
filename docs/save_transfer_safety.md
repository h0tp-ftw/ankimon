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

Publishing `pending.json` is staging's commit point. Anything that fails after
it — the directory syncs, the read-back — leaves an import that WILL install at
the next full start, so it is reported as pending with the cancel instruction
rather than as an abort. Only failures before publication say the current save
is unchanged, and they leave nothing staged.

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
guarded before Anki can start automatic media sync, then captured on a background
worker. Media sync resumes only after capture completes. Anki reads that gate
once, when a sync starts, so clearing the guard would otherwise only permit the
*next* attempt: a collection sync that began during the capture finishes without
media and is never re-requested. Ankimon therefore remembers a sync its guard
turned away and asks Anki's media syncer for it once the capture succeeds. That
restart still honours the user's own media-sync preference, sign-in and profile
state, and a scan that suppressed nothing starts nothing. Verified copies include
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
and dispatch failures remain retryable and visible. Preservation work runs on
the guarded worker and does not copy, archive, or compare saves on the GUI
thread. Unchanged unreadable saves have a 30-second retry delay; changed files,
permissions, or SQLite sidecars re-arm immediately.
Moving an uncaptured save out of the media folder also clears its sync guard.
Recovery paths are recorded in the Ankimon
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

## CodeRabbit review of #850

All ten inline findings and the failed User Data Safety pre-merge check were
accepted after checking the implementation. The fixes preserve the existing
restart-only import behavior:

- Failed backups never enter retention. A backup stays in a hidden staging
  directory until its required SQLite snapshot and summary are complete; even
  a failed cleanup cannot cause that directory to evict an older valid backup.
- Restore and import callers request observable shutdown errors and report that
  their prepared save remains pending. Cancellation filesystem errors and
  recovery-folder access failures receive actionable menu warnings.
- Errors after atomic replacement are reported separately: the imported save
  is active, and final disk sync/cleanup can retry without reinstalling it over
  newer progress. Failures before replacement still leave the old save active.
- File fsync uses a writable handle for Windows. Existing pre-import recovery
  directories are restricted before access, including those opened by browsing.
- Preservation and raw archive writes run once on the guarded worker. Repeated
  sync-stop notifications cannot immediately repeat an unchanged failed capture.
- The locked-source timing test pins its probe budget; the permission test skips
  root; the digest-collision test uses a valid source and a damaged copy in the
  actual recovery directory and checks that a numbered copy preserves both.

The optional 80% docstring-coverage warning was not adopted as a blanket rewrite
of the transfer tests and existing helpers. Behavioral contracts and the new
failure/scheduling paths are documented where they need explanation.

A second review round raised four further findings, all accepted:

- Import outcome warnings are delivered inside their own guard, and each list is
  cleared only after the user has actually been shown it. A presenter failure no
  longer discards the notice or prevents the AnkiWeb sync hooks from registering.
- The automatic shutdown backup spends a single 30-second budget across every
  database it snapshots, starting with the one the result depends on, so two
  locked saves cannot delay closing Anki for a full timeout each. Manual and
  pre-overwrite backups keep the per-file default.
- A media scan whose source changed after the worker captured it is discarded
  whole rather than applied. Its comparison figures and rescue snapshot describe
  a save that is already gone, and the next pass rescans with sync still paused.
  The comparison uses the state the worker observed, so a transient metadata
  failure while the scan was being dispatched no longer costs an extra pass.
  The worker reads that state both before and after the capture, so a writer
  landing *during* the copy is caught too: the copy verifies its own bytes, not
  that the source held still while they were read, and only a capture bound to
  one observed revision releases the sync guard.
- The worker/GUI-thread sentence above names preservation work as its subject.

A third review round raised four further findings, all accepted:

- Media capture is bound to a source revision observed both before and after
  the copy. Verifying the copy's own bytes never established that the source
  held still while they were read, so a writer landing mid-capture left a
  recovery copy of the old version described by the new version's signature —
  and the callback, comparing new against new, released the sync guard over
  progress that had never been preserved.
- Staging is split at its publication commit point. Once `pending.json` is in
  place the import installs at the next full start whatever fails afterwards,
  so Import, Rescue and Backup Restore report it as pending and name the cancel
  action instead of announcing an abort with the current save unchanged.
- A media sync the capture guard turned away is re-requested once capture
  succeeds. Anki reads the gate only as a sync starts, so clearing it merely
  permitted the next attempt while the suppressed request was gone.
- The shutdown backup's single budget now also covers summary generation, which
  fell back to reading the live database — and its own busy timeout — whenever
  the required snapshot had not been taken.
