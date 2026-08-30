"""Regression guards for the defects found in the first cut of this branch.

Each of these was written RED against head 0582ffd0 -- reproducing a real
failure -- and is kept green by the fixes in this commit. They are grouped by
what they protect rather than by module, because they share one theme: the
migration must never treat an ABSENCE or an UNKNOWN as a resolution.

  * a media-sync hook that fires on failure is not proof a download arrived;
  * a save that will not open is unknown, not empty, on either side;
  * developer and normal saves never share a protected name;
  * export never writes over the live save, and never carries a credential.

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
