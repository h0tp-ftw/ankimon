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
import shutil
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from test_save_transfer import _make_save, _protected, _Logger, st  # noqa: F401
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
    st.run_media_migration(MagicMock(), logger)

    marked.assert_not_called()

    # ...and the save that arrives on the next, successful sync is still found.
    _make_save(media / "ankimon.db", pokemon=42, badges=8, history=99)
    ask = MagicMock(return_value=False)
    monkeypatch.setattr(st, "askUser", ask)
    st.run_media_migration(MagicMock(), logger)
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
    protected = media / "_addons21_ankimon.db"
    _make_save(protected, pokemon=900, badges=40, history=700)
    raw = bytearray(protected.read_bytes())
    for i in range(1024, len(raw)):  # keep the header, shred the body
        raw[i] = 0
    protected.write_bytes(bytes(raw))
    before = bytes(raw)

    assert st.get_db_stats(protected) is None  # unreadable
    _make_save(media / "ankimon.db", pokemon=1)  # readable but STALE
    monkeypatch.setattr(st, "askUser", lambda *a, **k: False)

    st.run_media_migration(MagicMock(), logger)

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
    st.run_media_migration(MagicMock(), logger)

    # --- profile switch: B has its own, still-empty media folder -------------
    media_b = tmp_path / "profileB" / "collection.media"
    media_b.mkdir(parents=True)
    monkeypatch.setattr(st, "_media_dir", lambda: media_b)
    monkeypatch.setattr(st, "_migration_done", lambda: False)
    marked = MagicMock()
    monkeypatch.setattr(st, "_mark_migration_done", marked)

    st.run_media_migration(MagicMock(), logger)  # B's profile_did_open scan
    st.run_media_migration(MagicMock(), logger)

    marked.assert_not_called()

    # ...and B's save, once it lands, is still protected and offered.
    ask = MagicMock(return_value=False)
    monkeypatch.setattr(st, "askUser", ask)
    _make_save(media_b / "ankimon.db", pokemon=7, badges=3, history=11)
    st.run_media_migration(MagicMock(), logger)

    assert len(_protected(media_b)) == 1
    ask.assert_called_once()


# ===========================================================================
# P1 #5 — normal and developer saves share one protected name
# ===========================================================================
def test_dev_save_does_not_land_in_the_normal_partitions_protected_name(
    media, tmp_path, logger, monkeypatch
):
    """_find_media_saves partitions candidates, but the protect step once wrote
    every target's copy under the NORMAL partition's name."""
    dev_active = _make_save(tmp_path / "ankimonDEV.db", pokemon=2)
    monkeypatch.setattr(st, "_active_db_path", lambda: dev_active)
    _make_save(media / "ankimonDEV.db", pokemon=500, badges=50, history=900)
    monkeypatch.setattr(st, "askUser", lambda *a, **k: False)

    st.run_media_migration(MagicMock(), logger)

    assert _protected(media) == [], (
        "the developer save was written into the normal partition's prefix"
    )
    assert len(_protected(media, "ankimonDEV.db")) == 1


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
    st.run_media_migration(MagicMock(), logger)

    real_active = _make_save(tmp_path / "ankimon.db", pokemon=3, badges=1, history=2)
    monkeypatch.setattr(st, "_active_db_path", lambda: real_active)
    monkeypatch.setattr(st, "_migration_done", lambda: False)
    ask = MagicMock(return_value=False)
    monkeypatch.setattr(st, "askUser", ask)

    st.run_media_migration(MagicMock(), logger)

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
        st.run_media_migration(MagicMock(), logger)
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
    through to the settle: the one-shot burned on a comparison that never
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
        st.run_media_migration(MagicMock(), logger)
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

    # Negative, not zero: the handler aborts on `monotonic() > deadline`, and a
    # coarse clock can return the same value at the first callback, letting the
    # scan finish and inverting every assertion below.
    monkeypatch.setattr(st, "MIGRATION_PROBE_TIMEOUT", -1.0)
    marked = MagicMock()
    monkeypatch.setattr(st, "_mark_migration_done", marked)
    ask = MagicMock(return_value=False)
    monkeypatch.setattr(st, "askUser", ask)

    st.run_media_migration(MagicMock(), logger)

    # Over budget => unreadable => nothing concluded, nothing overwritten.
    marked.assert_not_called()
    ask.assert_not_called()
    assert _protected(media) == []


def test_the_probe_deadline_covers_quick_check_itself(tmp_path):
    """The bound must sit on the integrity check, not only on connect()."""
    from Ankimon.pyobj.ankimon_sync import _verify_sqlite_integrity

    save = _make_save(tmp_path / "ankimon.db", pokemon=200, history=200)

    assert _verify_sqlite_integrity(save) is True  # generous budget: fine
    # Negative rather than zero, so the deadline is unambiguously already past.
    assert _verify_sqlite_integrity(save, timeout=-1.0) is False  # aborts


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
        self.kwargs = None

    def run_in_background(self, task, on_done=None, args=None, **kwargs):
        self.kwargs = kwargs
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
    st.run_media_migration(MagicMock(), logger)

    ask.assert_called_once()
    assert 42 in {st.get_db_stats(p)["pokemon"] for p in _protected(folder)}


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
        st.run_media_migration(MagicMock(), logger)

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
    protected = media / "_addons21_ankimon.db"
    _make_save(protected, pokemon=5, badges=3, history=200)
    _make_save(media / "ankimon.db", pokemon=10, badges=0, history=50)
    monkeypatch.setattr(st, "askUser", lambda *a, **k: False)

    st.run_media_migration(MagicMock(), logger)

    kept = st.get_db_stats(protected)
    assert (kept["pokemon"], kept["badges"], kept["history"]) == (5, 3, 200)

    # ...and the diverged side is preserved too, under its own underscore name,
    # because a bare ankimon.db is what "Delete Unused Files" deletes.
    copies = _protected(media)
    assert len(copies) == 1
    rescued = st.get_db_stats(copies[0])
    assert (rescued["pokemon"], rescued["badges"], rescued["history"]) == (10, 0, 50)
    assert (media / "ankimon.db").is_file()          # nothing is ever deleted


def test_a_diverged_bare_save_is_protected_even_when_it_ranks_lower(
    media, live_db, logger, monkeypatch
):
    """The mirror of the case above, and the one a ranking-shaped fix misses.

    When the PROTECTED copy is the one that ranks highest, there is nothing to
    preserve it from — but the bare ankimon.db beside it still holds badges and
    history the protected copy does not, and the bare name is the only one in
    the partition that "Delete Unused Files" can take. Protecting the at-risk
    file cannot be conditional on it having won a ranking."""
    protected = media / "_addons21_ankimon.db"
    _make_save(protected, pokemon=10, badges=0, history=50)
    _make_save(media / "ankimon.db", pokemon=5, badges=3, history=200)
    monkeypatch.setattr(st, "askUser", lambda *a, **k: False)

    st.run_media_migration(MagicMock(), logger)

    kept = st.get_db_stats(protected)
    assert (kept["pokemon"], kept["badges"], kept["history"]) == (10, 0, 50)
    copies = _protected(media)
    assert len(copies) == 1
    rescued = st.get_db_stats(copies[0])
    assert (rescued["pokemon"], rescued["badges"], rescued["history"]) == (5, 3, 200)


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
    _make_save(media / "_addons21_ankimon.db", pokemon=10, badges=0, history=50)
    ask = MagicMock(return_value=True)     # the user would have said yes
    info = MagicMock()
    monkeypatch.setattr(st, "askUser", ask)
    monkeypatch.setattr(st, "showInfo", info)

    st.run_media_migration(MagicMock(), logger)

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
    _make_save(media / "_addons21_ankimon.db", pokemon=10, badges=3, history=240)
    ask = MagicMock(return_value=False)
    monkeypatch.setattr(st, "askUser", ask)

    st.run_media_migration(MagicMock(), logger)

    ask.assert_called_once()


def test_every_divergent_bare_save_gets_a_protected_home(
    media, live_db, logger, monkeypatch
):
    """The THIRD save, and the fourth, and the tenth.

    Reserving one spare protected name assumes one spare is always enough. It is
    not, over time: _ankimon_save.db holds save A, a divergent bare ankimon.db
    is preserved as _ankimon_save_diverged.db = B, and then a third device
    delivers a divergent bare ankimon.db = C. Both reserved names are taken by
    saves that diverge from C, so C was knowingly left under the bare name --
    which is precisely the name Anki's "Delete Unused Files" deletes, and the
    deletion propagates to every other device. A log line saying "do not use
    Delete Unused Files here" is not a recovery mechanism.

    Content-addressed names are unbounded but stable, so there is no Nth save
    that runs out of room.
    """
    occupied = {
        "_addons21_ankimon.db": (5, 3, 200),
        "_src_ankimon.db": (10, 0, 50),
    }
    for name, (pokemon, badges, history) in occupied.items():
        _make_save(media / name, pokemon=pokemon, badges=badges, history=history)
    before = {name: (media / name).read_bytes() for name in occupied}
    monkeypatch.setattr(st, "askUser", lambda *a, **k: False)

    # C, then D: each arrives under the bare name, diverging from everything.
    for pokemon, badges, history in ((1, 9, 1), (2, 11, 4)):
        (media / "ankimon.db").unlink(missing_ok=True)
        _make_save(media / "ankimon.db", pokemon=pokemon, badges=badges, history=history)
        monkeypatch.setattr(st, "_migration_done", lambda: False)
        st.run_media_migration(MagicMock(), logger)

    held = {
        (s["pokemon"], s["badges"], s["history"])
        for s in (st.get_db_stats(path) for path in _protected(media))
    }
    assert (1, 9, 1) in held, "the third divergent save was left under the bare name"
    assert (2, 11, 4) in held, "the fourth divergent save was left under the bare name"
    for name, raw in before.items():
        assert (media / name).read_bytes() == raw, f"{name} was written over"
    assert logger.errors == []


def test_the_diverged_copy_is_not_rewritten_on_every_pass(
    media, live_db, logger, monkeypatch
):
    """Once both sides are under underscore names they are both safe, so the
    steady state must be a no-op -- not a copy on every boot."""
    _make_save(media / "_addons21_ankimon.db", pokemon=5, badges=3, history=200)
    _make_save(media / "ankimon.db", pokemon=10, badges=0, history=50)
    monkeypatch.setattr(st, "askUser", lambda *a, **k: False)

    st.run_media_migration(MagicMock(), logger)
    copies = _protected(media)
    assert len(copies) == 1
    diverged = copies[0]
    stamp = diverged.stat().st_mtime_ns

    # The `media` fixture settles a BOOLEAN one-shot, so the second pass would
    # return at the _migration_done() guard and the assertion below would hold
    # no matter what a repeat scan does. Re-arm, so the scan really runs again.
    monkeypatch.setattr(st, "_migration_done", lambda: False)
    st.run_media_migration(MagicMock(), logger)

    assert diverged.stat().st_mtime_ns == stamp
    assert _protected(media) == [diverged]      # and no second copy of it either


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
    assert len(_protected(folder)) == 1


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
        st.start_media_migration(MagicMock(), logger)
        assert len(taskman.queued) == 0                  # coalesced, not stacked

    taskman.run_next(mid_scan=_download_lands_mid_scan)  # boot scan completes

    assert len(taskman.queued) == 1                      # ...and re-dispatches once
    taskman.run_next()
    ask.assert_called_once()
    assert taskman.queued == []                          # exactly one re-run


def test_a_download_landing_mid_scan_is_not_settled_away(
    real_flag_media, live_db, logger, monkeypatch, taskman
):
    """The race the background dispatch introduced, and the reason the settle
    stores the fingerprint the SCAN took rather than the folder as it stands.

    The scan reads a stale save on a worker while Anki's media sync is running.
    The peer's newer save lands before the callback gets to settle. Fingerprint
    the folder at settle time and the stored signature covers a file nothing
    ever examined -- so the coalesced rerun looks up, sees a match, and no-ops.
    That is the original P1 again, now hiding inside a window of milliseconds.
    """
    folder, _profile = real_flag_media
    _make_save(folder / "ankimon.db", pokemon=1)          # stale, local is ahead
    ask = MagicMock(return_value=False)
    monkeypatch.setattr(st, "askUser", ask)

    st.start_media_migration(MagicMock(), logger)

    def _download_lands_after_the_read():
        (folder / "ankimon.db").unlink()
        _make_save(folder / "ankimon.db", pokemon=42, badges=8, history=99)
        st.start_media_migration(MagicMock(), logger)

    taskman.run_next(mid_scan=_download_lands_after_the_read)

    ask.assert_not_called()                  # the scan only saw the stale save
    assert len(taskman.queued) == 1          # ...and the rerun was NOT swallowed

    taskman.run_next()

    ask.assert_called_once()
    assert 42 in {st.get_db_stats(p)["pokemon"] for p in _protected(folder)}


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
    monkeypatch.setattr(st, "_mark_migration_done", settled)

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


# ---------------------------------------------------------------------------
# Third round: what an interruption, and an unreadable neighbour, may cost
# ---------------------------------------------------------------------------
def test_an_interrupted_protect_does_not_destroy_the_protected_copy(
    media, live_db, logger, monkeypatch
):
    """Connection.backup writes pages INTO the file it is handed, so backing up
    straight over the protected copy means a full disk, a killed process or a
    power loss leaves that copy half-written -- and the one file this migration
    exists to keep safe is gone. Build into a temp and move it into place."""
    protected = media / "_addons21_ankimon.db"
    _make_save(protected, pokemon=5, badges=3, history=20)
    before = protected.read_bytes()
    _make_save(media / "ankimon.db", pokemon=40, badges=9, history=90)

    import Ankimon.pyobj.ankimon_sync as sync

    def _interrupted(src, dest, before_replace=None):
        Path(dest).write_bytes(b"half a database" * 100)   # partial write...
        raise RuntimeError("disk full")                    # ...then it dies

    monkeypatch.setattr(sync, "_atomic_write_over", _interrupted)
    monkeypatch.setattr(st, "askUser", lambda *a, **k: False)

    st.run_media_migration(MagicMock(), logger)

    assert protected.read_bytes() == before
    assert (media / "ankimon.db").is_file()             # the source is intact too
    assert logger.errors                                # and the failure was logged


def test_an_unreadable_neighbour_does_not_re_ask_the_same_rescue_every_boot(
    real_flag_media, live_db, logger, monkeypatch
):
    """A folder can hold one save that will not open AND one readable save that
    is genuinely ahead of the local one. That reaches a real comparison and must
    STAY ARMED to retry the unreadable file -- and staying armed means never
    settling, so the answer has to be remembered on its own or the same prompt
    greets the user on every single profile open."""
    folder, _profile = real_flag_media
    _make_save(folder / "ankimon.db", pokemon=42, badges=8, history=99)
    corrupt = folder / "_1908235722_ankimon.db"
    _make_save(corrupt, pokemon=3)
    raw = bytearray(corrupt.read_bytes())
    for i in range(1024, len(raw)):
        raw[i] = 0
    corrupt.write_bytes(bytes(raw))          # header intact, body shredded
    ask = MagicMock(return_value=False)
    monkeypatch.setattr(st, "askUser", ask)

    for _ in range(3):
        st.run_media_migration(MagicMock(), logger)

    assert ask.call_count == 1
    assert not st._migration_done()          # still armed to retry the corrupt one


def test_a_changed_folder_asks_again_even_after_an_answer(
    real_flag_media, live_db, logger, monkeypatch
):
    """The guard on the guard: remembering an answer must be scoped to the
    folder that was answered about, or the next peer's save is silently
    swallowed by the memory of the last one."""
    folder, _profile = real_flag_media
    _make_save(folder / "ankimon.db", pokemon=42, badges=8, history=99)
    corrupt = folder / "_1908235722_ankimon.db"
    corrupt.write_bytes(b"SQLite format 3\x00" + b"\x00" * 2048)
    ask = MagicMock(return_value=False)
    monkeypatch.setattr(st, "askUser", ask)

    st.run_media_migration(MagicMock(), logger)
    assert ask.call_count == 1

    (folder / "ankimon.db").unlink()
    _make_save(folder / "ankimon.db", pokemon=90, badges=12, history=300)
    st.run_media_migration(MagicMock(), logger)

    assert ask.call_count == 2


# ===========================================================================
# Third review round: aggregate counters are not evidence of containment,
# and an aborted read is not an empty save
# ===========================================================================
def test_a_dominating_but_disjoint_save_does_not_replace_a_protected_one(
    media, live_db, logger, monkeypatch
):
    """THE unsound inference, in one file pair.

    ``_dominates`` compared (pokemon, badges, history) and the protect step read
    the answer as "everything the protected copy holds is in the candidate too,
    so replacing it loses nothing". Aggregates cannot say that. Here the
    protected copy holds Pokemon {A, B} and the incoming save holds {C, D, E}:
    (3, 2, 101) >= (2, 2, 100) on every counter, so the candidate "dominates"
    and authorised the overwrite -- destroying A and B, which existed nowhere
    else.

    The counters are not even monotone: AnkimonDB.delete_pokemon runs
    ``DELETE FROM captured_pokemon WHERE individual_id = ?`` from the Pokemon
    details window, and the duplicate prune drops rows too.
    """
    protected = media / "_addons21_ankimon.db"
    _make_save(protected, pokemon=2, badges=2, history=100, ids="kept")
    before = protected.read_bytes()
    _make_save(media / "ankimon.db", pokemon=3, badges=2, history=101, ids="other")

    assert st._dominates(
        st.get_db_stats(media / "ankimon.db"), st.get_db_stats(protected)
    ), "fixture no longer reproduces the dominating-but-disjoint shape"

    monkeypatch.setattr(st, "askUser", lambda *a, **k: False)
    st.run_media_migration(MagicMock(), logger)

    assert protected.read_bytes() == before, (
        "a save holding none of the protected copy's Pokemon overwrote it"
    )
    # ...and the incoming save is preserved beside it, not instead of it.
    held = {
        (s["pokemon"], s["badges"], s["history"])
        for s in (st.get_db_stats(path) for path in _protected(media))
    }
    assert held == {(3, 2, 101)}


def test_equal_counters_do_not_mean_the_save_is_already_preserved(
    media, live_db, logger, monkeypatch
):
    """The same mistake wearing the other face. ``_preserve_diverged`` treated
    ``_progress_key(protected) == _progress_key(at_risk)`` as "already safe" and
    returned without protecting the bare save. Two saves can agree on all three
    counts and share not one row, so the bare copy -- the only name a media
    check can delete -- was left exposed."""
    _make_save(media / "_addons21_ankimon.db", pokemon=4, badges=2, history=30, ids="mine")
    _make_save(media / "ankimon.db", pokemon=4, badges=2, history=30, ids="theirs")
    monkeypatch.setattr(st, "askUser", lambda *a, **k: False)

    st.run_media_migration(MagicMock(), logger)

    copies = _protected(media)
    assert len(copies) == 1, "the equal-but-different bare save was left exposed"
    assert copies[0].read_bytes() == (media / "ankimon.db").read_bytes()


def test_two_devices_converge_on_one_name_for_one_save(tmp_path, live_db, logger, monkeypatch):
    """The protected name is derived from the file's bytes and nothing else, so
    two machines that receive the same save through media sync compute the same
    filename -- and dedupe against each other instead of each adding a copy."""
    names = []
    for device in ("A", "B"):
        folder = tmp_path / device / "collection.media"
        folder.mkdir(parents=True)
        shutil.copy2(live_db, folder / "ankimon.db")
        monkeypatch.setattr(st, "_media_dir", lambda folder=folder: folder)
        monkeypatch.setattr(st, "_migration_done", lambda: False)
        monkeypatch.setattr(st, "_mark_migration_done", lambda fingerprint: None)
        monkeypatch.setattr(st, "askUser", lambda *a, **k: False)

        st.run_media_migration(MagicMock(), logger)
        names.append([p.name for p in _protected(folder)])

    assert len(names[0]) == 1                   # each device protected the save...
    assert names[0] == names[1]                 # ...under one and the same name


def test_a_peers_content_addressed_copy_is_found_by_the_scan(
    media, live_db, logger, monkeypatch
):
    """A protected copy is itself a media file, so it syncs to the other
    devices. The scan's globs have to match the names this migration writes, or
    a peer's preserved save is invisible to the ranking AND to the settle
    fingerprint -- which would re-open the stale-settle hole the fingerprint
    exists to close."""
    peer = media / st._protected_copy_name("ankimon.db", "a" * st._DIGEST_CHARS)
    _make_save(peer, pokemon=42, badges=8, history=99)
    ask = MagicMock(return_value=False)
    monkeypatch.setattr(st, "askUser", ask)

    assert peer in st._media_candidate_paths(media, "ankimon.db")
    assert peer.name in st._media_fingerprint_entries(media, "ankimon.db")

    st.run_media_migration(MagicMock(), logger)

    ask.assert_called_once()                    # found, read and offered
    assert _protected(media) == [peer]          # and not copied again


def test_a_developer_content_addressed_copy_stays_in_its_own_partition(media):
    """``_ankimon_save_dev_<digest>.db`` spells no "ankimonDEV", and the normal
    partition's prefix is a prefix of the developer one -- so a substring test,
    or the wrong test order, hands developer saves to the normal scan."""
    digest = "b" * st._DIGEST_CHARS
    dev = media / st._protected_copy_name("ankimonDEV.db", digest)
    normal = media / st._protected_copy_name("ankimon.db", digest)

    assert st._target_db_for(dev) == "ankimonDEV.db"
    assert st._target_db_for(normal) == "ankimon.db"
    assert dev not in st._media_candidate_paths(media, "ankimon.db")
    assert normal not in st._media_candidate_paths(media, "ankimonDEV.db")


# ---------------------------------------------------------------------------
# An aborted probe is UNKNOWN, never zero
# ---------------------------------------------------------------------------
def _interrupt_counts(monkeypatch, only_when=lambda uri: True):
    """Make SQLite raise what an aborted statement raises, on the COUNT(*)s.

    This is the state the probe's progress handler puts the connection in when
    MIGRATION_PROBE_TIMEOUT expires mid-scan: ``sqlite3.OperationalError:
    interrupted``. Driven directly rather than by racing a real clock, so the
    abort lands on a specific statement every run.
    """
    real_connect = st.sqlite3.connect

    class _Interrupting:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, *args):
            if sql.strip().upper().startswith("SELECT COUNT"):
                raise st.sqlite3.OperationalError("interrupted")
            return self._conn.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(self._conn, name)

        def __setattr__(self, name, value):
            if name == "_conn":
                object.__setattr__(self, name, value)
            else:
                setattr(self._conn, name, value)

    # NOT named `uri`: get_db_stats calls connect(<string>, uri=True, ...), so a
    # first parameter of that name collides with the keyword and the connection
    # fails with a TypeError instead — which get_db_stats reports as None, for
    # entirely the wrong reason, turning every test below green against the bug.
    def _connect(database, *a, **k):
        conn = real_connect(database, *a, **k)
        return _Interrupting(conn) if only_when(str(database)) else conn

    monkeypatch.setattr(st.sqlite3, "connect", _connect)


def test_an_aborted_count_reads_as_unknown_not_empty(tmp_path, monkeypatch):
    """get_db_stats installs a progress handler so a COUNT(*) over a big save
    cannot outrun the 0.5 s migration budget, and documents the abort as
    surfacing "could not read this side". It did not: the per-query wrapper
    caught the OperationalError and returned its default, so a healthy save came
    back as {'pokemon': 0, 'badges': 0, 'history': 0} -- unknown rendered as
    empty, which is the one distinction this function exists to make. Those
    false zeroes then fed straight into _progress_key and _dominates."""
    save = _make_save(tmp_path / "ankimon.db", pokemon=40, badges=8, history=90)
    assert st.get_db_stats(save)["pokemon"] == 40        # readable to begin with

    _interrupt_counts(monkeypatch)

    assert st.get_db_stats(save) is None


def test_a_save_with_an_older_schema_still_reads(tmp_path):
    """The other half of the same change: failing closed on an ABORT must not
    turn ordinary schema drift into an unreadable save. A table this build has
    never heard of is missing, not interrupted."""
    save = _make_save(tmp_path / "ankimon.db", pokemon=4, badges=1, history=6)
    conn = sqlite3.connect(str(save))
    conn.execute("DROP TABLE badges")
    conn.execute("UPDATE config SET value = '' WHERE key = 'trainer.level'")
    conn.commit()
    conn.close()

    stats = st.get_db_stats(save)
    assert stats is not None
    assert (stats["pokemon"], stats["badges"], stats["history"]) == (4, 0, 6)
    assert stats["trainer_level"] == 0


def test_an_aborted_probe_of_the_local_save_neither_prompts_nor_settles(
    media, live_db, logger, monkeypatch
):
    """End to end, and the reason it matters. A local save whose COUNT(*)s are
    cut short read as (0, 0, 0), which every real save dominates -- so the
    migration offered to load a media copy over a save it had simply failed to
    finish reading, and then settled the profile on that comparison."""
    _make_save(media / "ankimon.db", pokemon=1)          # far BEHIND the local save
    marked = MagicMock()
    monkeypatch.setattr(st, "_mark_migration_done", marked)
    ask = MagicMock(return_value=False)
    monkeypatch.setattr(st, "askUser", ask)
    _interrupt_counts(monkeypatch, only_when=lambda uri: "collection.media" not in uri)

    st.run_media_migration(MagicMock(), logger)

    ask.assert_not_called()
    marked.assert_not_called()
    assert st.get_db_stats(live_db) is None              # unknown, for this pass


@pytest.mark.parametrize("shape", [
    {"ankimon.db": (3, 1, 2)},                          # identical to the local save
    {"ankimon.db": (99, 9, 99)},                        # strictly ahead
    {"ankimon.db": (1, 0, 0)},                          # strictly behind
    {"ankimon.db": (10, 0, 50), "_addons21_ankimon.db": (5, 3, 200)},       # diverged
    {"ankimon.db": (5, 3, 200), "_addons21_ankimon.db": (10, 0, 50)},       # the mirror
    {"ankimon.db": (4, 2, 30), "_addons21_ankimon.db": (4, 2, 30)},         # equal counts
    {"ankimon.db": (1, 9, 1), "_addons21_ankimon.db": (5, 3, 200),
     "_src_ankimon.db": (10, 0, 50)},                         # a third save
    {"ankimon.db": (3, 2, 101), "_addons21_ankimon.db": (2, 2, 100)},       # "dominates"
    {"ankimon.db": (10, 0, 50), "_addons21_ankimon.db": (5, 3, 200),
     "_src_ankimon.db": (2, 0, 10)},          # "dominates" the spare name
    {"_1908235722_ankimon.db": (7, 3, 9)},              # a pre-2024 legacy name
])
def test_no_file_already_in_the_media_folder_is_ever_modified(
    shape, media, live_db, logger, monkeypatch
):
    """The promise, as one property over every folder shape the migration meets.

    A migration that can only ADD files cannot lose a save, whatever it believes
    about which one is further along. Anything that reintroduces a write over an
    existing media file — a smarter ranking, a reused fixed name, a "this one is
    clearly superseded" shortcut — fails here rather than in someone's profile.
    """
    for index, (name, (pokemon, badges, history)) in enumerate(shape.items()):
        _make_save(media / name, pokemon=pokemon, badges=badges,
                   history=history, ids=f"save{index}")
    before = {p.name: p.read_bytes() for p in media.iterdir()}
    monkeypatch.setattr(st, "askUser", lambda *a, **k: False)
    monkeypatch.setattr(st, "showInfo", MagicMock())

    st.run_media_migration(MagicMock(), logger)

    for name, raw in before.items():
        assert (media / name).is_file(), f"{name} was deleted"
        assert (media / name).read_bytes() == raw, f"{name} was written over"
    assert logger.errors == []
