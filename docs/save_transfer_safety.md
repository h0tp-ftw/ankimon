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

Verifying those four fixes surfaced five more, all accepted:

- The media-sync guard stands aside for Anki's Preferences dialog. That dialog
  reads the same gate only to tick "Synchronize audio and images too" and
  writes whatever the box shows back into the profile on OK, so a blocked
  answer there would have silently switched the user's real AnkiWeb media-sync
  preference off for good. The guard delays a sync; it never edits a setting.
- Recording a turned-away sync and replaying it happen under one lock. Anki
  evaluates the gate on a background thread, so without it a request could be
  remembered just after the release that would have replayed it.
- The success notices for Import, Rescue and Backup Restore are guarded. They
  reach into Qt in a shutdown-adjacent state, and a failure there used to
  surface as "aborted, nothing was replaced" over a save that was staged.
- A second import attempt while one is pending has its own message naming the
  pending import, instead of an abort notice that is true of the attempt and
  misleading about the session.
- Cancelling a pending import is committed by removing the manifest. A failure
  of the durability flush that follows no longer reports "could not be
  cancelled, try again", which the retry immediately contradicted with "there
  is no pending save import".

An external review of the branch's own fixes found no surviving instance of the
four findings it re-checked. Verifying them adversarially instead surfaced
thirteen residual gaps, all accepted; eight further claims were refuted on the
source and not acted on.

Capture and the media-sync guard:

- The capture signature no longer counts the scan's own `-shm` churn as somebody
  else writing. Every read-only SQLite open the scan performs on a WAL-mode media
  save creates or restamps that save's `-shm`, so the before/after pair could
  never agree: the guard would never release and the scan would re-dispatch for
  as long as the profile was open. The add-on's own exporter writes single-file
  DELETE-mode saves, so this is a file that arrived some other way, not the
  common path.
- A sync the guard turned away is held rather than dropped when the replay
  stands down. During a backup restore Anki keeps `restoring_backup` set for the
  whole session and switches its own unattended syncs off, so the request that
  was discarded there was gone for good.
- A pass that leaves the guard up schedules its own rescan. The only recurring
  trigger was the media-sync hook, and `MediaSyncer.start` returns before firing
  it when the gate says no, so the guard suppressed its own retry. That timer is
  requested unconditionally: Anki's collection gate drops rather than defers.
- The gate reads the user's preference before the profile folder, and fails open
  if the folder cannot be resolved. Anki's own `media_syncing_enabled` is a dict
  lookup that cannot raise, and three callers assume as much.

Import lifecycle:

- Every notice that reports an armed import is guarded, not just the success
  one. An exception from Qt in the staged or already-pending branch unwound into
  the caller's "aborted, nothing was replaced" handler.
- The menu can tell a pending import from one that has already installed. A
  failed post-install cleanup leaves the record beside a replaced save, and both
  answers were the wrong way round: Cancel claimed the save was unchanged, and a
  second attempt was told the first would install at the next restart.
- The advertised recovery path always holds the newest pre-install snapshot. A
  retried install used to redirect the new snapshot and leave the stale one under
  the name the user was given.
- Superseded recovery snapshots are pruned to one. An install that cannot finish
  is retried on every start and each attempt snapshots the save again.
- The whole startup installation shares one 30-second budget. It runs during
  add-on import, before Anki has a window or a progress dialog.
- A record whose target no longer exists says so, and Cancel Pending Save Import
  covers both save modes, since startup reports failures for both.

Shutdown and scheduling:

- Retention cannot take Anki's close down with it, and it stops when the
  shutdown budget is gone. Abandoned staging directories are swept after an hour.
- The media scan is dispatched as the last act of profile-open again. Its
  main-thread callback would otherwise be delivered inside the modal dialogs
  that follow, and the rescue it can offer reaches `close_anki` from there.

A Tier-2 probe now plays the whole import contract out across a real restart of
the real add-on: stage over the live save, keep editing, exit, start again, and
check that the runtime is on the imported save with the final pre-import
progress in the recovery copy and nothing left staged. Windows file locking and
authenticated AnkiWeb behaviour remain outside this validation.
