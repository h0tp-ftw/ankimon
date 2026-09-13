# Save transfer safety after #797

This follow-up addresses the transfer and migration review of #797, including
the inherited backup defect tracked in #798.

## Import and recovery

Import prepares a private, verified save for the next **full Anki restart**.
It never replaces the database used by the current game objects. Choosing
**Keep Editing** during shutdown, a shutdown exception, and shutdown-triggered
sync therefore leave the current runtime and save together. The menu action
**Cancel Pending Save Import** discards prepared imports for both save modes
(`ankimon.db` and `ankimonDEV.db`).

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
is unchanged, and they leave nothing staged. The one exception after publication
is a manifest that has vanished by read-back: then nothing will install, so
staging removes its staged copy and fails as an ordinary abort.

Recovery copies live in `ankimon_recovery/pre-import-<token>/` beside the active
database. The preparation message displays the reserved location, and
**Browse Pre-import Recovery Saves…** opens the folder. Routine backup retention
does not delete these copies. Import a recovery `.db` through the same import
flow to restore it. A failed installation retry keeps the newest snapshot under
the advertised name, plus the one it superseded; older retry copies are pruned.

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
the raw database and its WAL or rollback journal in the same local recovery
directory. `-shm` is left out: it is a rebuildable index, and every read-only
open restamps it, which gave an unchanged save a new archive on every pass. This
is recovery material, not a guarantee that the archive contains a consistent
save. Existing copies and raw archives must match their content before reuse.
Damaged files remain untouched. If even raw capture fails, media sync pauses for
that profile in memory until capture succeeds. Sync preferences are not changed.
Close anything locking the files. After a completed scan, Ankimon retries about
every 30 seconds while Anki is open; if the scan itself could not run, restart Anki
to retry.

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
  action instead of announcing an abort with the current save unchanged. The
  exception is a manifest that has vanished by read-back, which leaves nothing
  to install and is reported as an abort.
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
- Cancelling a pending import is committed before anything is removed. A failure
  of the durability flush that follows no longer reports "could not be
  cancelled, try again", which the retry immediately contradicted with "there
  is no pending save import". The fifth round moved that commit onto a synced
  rewrite of the record, so a crash cannot undo it either.

An external review of the branch's own fixes found no surviving instance of the
four findings it re-checked. Verifying them adversarially instead surfaced
fifteen residual gaps in this branch, all accepted; eight further claims were
refuted on the source and not acted on.

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

A fifth round left ten review threads open, and the User Data Safety pre-merge
check failed on two claims. Nine threads led to changes. The `-shm` thread and
the retention half of the pre-merge check were refuted on the source, the second
with a regression test.

Cancellation and the pending record:

- Cancelling is durable before it is reported. `pending.json` is rewritten in
  place as a cancelled record and synced as a file, and only then unlinked. A
  file sync needs no directory sync, which is the step whose failure the third
  round had to tolerate, so a crash that undoes the unlink or the staged copy's
  removal brings back a record that installs nothing. If the rewrite itself
  cannot be synced, Cancel reports failure and puts the original record back, so
  the retry it asks for still finds the import. A separate tombstone file was not
  used: its directory entry would need the very sync that failed. The record is
  padded to the old one's length, so no truncate follows the write for a crash to
  split from it.
- Whether a pending record describes an already-installed import has three
  answers. The check runs on the GUI thread only to choose wording, so the save
  gets two seconds rather than SQLite's 30-second busy timeout. A damaged record
  or a locked or unreadable save answers "unknown", and Import, Backup Restore
  and Cancel word that as neither pending nor installed, without guessing which
  of the two could not be read; the cancellation event carries `installed=None`.
- Cancel reports one line per save. A cancellation that succeeds for one save and
  fails for the other names both outcomes, and an import that had already
  installed on one save is no longer described as if it were the other's.
- A manifest that has vanished by read-back is the one failure after publication
  that stays an ordinary abort: nothing will install, and the staged copy goes.

Startup and shutdown budgets:

- The startup install checks its shared budget between blocks of the digest and
  of the copy into place, and before each file sync. An fsync already in flight
  cannot be abandoned, and nothing is checked after the atomic replacement.
- Retention, and the removal of a failed backup attempt, delete one entry at a
  time under the shutdown deadline instead of calling `shutil.rmtree`.
- Retention renames a doomed directory out of the `backup_` namespace before
  deleting anything in it, and a later pass finishes what is left. Deleting
  entries restamps a directory's mtime and retention orders by mtime, so a
  removal cut short in place, by the deadline or by one locked file, left remains
  that sorted as the newest backup and evicted a good one on the next pass. This
  turned up while checking the refuted claim below. Backup Manager's Delete
  button had the same problem and now renames first too. A linked backup is
  removed as a link: walking it would delete the files it points at, which
  `shutil.rmtree` refused to do.

Refuted:

- *A backup that fails to delete lets retention evict a newer one.* Retention only
  ever attempts the oldest directories beyond `MAX_BACKUPS`, and a failure adds no
  attempt, so nothing in the kept set is touched. Stopping at the first failure,
  as suggested, would let one permanently locked directory grow the backup folder
  without bound. The test locks the oldest backup and checks that exactly the next
  one goes and the newest five stay.
- *The `-shm` assertion can fail on other SQLite builds.* The last connection
  `get_db_stats` closes is read-only, and SQLite removes `-wal` and `-shm` on
  close only after a checkpoint, which a read-only connection cannot run. That
  holds for the builds it was checked against, but it is not a guarantee for
  every build or VFS; since the sixth round the test skips where the index is
  gone instead of asserting that it never is.

Also: a deferred media sync that cannot be restarted is logged (the request itself
has been kept since the fourth round), each phase of the Tier-2 import probe is
bounded at 300 seconds, and the preference-off guard test now drives the user's
preference through the installed wrapper instead of replacing it.

These changes were checked adversarially before they were pushed. That found seven
gaps in them, each now fixed with a test: three ways Cancel's wording could be
wrong across two saves, a record rewrite that a crash could split from its
truncate, the Delete button's in-place removal, a linked backup being walked, and
no test driving the digest and recovery-sync budget checks through the install
itself. The `-shm` refutation was re-checked against SQLite 3.46.1's Windows VFS,
which also refuses a read-only handle the exclusive lock a checkpoint needs.

A sixth round came from a quality pass over the whole branch, two further
CodeRabbit threads, and its Anki and PyQt compatibility check. Each fix below has
a test that fails without it.

Media sync:

- Reopening a profile that was already captured this session left media sync
  paused until Anki restarted. `guard_media_saves_now` only stats files, so the
  reopen re-armed the guard, and the settled scan that followed returned before
  the one call that lifts it.
- A retry that the throttle turned away scheduled nothing. Qt rounds a 30-second
  timer to whole seconds and can fire it half a second early, and a timer armed
  for an earlier pass can land after a later failed pass moved the throttle on.
  Either way the guard stayed up with no retry left. A pass the throttle turns
  away now asks for a timer covering what is left of it.
- A rerun requested during a scan ran even when the profile had closed meanwhile,
  and put its warnings and the rescue prompt over the profile manager.
- A raw archive no longer includes `-shm`, so an unreadable WAL-mode save is
  archived once rather than once per pass, with a warning each time.
- The pause notice says Ankimon retries by itself about every 30 seconds when a
  retry timer is armed. A scan that failed or could not be dispatched arms none, so
  there it still says to restart Anki: a timer would repeat that path's own warning
  every 30 seconds.
- A reopen inside the 30-second throttle puts the earlier pass's verdict back over
  the re-armed guard, so a save that was archived but not verified does not leave
  media sync paused either.

Import and recovery:

- SQLite opens work on a Windows network path. `Path.as_uri` puts a UNC server in
  the URI authority, and SQLite refuses every authority but an empty one or
  `localhost`, so a profile on a redirected AppData folder could not back up,
  restore, import, export or compare saves. That case now percent-encodes the
  whole native path into `file:` with no authority, which SQLite decodes to
  exactly the filename a plain open would use, with `mode=ro` still applied. The
  same helper covers the two opens in `save_transfer` and `ankimon_sync` that
  predate this branch. It was exercised on Linux with `as_uri` answering the way it
  does on Windows, not on a real share.
- A filesystem that cannot sync a directory (EINVAL, ENOTSUP) no longer blocks an
  install at every start. Nor does a recovery folder on a volume that refuses
  chmod, provided this user owns it. A real I/O error, and a folder owned by
  another account, still stop the install before anything is replaced.
- An accepted rescue that could not quiet the live save did nothing and said
  nothing. It now reports why, as `main` did, and that the rescue will be offered
  again after the next sync or restart.
- Cancel over a record whose save no longer exists says the import was cancelled,
  not that Ankimon could not tell whether it had installed. The exception is a
  record with a recovery copy beside it: an install got as far as replacing that
  save, so the answer stays unknown and points at the copy.
- Every menu path the import and recovery notices name exists. One pointed at
  "Browse Recovered Saves",
  which never existed, and all of them left out the Game submenu the actions live
  in. A test reads the menu to keep it that way.
- The startup failure notice no longer says that no save was replaced. `get_db`
  tries both save modes, and the other one may have installed in the same start.

CodeRabbit:

- `_remove_tree` checks the shutdown deadline before listing a directory as well
  as between entries. Iterating `iterdir()` lazily does not help on its own:
  `Path.iterdir` reads the whole listing before it yields anything, through
  `os.listdir` on Python 3.12 and `list(os.scandir(...))` on 3.14. Past the
  deadline an empty directory is still removed, since `rmdir` needs no listing;
  a failed shutdown attempt's staging folder is empty, and a test pins that it
  does not outlive the attempt.
- The `-shm` test skips where the index is gone after the read-only close, and the
  refutation above no longer claims every build.
- The compatibility check objected to installing synchronously while the add-on
  loads. It has to: the install must finish before any database manager or game
  object opens the save, or the runtime writes into a save that is being replaced,
  which is the #797 defect. An ordinary start pays one failed open of
  `pending.json` per save mode. A 100 MB save installed in 1.15 seconds in testing, 10 MB in
  0.27 seconds, and the 30-second budget is a ceiling for a locked save, after
  which the import stays pending.

After CodeRabbit re-reviewed that push:

- The notice that Anki could not close after an import or a restore was staged is
  guarded like every other notice about an armed import. Its own failure reached
  `import_save`'s "Import aborted ... Nothing was replaced" handler.
- Backup Manager's warnings about an armed restore go through `services.ui`, as
  new popups should. In Anki that presenter shows the same `showWarning` dialog.
- *The missing-save probes in `pending_import_is_installed` can freeze the GUI
  thread on a disconnected network profile.* Not changed. `pending_import_info`
  runs first in the same call: it resolves the save's path and opens
  `pending.json` beside it synchronously, so a dead mount blocks there before
  either probe. The callers have just done synchronous file work on that volume by
  design, and the two-second budget bounds SQLite's wait on a locked save, not a
  mount that has gone away.

Tests only: the prune test checks that the superseded copy it keeps is the newest,
a staged save swapped for another valid save now reaches the digest refusal
instead of the size check, the shutdown-budget test covers a developer-mode active
save, and the sync-hardening stub of `cleanup_backups` takes its real signature.

Deferred, with reasons:

- Windows lock retries for the backup's publishing rename and the import's
  `os.replace` calls (#636). These are real regressions from `main` in that
  environment, but a fix needs the lock-retry helpers in a module the stdlib-only
  import code can load, and a Windows runner to exercise them. That is its own
  change.
- A current save that fails `quick_check` but still works blocks imports and
  Backup Restore, because the safety snapshot of it fails verification. Repairing
  the snapshot changes what gets retained, so it needs its own review.
- Retention orders backups by directory mtime, which a read of a WAL-mode backup
  restamps. Ordering by the timestamp in the name has to handle legacy names.
- A staged import for `ankimon.db` whose file is deleted before the next start is
  reported as impossible to install. `get_db` then creates a fresh save, and the
  following start installs the import over it without a notice; only the recovery
  copy keeps what was played in between. Which save should win is a product
  decision.
- Smaller items: a staged rescue can be offered again by a later scan in the same
  session; the unverified-archive warning repeats at every start for a permanently
  damaged file; temporary copies left by a force-quit install or an interrupted
  cancel are not swept; the import lifecycle is thinly logged; and a handful of
  paths are covered only indirectly by tests.
