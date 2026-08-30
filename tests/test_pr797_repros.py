"""Regression guards for the defects found in the first cut of this branch.

Each of these was written RED against head 0582ffd0 -- reproducing a real
failure -- and is kept green by the fixes in this commit. They are grouped by
what they protect rather than by module, because they share one theme: the
migration must never treat an ABSENCE or an UNKNOWN as a resolution.

  * a media-sync hook that fires on failure is not proof a download arrived;
  * a save that will not open is unknown, not empty, on either side;
  * developer and normal saves never share a protected name;
  * export never writes over the live save, and never carries a credential.

A second round, written RED against head 38bc970a, follows the first. Those
share a different theme: a resolution is only as good as what it looked at.

  * settling on a stale save seconds before the download lands is still a
    permanent miss, so a settle records WHAT it resolved and expires when that
    changes;
  * two saves that merely diverged have no winner, and the lexicographic
    _progress_key invented one -- then wrote it over the other;
  * none of that file work may happen on the profile-open stack.

Reuses the shipped scaffolding in test_save_transfer so the setup matches the
rest of the suite exactly.
"""

import os
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from test_save_transfer import _make_save, _Logger, st  # noqa: F401
from test_save_transfer import logger, media, live_db  # noqa: F401


# ===========================================================================
# P0 #1 — a FAILED or ABORTED media sync permanently settles the migration
# ===========================================================================
def test_a_failed_media_sync_does_not_settle_an_empty_folder(
    media, live_db, logger, monkeypatch
):
    """aqt/mediasync.py:77-86 --

        def _on_finished(self, future, is_periodic_sync=False):
            self._syncing = False
            self._last_progress_at = int_time()
            gui_hooks.media_sync_did_start_or_stop(False)   # <-- line 80
            exc = future.exception()                        # <-- line 82
            if exc is not None:
                self._handle_sync_error(exc, is_periodic_sync)

    The hook fires BEFORE Anki inspects the future, so `False` means "the worker
    stopped", never "it succeeded". A network failure or a user abort fires the
    identical hook -- and it also fires degenerately when media syncing is off.

    run_media_migration treated that single signal as proof a download had its
    chance, settled an empty folder, and burned the per-profile flag forever.
    The PR body states the hook is "treated only as 'rescan now', never as 'it
    worked'"; this asserts that stated invariant.
    """
    marked = MagicMock()
    monkeypatch.setattr(st, "_mark_migration_done", marked)

    # The first media sync on a new device FAILS. Folder is still empty.
    st.run_media_migration(MagicMock(), logger, after_media_sync=True)

    marked.assert_not_called()

    # ...and the save that arrives on the next, successful sync is still found.
    _make_save(media / "ankimon.db", pokemon=42, badges=8, history=99)
    ask = MagicMock(return_value=False)
    monkeypatch.setattr(st, "askUser", ask)
    st.run_media_migration(MagicMock(), logger, after_media_sync=True)
    ask.assert_called_once()


# ===========================================================================
# P0 #2 — a corrupt protected copy is silently overwritten by a stale one
# ===========================================================================
def test_corrupt_protected_copy_is_not_overwritten_by_a_stale_candidate(
    media, live_db, logger, monkeypatch
):
    """The realistic corruption mode: a partial write / disk error leaves the
    16-byte SQLite header intact and shreds the pages.

    get_db_stats() then returns None, _progress_key floors it to (-1,-1,-1), the
    `protected >= best` guard fails, and _sqlite_backup happily writes into a
    file whose header still says "SQLite format 3" -- destroying the user's only
    protected copy. No error is logged.

    (A file of pure garbage survives only incidentally: SQLite refuses the
    destination connection with "file is not a database". That accident is the
    entire protection the current code has.)
    """
    protected = media / st.MEDIA_SAVE_NAME
    _make_save(protected, pokemon=900, badges=40, history=700)
    raw = bytearray(protected.read_bytes())
    for i in range(1024, len(raw)):  # keep the header, shred the body
        raw[i] = 0
    protected.write_bytes(bytes(raw))
    before = bytes(raw)

    assert st.get_db_stats(protected) is None  # unreadable
    _make_save(media / "ankimon.db", pokemon=1)  # readable but STALE
    monkeypatch.setattr(st, "askUser", lambda *a, **k: False)

    st.run_media_migration(MagicMock(), logger, after_media_sync=True)

    assert protected.read_bytes() == before, (
        "a stale 1-pokemon candidate overwrote the protected 900-pokemon save"
    )


# ===========================================================================
# P0 #3 — export can replace the LIVE database under its own connection
# ===========================================================================
def test_export_refuses_the_active_db_as_destination(tmp_path, monkeypatch):
    """import_save guards this (`if source.resolve() == Path(target).resolve()`),
    export_save does not. Nothing between the file picker and
    `_retry_on_lock(lambda: os.replace(tmp, dest))` compares the two paths.

    os.replace() then swaps the pathname underneath the add-on's open SQLite
    connection: the connection keeps the old, now-unlinked inode while
    ankimon.db names a new file, so later writes land somewhere nothing will
    ever read again.
    """
    active = _make_save(tmp_path / "ankimon.db", pokemon=42, badges=5, history=77)
    monkeypatch.setattr(st, "_active_db_path", lambda: active)
    live = sqlite3.connect(str(active))  # the add-on's live handle
    inode_before = os.stat(active).st_ino

    picker = MagicMock()
    picker.getSaveFileName.return_value = (str(active), "")
    monkeypatch.setattr(st, "QFileDialog", picker)
    monkeypatch.setattr(st, "showInfo", MagicMock())
    warn = MagicMock()
    monkeypatch.setattr(st, "showWarning", warn)

    try:
        result = st.export_save()
        assert os.stat(active).st_ino == inode_before, (
            "export replaced the live database's inode under its open connection"
        )
        assert result is False and warn.called, (
            "export accepted the active save as its own destination"
        )
    finally:
        live.close()


# ===========================================================================
# P0 #3b — a portable export must not carry the leaderboard credential
# ===========================================================================
def test_export_does_not_carry_the_leaderboard_api_key(tmp_path, monkeypatch):
    """Ankimon's settings live in the same SQLite file as the save
    (settings.set -> db.set_config_value -> the config table), and the export is
    a full backup of it. Before this PR there was no supported way to hand your
    save to anyone; this one ships the user's leaderboard API key with it.
    """
    active = _make_save(tmp_path / "ankimon.db", pokemon=42)
    conn = sqlite3.connect(str(active))
    conn.execute(
        "INSERT INTO config VALUES ('leaderboard.api_key', 'SECRET-KEY-abc123')"
    )
    conn.execute("INSERT INTO config VALUES ('leaderboard.username', 'scott')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(st, "_active_db_path", lambda: active)

    dest = tmp_path / "portable-export.db"
    picker = MagicMock()
    picker.getSaveFileName.return_value = (str(dest), "")
    monkeypatch.setattr(st, "QFileDialog", picker)
    monkeypatch.setattr(st, "showInfo", MagicMock())
    monkeypatch.setattr(st, "showWarning", MagicMock())

    assert st.export_save() is True

    rows = dict(
        sqlite3.connect(str(dest))
        .execute("SELECT key, value FROM config WHERE key LIKE 'leaderboard%'")
        .fetchall()
    )
    assert "leaderboard.api_key" not in rows, "the export carried the API key"
    assert b"SECRET-KEY-abc123" not in dest.read_bytes()
    # The username is not a secret and identifies the save's owner -- keep it.
    assert rows.get("leaderboard.username") == "scott"

    # The LIVE save is untouched: the strip happens on the temp copy.
    live = dict(
        sqlite3.connect(str(active))
        .execute("SELECT key, value FROM config WHERE key LIKE 'leaderboard%'")
        .fetchall()
    )
    assert live["leaderboard.api_key"] == "SECRET-KEY-abc123"


# ===========================================================================
# P1 #4 — the session latch leaks across a profile switch
# ===========================================================================
def test_no_process_global_carries_sync_state_between_profiles(
    media, live_db, logger, monkeypatch, tmp_path
):
    """Anki does not re-import add-on modules on a profile switch, so ANY
    module-global "a media sync finished" latch is inherited by the next
    profile. Profile A syncs -> latch True -> switch to B -> B's own
    collection.media is still empty because B's download has not landed -> B
    settles on A's state and burns its one-shot forever.

    The fix is structural: there is no such global. This pins that, and pins the
    behaviour it existed to produce.
    """
    assert not hasattr(st, "_media_sync_completed_this_session"), (
        "a process-global sync latch is back; it leaks across profile switches"
    )

    # --- profile A: a save is present, gets protected, rescue declined --------
    _make_save(media / "ankimon.db", pokemon=42, badges=8, history=99)
    monkeypatch.setattr(st, "askUser", lambda *a, **k: False)
    st.run_media_migration(MagicMock(), logger, after_media_sync=True)

    # --- profile switch: B has its own, still-empty media folder -------------
    media_b = tmp_path / "profileB" / "collection.media"
    media_b.mkdir(parents=True)
    monkeypatch.setattr(st, "_media_dir", lambda: media_b)
    monkeypatch.setattr(st, "_migration_done", lambda: False)
    marked = MagicMock()
    monkeypatch.setattr(st, "_mark_migration_done", marked)

    st.run_media_migration(MagicMock(), logger)  # B's profile_did_open scan
    st.run_media_migration(MagicMock(), logger, after_media_sync=True)

    marked.assert_not_called()

    # ...and B's save, once it lands, is still protected and offered.
    ask = MagicMock(return_value=False)
    monkeypatch.setattr(st, "askUser", ask)
    _make_save(media_b / "ankimon.db", pokemon=7, badges=3, history=11)
    st.run_media_migration(MagicMock(), logger, after_media_sync=True)

    assert (media_b / st.MEDIA_SAVE_NAME).is_file()
    ask.assert_called_once()


# ===========================================================================
# P1 #5 — normal and developer saves share one protected name
# ===========================================================================
def test_dev_save_does_not_land_in_the_normal_partitions_protected_name(
    media, tmp_path, logger, monkeypatch
):
    """_find_media_saves partitions candidates, but run_media_migration then
    hardcodes `protected = media_dir / MEDIA_SAVE_NAME` for EVERY target."""
    dev_active = _make_save(tmp_path / "ankimonDEV.db", pokemon=2)
    monkeypatch.setattr(st, "_active_db_path", lambda: dev_active)
    _make_save(media / "ankimonDEV.db", pokemon=500, badges=50, history=900)
    monkeypatch.setattr(st, "askUser", lambda *a, **k: False)

    st.run_media_migration(MagicMock(), logger, after_media_sync=True)

    protected = media / st.MEDIA_SAVE_NAME
    if protected.is_file():
        stats = st.get_db_stats(protected)
        assert stats is None or stats["pokemon"] != 500, (
            "the developer save was written into the normal partition's "
            "protected name (_ankimon_save.db)"
        )


def test_dev_contaminated_protected_name_is_not_offered_over_the_real_save(
    media, tmp_path, logger, monkeypatch
):
    """The consequence end-to-end. _target_db_for("_ankimon_save.db") returns
    "ankimon.db" -- the name contains no "ankimonDEV" -- so a later normal-mode
    scan adopts the developer save as a real-save candidate and offers it.

    _ankimon_save.db lives in collection.media, so media sync propagates the
    contaminated file to every other device, where the per-profile flag is unset
    and the scan runs fresh.
    """
    dev_active = _make_save(tmp_path / "ankimonDEV.db", pokemon=2)
    monkeypatch.setattr(st, "_active_db_path", lambda: dev_active)
    _make_save(media / "ankimonDEV.db", pokemon=500, badges=50, history=900)
    monkeypatch.setattr(st, "askUser", lambda *a, **k: False)
    st.run_media_migration(MagicMock(), logger, after_media_sync=True)

    real_active = _make_save(tmp_path / "ankimon.db", pokemon=3, badges=1, history=2)
    monkeypatch.setattr(st, "_active_db_path", lambda: real_active)
    monkeypatch.setattr(st, "_migration_done", lambda: False)
    ask = MagicMock(return_value=False)
    monkeypatch.setattr(st, "askUser", ask)

    st.run_media_migration(MagicMock(), logger, after_media_sync=True)

    assert not ask.called, (
        "a developer test save was offered as a rescue over the real save"
    )


# ===========================================================================
# P1 #6 — the boot scan blocks the profile-open stack on a locked save
# ===========================================================================
def test_migration_does_not_block_profile_open_on_a_locked_save(
    media, live_db, logger, monkeypatch
):
    """register_media_migration_hooks ends with a synchronous
    run_media_migration(), so get_db_stats' 30 s busy timeout is spent on the
    profile_did_open stack. Measured 5.2 s of frozen startup for ONE locked
    file; the migration inspects every candidate.
    """
    import time

    locked = _make_save(media / "ankimon.db", pokemon=9, badges=2, history=5)
    holder = sqlite3.connect(str(locked), isolation_level=None)
    holder.execute("PRAGMA locking_mode = EXCLUSIVE;")
    holder.execute("BEGIN EXCLUSIVE;")
    holder.execute("INSERT INTO items VALUES ('lockholder', 1)")
    monkeypatch.setattr(st, "askUser", lambda *a, **k: False)

    try:
        t0 = time.monotonic()
        st.run_media_migration(MagicMock(), logger, after_media_sync=True)
        elapsed = time.monotonic() - t0
        assert elapsed < 1.0, (
            f"profile-open path blocked for {elapsed:.1f}s on a locked media save"
        )
    finally:
        holder.close()


# ===========================================================================
# The same UNKNOWN rule, applied to the LOCAL save
# ===========================================================================
def test_a_locked_local_save_is_not_ranked_or_settled_against(
    media, tmp_path, logger, monkeypatch
):
    """_progress_key floors an unreadable save to (-1,-1,-1), so a local save
    that is merely locked this second -- the OneDrive/antivirus case this add-on
    already carries a lock ladder for -- would lose to any readable media copy.

    The rescue would then be offered against a side the dialog itself renders as
    "could not read this file", and a user who sensibly declined would fall
    through to _settle(): the one-shot burned on a comparison that never
    happened. Nothing may be concluded from a save that would not open.
    """
    import time

    local = _make_save(tmp_path / "ankimon.db", pokemon=500, badges=30, history=400)
    monkeypatch.setattr(st, "_active_db_path", lambda: local)
    _make_save(media / "ankimon.db", pokemon=2)  # readable, and far behind

    holder = sqlite3.connect(str(local), isolation_level=None)
    holder.execute("PRAGMA locking_mode = EXCLUSIVE;")
    holder.execute("BEGIN EXCLUSIVE;")
    holder.execute("INSERT INTO items VALUES ('lockholder', 1)")

    ask = MagicMock(return_value=False)
    monkeypatch.setattr(st, "askUser", ask)
    marked = MagicMock()
    monkeypatch.setattr(st, "_mark_migration_done", marked)

    try:
        t0 = time.monotonic()
        st.run_media_migration(MagicMock(), logger, after_media_sync=True)
        elapsed = time.monotonic() - t0

        ask.assert_not_called()  # never offered against an unreadable local
        marked.assert_not_called()  # and never settled on it
        assert elapsed < 1.0, f"blocked {elapsed:.1f}s on a locked local save"
    finally:
        holder.close()


# ===========================================================================
# A wall-clock bound, not just a lock bound
# ===========================================================================
def test_a_large_unlocked_save_cannot_outrun_the_probe_budget(
    media, live_db, logger, monkeypatch
):
    """sqlite3.connect(timeout=) bounds the wait for a LOCK and nothing else.

    PRAGMA quick_check scans the entire database, so a big enough save runs past
    MIGRATION_PROBE_TIMEOUT with no lock involved -- on the profile-open stack,
    which is the freeze this budget exists to prevent. A progress handler is the
    only real deadline: SQLite calls it every N VM instructions and aborts the
    statement when it returns non-zero.

    Driven here by pinning the budget at zero rather than by building a
    multi-gigabyte fixture: with the handler in place any scan is over budget on
    its first callback, so this fails outright if the handler is absent.
    """
    big = media / "ankimon.db"
    _make_save(big, pokemon=400, badges=8, history=400)

    monkeypatch.setattr(st, "MIGRATION_PROBE_TIMEOUT", 0.0)
    marked = MagicMock()
    monkeypatch.setattr(st, "_mark_migration_done", marked)
    ask = MagicMock(return_value=False)
    monkeypatch.setattr(st, "askUser", ask)

    st.run_media_migration(MagicMock(), logger, after_media_sync=True)

    # Over budget => unreadable => nothing concluded, nothing overwritten.
    marked.assert_not_called()
    ask.assert_not_called()
    assert not (media / st.MEDIA_SAVE_NAME).exists()


def test_the_probe_deadline_covers_quick_check_itself(tmp_path):
    """The bound must sit on the integrity check, not only on connect()."""
    from Ankimon.pyobj.ankimon_sync import _verify_sqlite_integrity

    save = _make_save(tmp_path / "ankimon.db", pokemon=200, history=200)

    assert _verify_sqlite_integrity(save) is True  # generous budget: fine
    assert _verify_sqlite_integrity(save, timeout=0.0) is False  # zero: aborts


# ===========================================================================
# Second review round: what a HONEST resolution is still allowed to conclude
# ===========================================================================
@pytest.fixture(autouse=True)
def _reset_scan_state():
    """``start_media_migration`` keeps in-flight state in a module global."""
    st._MIGRATION_SCAN_STATE.update({"running": False, "rerun": False})
    yield
    st._MIGRATION_SCAN_STATE.update({"running": False, "rerun": False})


@pytest.fixture
def real_flag_media(tmp_path, monkeypatch):
    """A media folder plus the REAL per-profile one-shot.

    The shared ``media`` fixture stubs ``_migration_done``/``_mark_migration
    _done`` down to a boolean, which is exactly the behaviour these tests exist
    to reject, so they drive the real thing against a real profile dict.
    """
    folder = tmp_path / "collection.media"
    folder.mkdir()
    monkeypatch.setattr(st, "_media_dir", lambda: folder)
    profile = {}
    monkeypatch.setattr(st.mw.pm, "profile", profile, raising=False)
    monkeypatch.setattr(st.mw.pm, "save", lambda: None, raising=False)
    return folder, profile


class _FakeTaskman:
    """Anki's taskman, but the work only happens when the test says so."""

    def __init__(self):
        self.queued = []

    def run_in_background(self, task, on_done=None, args=None):
        self.queued.append((task, on_done))

    def run_next(self, mid_scan=None):
        """Run the queued worker, then its callback.

        ``mid_scan`` runs in between, which is where a request that arrives
        while the worker is still in flight has to be simulated: the in-flight
        flag is only cleared by the callback.
        """
        task, on_done = self.queued.pop(0)

        class _Future:
            def __init__(self, fn):
                try:
                    self._value, self._exc = fn(), None
                except BaseException as exc:      # noqa: BLE001 - mirrors a Future
                    self._value, self._exc = None, exc

            def result(self):
                if self._exc is not None:
                    raise self._exc
                return self._value

        future = _Future(task)
        if mid_scan is not None:
            mid_scan()
        if on_done is not None:
            on_done(future)


@pytest.fixture
def taskman(monkeypatch):
    fake = _FakeTaskman()
    monkeypatch.setattr(st.mw, "taskman", fake, raising=False)
    return fake


# ---------------------------------------------------------------------------
# P1 -- a settle reached BEFORE the download permanently hides it
# ---------------------------------------------------------------------------
def test_a_stale_save_at_boot_does_not_settle_away_the_download(
    real_flag_media, live_db, logger, monkeypatch
):
    """The boot-ordering trap again, with a NON-empty folder.

    profile_did_open fires at aqt/main.py:568, one line before Anki starts its
    own sync at :569. The empty-folder case is guarded, but a folder that
    already holds something stale -- a leftover ``ankimon.db`` from the removed
    feature, or last week's copy -- is not an absence: the scan reads it, finds
    the local save ahead, resolves honestly, and settles. Seconds later the
    peer's newer save is downloaded on top of it, and a permanent one-shot means
    nothing ever looks at that folder again. The save is neither protected nor
    offered -- for exactly the two-device user the rescue exists to serve.

    A settle therefore records WHAT it resolved, and expires when that changes.
    """
    folder, profile = real_flag_media
    _make_save(folder / "ankimon.db", pokemon=1)          # stale leftover
    ask = MagicMock(return_value=False)
    monkeypatch.setattr(st, "askUser", ask)

    st.run_media_migration(MagicMock(), logger)           # the profile_did_open pass

    assert profile[st._MIGRATION_FLAG]                    # resolved, honestly
    ask.assert_not_called()                               # local was ahead

    (folder / "ankimon.db").unlink()                      # ...the download lands
    _make_save(folder / "ankimon.db", pokemon=42, badges=8, history=99)
    st.run_media_migration(MagicMock(), logger, after_media_sync=True)

    ask.assert_called_once()
    assert st.get_db_stats(folder / st.MEDIA_SAVE_NAME)["pokemon"] == 42


def test_an_unchanged_folder_is_not_rescanned_or_re_asked(
    real_flag_media, live_db, logger, monkeypatch
):
    """The other half: re-arming on CHANGE must not become nagging on every
    boot. A declined rescue is remembered for as long as the folder holds the
    save it was declined about."""
    folder, _profile = real_flag_media
    _make_save(folder / "ankimon.db", pokemon=42, badges=8, history=99)
    ask = MagicMock(return_value=False)
    monkeypatch.setattr(st, "askUser", ask)
    st.run_media_migration(MagicMock(), logger)
    ask.assert_called_once()

    scanned = MagicMock(side_effect=st._find_media_saves)
    monkeypatch.setattr(st, "_find_media_saves", scanned)
    for _ in range(3):
        st.run_media_migration(MagicMock(), logger, after_media_sync=True)

    assert ask.call_count == 1
    scanned.assert_not_called()          # not even opened: the check is stat-only


def test_a_legacy_boolean_one_shot_is_re_armed_exactly_once(
    real_flag_media, live_db, logger, monkeypatch
):
    """A profile that ran an earlier build carries ``True``, which promised more
    than it could deliver. Honour it for one more pass, then let it settle into
    a fingerprint."""
    folder, profile = real_flag_media
    profile[st._MIGRATION_FLAG] = True
    _make_save(folder / "ankimon.db", pokemon=1)
    monkeypatch.setattr(st, "askUser", lambda *a, **k: False)

    assert st._migration_done() is False
    st.run_media_migration(MagicMock(), logger)

    assert isinstance(profile[st._MIGRATION_FLAG], str)
    assert st._migration_done() is True


# ---------------------------------------------------------------------------
# P1 -- _progress_key invents a winner between two saves that only diverged
# ---------------------------------------------------------------------------
def test_a_diverged_candidate_does_not_overwrite_the_protected_copy(
    media, live_db, logger, monkeypatch
):
    """_progress_key is a 3-tuple compared lexicographically, so (10, 0, 50)
    ranks above (5, 3, 200) purely because 10 > 8 settles it before badges or
    history are ever looked at. The protect step then wrote the first over the
    second, destroying three badges and 150 history rows in the one file the
    whole migration exists to keep safe.

    Two devices that both played while the removed sync picked winners by mtime
    is the ORDINARY way to reach this, not a corner case.
    """
    protected = media / st.MEDIA_SAVE_NAME
    _make_save(protected, pokemon=5, badges=3, history=200)
    _make_save(media / "ankimon.db", pokemon=10, badges=0, history=50)
    monkeypatch.setattr(st, "askUser", lambda *a, **k: False)

    st.run_media_migration(MagicMock(), logger, after_media_sync=True)

    kept = st.get_db_stats(protected)
    assert (kept["pokemon"], kept["badges"], kept["history"]) == (5, 3, 200)

    # ...and the diverged side is preserved too, under its own underscore name,
    # because a bare ankimon.db is what "Delete Unused Files" deletes.
    rescued = st.get_db_stats(media / st.DIVERGED_MEDIA_SAVE_NAME)
    assert (rescued["pokemon"], rescued["badges"], rescued["history"]) == (10, 0, 50)
    assert (media / "ankimon.db").is_file()          # nothing is ever deleted


def test_a_diverged_media_save_is_never_offered_as_a_replacement(
    media, tmp_path, logger, monkeypatch
):
    """The same lexicographic answer, aimed at the user's live save. The rescue
    dialog said the media copy 'looks further along' and offered to load it;
    accepting threw away three badges and 150 history rows that existed only on
    this computer. Neither side is a superset, so there is no honest replace --
    say what is true instead."""
    active = _make_save(tmp_path / "ankimon.db", pokemon=8, badges=3, history=200)
    monkeypatch.setattr(st, "_active_db_path", lambda: active)
    _make_save(media / st.MEDIA_SAVE_NAME, pokemon=10, badges=0, history=50)
    ask = MagicMock(return_value=True)     # the user would have said yes
    info = MagicMock()
    monkeypatch.setattr(st, "askUser", ask)
    monkeypatch.setattr(st, "showInfo", info)

    st.run_media_migration(MagicMock(), logger, after_media_sync=True)

    ask.assert_not_called()
    assert info.called and "DIVERGED" in info.call_args[0][0]
    kept = st.get_db_stats(active)
    assert (kept["pokemon"], kept["badges"], kept["history"]) == (8, 3, 200)


def test_a_media_save_that_really_is_ahead_is_still_offered(
    media, tmp_path, logger, monkeypatch
):
    """The guard against over-correcting: a media copy that contains everything
    the local save does AND more is a genuine rescue, and must still be offered.
    """
    active = _make_save(tmp_path / "ankimon.db", pokemon=8, badges=3, history=200)
    monkeypatch.setattr(st, "_active_db_path", lambda: active)
    _make_save(media / st.MEDIA_SAVE_NAME, pokemon=10, badges=3, history=240)
    ask = MagicMock(return_value=False)
    monkeypatch.setattr(st, "askUser", ask)

    st.run_media_migration(MagicMock(), logger, after_media_sync=True)

    ask.assert_called_once()


def test_a_third_incomparable_save_is_left_alone_rather_than_ranked(
    media, live_db, logger, monkeypatch
):
    """Both protected names are taken by saves that diverge from the candidate.
    There is no name left that can be written without destroying something, and
    inventing more would not terminate -- so nothing is written, nothing is
    deleted, and the pass stays armed."""
    _make_save(media / st.MEDIA_SAVE_NAME, pokemon=5, badges=3, history=200)
    _make_save(media / st.DIVERGED_MEDIA_SAVE_NAME, pokemon=10, badges=0, history=50)
    _make_save(media / "ankimon.db", pokemon=1, badges=9, history=1)
    before = {
        p: (media / p).read_bytes()
        for p in (st.MEDIA_SAVE_NAME, st.DIVERGED_MEDIA_SAVE_NAME, "ankimon.db")
    }
    monkeypatch.setattr(st, "askUser", lambda *a, **k: False)

    st.run_media_migration(MagicMock(), logger, after_media_sync=True)

    for name, raw in before.items():
        assert (media / name).read_bytes() == raw, f"{name} was written over"


def test_the_diverged_copy_is_not_rewritten_on_every_pass(
    media, live_db, logger, monkeypatch
):
    """Once both sides are under underscore names they are both safe, so the
    steady state must be a no-op -- not a copy on every boot."""
    _make_save(media / st.MEDIA_SAVE_NAME, pokemon=5, badges=3, history=200)
    _make_save(media / "ankimon.db", pokemon=10, badges=0, history=50)
    monkeypatch.setattr(st, "askUser", lambda *a, **k: False)

    st.run_media_migration(MagicMock(), logger, after_media_sync=True)
    diverged = media / st.DIVERGED_MEDIA_SAVE_NAME
    stamp = diverged.stat().st_mtime_ns

    st.run_media_migration(MagicMock(), logger, after_media_sync=True)

    assert diverged.stat().st_mtime_ns == stamp


# ---------------------------------------------------------------------------
# P2 -- the scan must not run on the profile-open stack
# ---------------------------------------------------------------------------
def test_the_scan_is_dispatched_instead_of_run_on_the_caller(
    real_flag_media, live_db, logger, monkeypatch, taskman
):
    """PRAGMA quick_check reads a whole database and a locked save waits out its
    busy timeout. Both callers are on Anki's main thread -- profile_did_open and
    the media-sync hook -- so that time is a frozen UI."""
    folder, _profile = real_flag_media
    _make_save(folder / "ankimon.db", pokemon=42, badges=8, history=99)
    opened = MagicMock(side_effect=st._find_media_saves)
    monkeypatch.setattr(st, "_find_media_saves", opened)
    ask = MagicMock(return_value=False)
    monkeypatch.setattr(st, "askUser", ask)

    st.start_media_migration(MagicMock(), logger)

    opened.assert_not_called()                  # nothing was opened inline
    assert len(taskman.queued) == 1

    taskman.run_next()                          # the worker, then the callback

    opened.assert_called_once()
    ask.assert_called_once()
    assert (folder / st.MEDIA_SAVE_NAME).is_file()


def test_a_settled_profile_starts_no_scan_at_all(
    real_flag_media, live_db, logger, monkeypatch, taskman
):
    """The cheap guard stays ahead of the dispatch, or every boot pays a thread
    and a round trip to do nothing."""
    folder, _profile = real_flag_media
    _make_save(folder / "ankimon.db", pokemon=1)
    monkeypatch.setattr(st, "askUser", lambda *a, **k: False)

    st.start_media_migration(MagicMock(), logger)
    taskman.run_next()
    assert taskman.queued == []

    st.start_media_migration(MagicMock(), logger)

    assert taskman.queued == []                 # settled: not dispatched again


def test_a_request_arriving_mid_scan_is_coalesced_not_dropped(
    real_flag_media, live_db, logger, monkeypatch, taskman
):
    """The post-sync pass exists precisely for the case where the download lands
    while the boot scan is still running. Refusing the overlapping request would
    drop the only signal that a peer's save arrived."""
    folder, _profile = real_flag_media
    ask = MagicMock(return_value=False)
    monkeypatch.setattr(st, "askUser", ask)

    st.start_media_migration(MagicMock(), logger)        # boot: folder is empty
    assert len(taskman.queued) == 1

    def _download_lands_mid_scan():
        _make_save(folder / "ankimon.db", pokemon=42, badges=8, history=99)
        st.start_media_migration(MagicMock(), logger, after_media_sync=True)
        assert len(taskman.queued) == 0                  # coalesced, not stacked

    taskman.run_next(mid_scan=_download_lands_mid_scan)  # boot scan completes

    assert len(taskman.queued) == 1                      # ...and re-dispatches once
    taskman.run_next()
    ask.assert_called_once()
    assert taskman.queued == []                          # exactly one re-run


def test_a_profile_switch_during_the_scan_discards_the_result(
    real_flag_media, live_db, logger, monkeypatch, taskman, tmp_path
):
    """A switch keeps the same Python process. Applying profile A's media folder
    to profile B would settle B's flag on A's files and offer A's save over B's.
    """
    folder, _profile = real_flag_media
    _make_save(folder / "ankimon.db", pokemon=42, badges=8, history=99)
    ask = MagicMock(return_value=False)
    monkeypatch.setattr(st, "askUser", ask)
    settled = MagicMock()
    monkeypatch.setattr(st, "_settle", settled)

    st.start_media_migration(MagicMock(), logger)

    other = tmp_path / "profile_b" / "collection.media"
    other.mkdir(parents=True)
    monkeypatch.setattr(st, "_media_dir", lambda: other)
    taskman.run_next()

    ask.assert_not_called()
    settled.assert_not_called()


def test_start_falls_back_to_a_synchronous_run_without_a_task_manager(
    real_flag_media, live_db, logger, monkeypatch
):
    """Correctness beats responsiveness: if the dispatch is refused, do the work
    rather than skip it."""
    folder, _profile = real_flag_media
    _make_save(folder / "ankimon.db", pokemon=42, badges=8, history=99)
    ask = MagicMock(return_value=False)
    monkeypatch.setattr(st, "askUser", ask)

    class _Broken:
        def run_in_background(self, *a, **k):
            raise RuntimeError("no task manager")

    monkeypatch.setattr(st.mw, "taskman", _Broken(), raising=False)

    st.start_media_migration(MagicMock(), logger)

    ask.assert_called_once()
    assert st._MIGRATION_SCAN_STATE["running"] is False
