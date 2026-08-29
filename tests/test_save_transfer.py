"""Tests for manual save Export/Import and the one-shot media migration that
replaced the removed AnkiWeb file-sync.

The three things worth pinning here are the ones the removed feature got wrong:

* nothing destructive happens without a VERIFIED source and a SUCCESSFUL backup;
* a read failure must never be reported as "this save is empty", because that is
  what invites a user to overwrite the save that actually holds their progress;
* the migration must find every historical media name — including the
  underscore-prefixed legacy ones, which are globbed rather than reconstructed,
  since the old prefix came from ``Path(__file__).parents[2].name`` and is
  ``addons21`` in an install, ``src`` in a checkout and a numeric package id in
  some real profiles.

Tier-1 house pattern: stub ``aqt``/``anki``/``PyQt6`` in ``sys.modules`` so the
real add-on modules import Qt-free, then drive the real code.
"""

import os
import sys
import atexit
import types
import shutil
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"

for _name in (
    "aqt", "aqt.qt", "aqt.utils", "aqt.gui_hooks", "aqt.operations",
    "aqt.theme", "aqt.sound", "aqt.webview", "aqt.main",
    "anki", "anki.hooks", "anki.collection", "anki.utils",
    "PyQt6", "PyQt6.QtGui", "PyQt6.QtWidgets", "PyQt6.QtCore",
    "PyQt6.QtWebChannel", "PyQt6.QtWebEngineWidgets",
):
    sys.modules.setdefault(_name, MagicMock())

for _pkg in ("Ankimon", "Ankimon.functions", "Ankimon.pyobj", "Ankimon.ankimon_items_web"):
    _existing = sys.modules.get(_pkg)
    if _existing is None or not hasattr(_existing, "__path__"):
        _mod = types.ModuleType(_pkg)
        _mod.__path__ = [str(_SRC / _pkg.replace(".", "/"))]
        _mod.__package__ = _pkg
        sys.modules[_pkg] = _mod

if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

_USER_DIR = Path(tempfile.mkdtemp(prefix="ankimon_transfer_ut_"))
os.environ.setdefault("ANKIMON_USER_PATH", str(_USER_DIR))
atexit.register(shutil.rmtree, _USER_DIR, ignore_errors=True)

import aqt.utils as _aqt_utils  # noqa: E402
for _n in ("showInfo", "showWarning", "tooltip", "askUser"):
    if not hasattr(_aqt_utils, _n):
        setattr(_aqt_utils, _n, MagicMock())

from Ankimon.services import services  # noqa: E402
import Ankimon.pyobj.save_transfer as st  # noqa: E402


class _Logger:
    def __init__(self):
        self.errors = []

    def log(self, level, msg, *a, **k):
        if level == "error":
            self.errors.append(msg)

    def game_log(self, *a, **k): pass
    def log_and_showinfo(self, *a, **k): pass


def _make_save(path: Path, *, pokemon=0, badges=0, history=0,
               name="Ash", level=1, cash=0, badge_flag=1):
    """A minimally realistic Ankimon save: enough shape that the integrity gate
    accepts it and get_db_stats can read every field it reports."""
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE captured_pokemon (individual_id TEXT PRIMARY KEY, is_main INTEGER, data TEXT);
        CREATE TABLE items (name TEXT PRIMARY KEY, quantity INTEGER);
        CREATE TABLE badges (badge_id INTEGER PRIMARY KEY, achieved);
        CREATE TABLE pokemon_history (individual_id TEXT PRIMARY KEY);
        CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT);
        """
    )
    for i in range(pokemon):
        conn.execute("INSERT INTO captured_pokemon VALUES (?, 0, '{}')", (f"uuid-{i}",))
    for i in range(badges):
        conn.execute("INSERT INTO badges VALUES (?, ?)", (i, badge_flag))
    for i in range(history):
        conn.execute("INSERT INTO pokemon_history VALUES (?)", (f"hist-{i}",))
    conn.execute("INSERT INTO items VALUES ('potion', 7)")
    conn.executemany(
        "INSERT INTO config VALUES (?, ?)",
        [("trainer.name", name), ("trainer.level", str(level)), ("trainer.cash", str(cash))],
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def logger():
    return _Logger()


@pytest.fixture
def live_db(tmp_path, monkeypatch):
    """Point the module's notion of the ACTIVE save at a real file."""
    active = _make_save(tmp_path / "ankimon.db", pokemon=3, badges=1, history=2, cash=500)
    monkeypatch.setattr(st, "_active_db_path", lambda: active)
    return active


# --------------------------------------------------------------------------
# get_db_stats: an unreadable save is None, never a zeroed one
# --------------------------------------------------------------------------
def test_stats_reads_a_real_save(tmp_path):
    p = _make_save(tmp_path / "s.db", pokemon=4, badges=2, history=6, name="Red", level=9, cash=1234)
    s = st.get_db_stats(p)
    assert s["pokemon"] == 4 and s["badges"] == 2 and s["history"] == 6
    assert s["trainer_name"] == "Red" and s["trainer_level"] == 9 and s["trainer_cash"] == 1234
    assert s["items"] == 7


@pytest.mark.parametrize("flag", [1, "true", "True"])
def test_stats_counts_every_achieved_badge_spelling(tmp_path, flag):
    """badges.achieved has been written as 1, 'true' and 'True' by different
    generations of the badge code; counting only the integer form makes a
    healthy save look like it has no badges."""
    p = _make_save(tmp_path / f"s{flag}.db", badges=3, badge_flag=flag)
    assert st.get_db_stats(p)["badges"] == 3


def test_stats_is_none_for_missing_and_garbage(tmp_path):
    assert st.get_db_stats(tmp_path / "nope.db") is None
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"definitely not sqlite" * 40)
    assert st.get_db_stats(junk) is None


def test_unreadable_side_renders_as_unreadable_not_empty(tmp_path):
    """The removed dialog showed a locked save as 'Level: 1, Cash: 0'. That is
    the text that talks a user into overwriting good data."""
    text = st._format_stats(None)
    assert "could not read" in text.lower()
    assert "Cash: 0" not in text


def test_progress_key_ignores_cash_and_level(tmp_path):
    """Progress ordering must use monotone counters only: spending cash or a
    recomputed level must never make a save look older than it is."""
    rich = st.get_db_stats(_make_save(tmp_path / "a.db", pokemon=2, cash=9999, level=50))
    poor = st.get_db_stats(_make_save(tmp_path / "b.db", pokemon=2, cash=0, level=1))
    assert st._progress_key(rich) == st._progress_key(poor)

    ahead = st.get_db_stats(_make_save(tmp_path / "c.db", pokemon=3, cash=0, level=1))
    assert st._progress_key(ahead) > st._progress_key(rich)


def test_progress_key_ranks_unreadable_below_everything(tmp_path):
    empty = st.get_db_stats(_make_save(tmp_path / "e.db"))
    assert st._progress_key(None) < st._progress_key(empty)


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------
def test_export_round_trips_through_sqlite_backup(tmp_path, live_db, monkeypatch):
    dest = tmp_path / "out" / "save.db"
    dest.parent.mkdir()
    monkeypatch.setattr(st.QFileDialog, "getSaveFileName", lambda *a, **k: (str(dest), ""))
    monkeypatch.setattr(st, "showInfo", MagicMock())

    assert st.export_save() is True
    assert dest.is_file()
    exported = st.get_db_stats(dest)
    assert exported["pokemon"] == 3 and exported["trainer_cash"] == 500


def test_export_cancelled_writes_nothing(tmp_path, live_db, monkeypatch):
    monkeypatch.setattr(st.QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""))
    assert st.export_save() is False
    assert not list(tmp_path.glob("*.db.*"))


def test_export_leaves_no_temp_behind_when_it_fails(tmp_path, live_db, monkeypatch):
    """A failed export must not leave a partial file the user could later import
    over a good save."""
    dest = tmp_path / "out" / "save.db"
    dest.parent.mkdir()
    monkeypatch.setattr(st.QFileDialog, "getSaveFileName", lambda *a, **k: (str(dest), ""))
    monkeypatch.setattr(st, "showWarning", MagicMock())

    def _boom(src, tmp):
        raise OSError("disk full")

    monkeypatch.setattr(st, "_sqlite_backup", _boom)
    assert st.export_save() is False
    assert not dest.exists()
    assert list(dest.parent.iterdir()) == []


def test_export_discards_a_file_that_fails_verification(tmp_path, live_db, monkeypatch):
    dest = tmp_path / "out" / "save.db"
    dest.parent.mkdir()
    monkeypatch.setattr(st.QFileDialog, "getSaveFileName", lambda *a, **k: (str(dest), ""))
    monkeypatch.setattr(st, "showWarning", MagicMock())
    monkeypatch.setattr(
        "Ankimon.pyobj.ankimon_sync._verify_sqlite_integrity", lambda p: False
    )
    assert st.export_save() is False
    assert not dest.exists()
    assert list(dest.parent.iterdir()) == []


# --------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------
def _stub_sync(monkeypatch, *, backup_ok=True):
    """Stand in for the AnkimonDataSync primitives the import path leans on,
    recording the order in which they were called."""
    calls = []

    class _Sync:
        def _backup_before_overwrite(self, name):
            calls.append(("backup", name))
            return backup_ok

        def _atomic_replace(self, src, dest):
            calls.append(("replace", str(src), str(dest)))
            shutil.copy2(src, dest)

    monkeypatch.setattr("Ankimon.pyobj.ankimon_sync.get_ankimon_sync", lambda: _Sync())
    return calls


def test_import_refuses_a_file_that_is_not_an_ankimon_save(tmp_path, live_db, monkeypatch):
    bad = tmp_path / "notasave.db"
    bad.write_bytes(b"nope" * 200)
    monkeypatch.setattr(st.QFileDialog, "getOpenFileName", lambda *a, **k: (str(bad), ""))
    warn = MagicMock()
    monkeypatch.setattr(st, "showWarning", warn)
    ask = MagicMock()
    monkeypatch.setattr(st, "askUser", ask)

    assert st.import_save() is False
    ask.assert_not_called()          # never even asks about a bad file
    assert live_db.read_bytes()[:16] == b"SQLite format 3\x00"
    assert st.get_db_stats(live_db)["pokemon"] == 3


def test_import_refuses_when_the_safety_backup_fails(tmp_path, live_db, monkeypatch):
    incoming = _make_save(tmp_path / "incoming.db", pokemon=99)
    monkeypatch.setattr(st.QFileDialog, "getOpenFileName", lambda *a, **k: (str(incoming), ""))
    monkeypatch.setattr(st, "askUser", lambda *a, **k: True)
    monkeypatch.setattr(st, "showWarning", MagicMock())
    monkeypatch.setattr(st, "showInfo", MagicMock())
    closed = MagicMock()
    monkeypatch.setattr(st, "close_anki", closed)
    calls = _stub_sync(monkeypatch, backup_ok=False)

    assert st.import_save() is False
    assert [c[0] for c in calls] == ["backup"]      # refused BEFORE replacing
    assert st.get_db_stats(live_db)["pokemon"] == 3
    closed.assert_not_called()


def test_import_declined_by_user_changes_nothing(tmp_path, live_db, monkeypatch):
    incoming = _make_save(tmp_path / "incoming.db", pokemon=99)
    monkeypatch.setattr(st.QFileDialog, "getOpenFileName", lambda *a, **k: (str(incoming), ""))
    monkeypatch.setattr(st, "askUser", lambda *a, **k: False)
    calls = _stub_sync(monkeypatch)

    assert st.import_save() is False
    assert calls == []
    assert st.get_db_stats(live_db)["pokemon"] == 3


def test_import_backs_up_before_replacing_then_closes_anki(tmp_path, live_db, monkeypatch):
    incoming = _make_save(tmp_path / "incoming.db", pokemon=99, badges=4)
    monkeypatch.setattr(st.QFileDialog, "getOpenFileName", lambda *a, **k: (str(incoming), ""))
    monkeypatch.setattr(st, "askUser", lambda *a, **k: True)
    monkeypatch.setattr(st, "showInfo", MagicMock())
    closed = MagicMock()
    monkeypatch.setattr(st, "close_anki", closed)
    calls = _stub_sync(monkeypatch)

    assert st.import_save() is True
    assert [c[0] for c in calls] == ["backup", "replace"]   # order is the safety
    assert st.get_db_stats(live_db)["pokemon"] == 99
    closed.assert_called_once()


def test_import_refuses_the_file_it_is_already_using(tmp_path, live_db, monkeypatch):
    monkeypatch.setattr(st.QFileDialog, "getOpenFileName", lambda *a, **k: (str(live_db), ""))
    monkeypatch.setattr(st, "showWarning", MagicMock())
    calls = _stub_sync(monkeypatch)
    assert st.import_save() is False
    assert calls == []


# --------------------------------------------------------------------------
# One-shot migration off the removed feature
# --------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def run_deferred_inline(monkeypatch):
    """The rescue is deferred by one event-loop turn via mw.progress.single_shot
    so it never runs on the profile_did_open stack. ``mw`` is a MagicMock here,
    which would swallow the callback, so run it inline instead."""
    monkeypatch.setattr(
        st.mw.progress, "single_shot",
        lambda ms, fn, requires_collection=True: fn(),
    )


@pytest.fixture
def media(tmp_path, monkeypatch):
    d = tmp_path / "collection.media"
    d.mkdir()
    monkeypatch.setattr(st, "_media_dir", lambda: d)
    flag = {}
    monkeypatch.setattr(st, "_migration_done", lambda: bool(flag.get("done")))
    monkeypatch.setattr(st, "_mark_migration_done", lambda: flag.__setitem__("done", True))
    return d


def test_migration_protects_the_bare_media_save(media, live_db, logger, monkeypatch):
    """collection.media/ankimon.db has no leading underscore, so Anki's Check
    Media lists it as unused and 'Delete Unused Files' deletes it — and that
    deletion propagates to every other device."""
    _make_save(media / "ankimon.db", pokemon=1)
    monkeypatch.setattr(st, "askUser", lambda *a, **k: False)

    st.run_media_migration(MagicMock(), logger)

    protected = media / st.MEDIA_SAVE_NAME
    assert protected.is_file()
    assert protected.name.startswith("_")
    assert st.get_db_stats(protected)["pokemon"] == 1
    assert (media / "ankimon.db").is_file()   # nothing is deleted
    assert logger.errors == []


def test_migration_finds_underscore_legacy_names_by_glob(media, live_db, logger, monkeypatch):
    """The legacy prefix cannot be reconstructed after the fact — it was built
    from Path(__file__).parents[2].name, which differs between an install, a
    checkout and a numeric package id — so it must be globbed."""
    _make_save(media / "_1908235722_ankimon.db", pokemon=7, badges=3, history=9)
    monkeypatch.setattr(st, "askUser", lambda *a, **k: False)

    st.run_media_migration(MagicMock(), logger)

    assert st.get_db_stats(media / st.MEDIA_SAVE_NAME)["pokemon"] == 7


def test_migration_ignores_a_foreign_file_with_a_matching_name(media, live_db, logger, monkeypatch):
    (media / "ankimon.db").write_bytes(b"some other addon's file" * 40)
    monkeypatch.setattr(st, "askUser", lambda *a, **k: False)

    st.run_media_migration(MagicMock(), logger)

    assert not (media / st.MEDIA_SAVE_NAME).exists()
    assert (media / "ankimon.db").read_bytes().startswith(b"some other")


def test_migration_offers_rescue_only_when_media_is_further_along(media, live_db, logger, monkeypatch):
    # live_db has pokemon=3, badges=1, history=2
    _make_save(media / "ankimon.db", pokemon=1, badges=0, history=0)
    ask = MagicMock(return_value=False)
    monkeypatch.setattr(st, "askUser", ask)

    st.run_media_migration(MagicMock(), logger)
    ask.assert_not_called()          # local is ahead — do not nag


def test_migration_rescue_replaces_the_save_when_accepted(media, live_db, logger, monkeypatch):
    _make_save(media / "ankimon.db", pokemon=42, badges=8, history=99)
    monkeypatch.setattr(st, "askUser", lambda *a, **k: True)
    monkeypatch.setattr(st, "showInfo", MagicMock())
    monkeypatch.setattr(st, "close_anki", MagicMock())
    calls = _stub_sync(monkeypatch)

    st.run_media_migration(MagicMock(), logger)

    assert [c[0] for c in calls] == ["backup", "replace"]
    assert st.get_db_stats(live_db)["pokemon"] == 42


def test_migration_declined_is_remembered_and_asks_only_once(media, live_db, logger, monkeypatch):
    _make_save(media / "ankimon.db", pokemon=42, badges=8, history=99)
    ask = MagicMock(return_value=False)
    monkeypatch.setattr(st, "askUser", ask)

    st.run_media_migration(MagicMock(), logger)
    st.run_media_migration(MagicMock(), logger)

    assert ask.call_count == 1
    assert st.get_db_stats(live_db)["pokemon"] == 3   # untouched


def test_migration_does_not_burn_its_one_shot_before_media_exists(tmp_path, live_db, logger, monkeypatch):
    """profile_did_open fires before Anki's auto-sync-on-open, so a brand-new
    device can reach this with no media folder yet. Marking the migration done
    there would mean the peer's file, arriving minutes later, is never seen."""
    missing = tmp_path / "no_such_media"
    monkeypatch.setattr(st, "_media_dir", lambda: missing)
    marked = MagicMock()
    monkeypatch.setattr(st, "_migration_done", lambda: False)
    monkeypatch.setattr(st, "_mark_migration_done", marked)

    st.run_media_migration(MagicMock(), logger)
    marked.assert_not_called()


def test_migration_never_raises_out_of_profile_open(media, live_db, logger, monkeypatch):
    """It runs during profile open; it must never be able to stop Ankimon
    loading, whatever it trips over."""
    def _boom(*a, **k):
        raise RuntimeError("media folder exploded")

    monkeypatch.setattr(st, "_find_media_saves", _boom)
    st.run_media_migration(MagicMock(), logger)   # must not raise
    assert logger.errors and "exploded" in logger.errors[0]


def test_migration_retries_the_rescue_if_the_replace_failed(media, live_db, logger, monkeypatch):
    """A refused backup or a persisting file lock must not burn the one-shot
    flag — that would leave the user with no offered route back to the only copy
    of their progress."""
    _make_save(media / "ankimon.db", pokemon=42, badges=8, history=99)
    ask = MagicMock(return_value=True)
    monkeypatch.setattr(st, "askUser", ask)
    monkeypatch.setattr(st, "showWarning", MagicMock())
    _stub_sync(monkeypatch, backup_ok=False)          # backup refuses

    st.run_media_migration(MagicMock(), logger)
    st.run_media_migration(MagicMock(), logger)

    assert ask.call_count == 2                        # offered again
    assert st.get_db_stats(live_db)["pokemon"] == 3   # and nothing was replaced


def test_migration_settles_after_a_successful_rescue(media, live_db, logger, monkeypatch):
    """The run after a successful rescue must see the two saves level, skip the
    prompt, and only then mark itself done."""
    _make_save(media / "ankimon.db", pokemon=42, badges=8, history=99)
    ask = MagicMock(return_value=True)
    monkeypatch.setattr(st, "askUser", ask)
    monkeypatch.setattr(st, "showInfo", MagicMock())
    monkeypatch.setattr(st, "close_anki", MagicMock())
    _stub_sync(monkeypatch)

    st.run_media_migration(MagicMock(), logger)       # rescues, "closes" Anki
    assert st.get_db_stats(live_db)["pokemon"] == 42
    st.run_media_migration(MagicMock(), logger)       # the next boot

    assert ask.call_count == 1                        # not asked a second time


# --------------------------------------------------------------------------
# The boot-ordering trap (the bug the first cut of this migration shipped)
# --------------------------------------------------------------------------
def test_empty_media_at_profile_open_does_not_burn_the_one_shot(media, live_db, logger, monkeypatch):
    """THE regression guard. Anki fires profile_did_open at aqt/main.py:568, one
    line BEFORE maybe_auto_sync_on_open_close at :569. On a brand-new second
    device collection.media EXISTS (Anki creates it with the profile) but is
    empty — the peer's save arrives from the sync that starts moments later.

    Concluding "nothing here, mark this profile done" at that moment burns the
    flag permanently and the arriving save is never protected or offered, for
    exactly the two-device user the rescue exists to serve.
    """
    marked = MagicMock()
    monkeypatch.setattr(st, "_mark_migration_done", marked)
    monkeypatch.setattr(st, "_media_sync_completed_this_session", False)

    st.run_media_migration(MagicMock(), logger)     # the profile_did_open scan

    marked.assert_not_called()


def test_peer_save_is_picked_up_on_the_post_sync_pass(media, live_db, logger, monkeypatch):
    """...and the post-media-sync pass is what actually finds it."""
    monkeypatch.setattr(st, "_media_sync_completed_this_session", False)
    ask = MagicMock(return_value=False)
    monkeypatch.setattr(st, "askUser", ask)

    st.run_media_migration(MagicMock(), logger)                 # boot: nothing yet
    assert not (media / st.MEDIA_SAVE_NAME).exists()
    ask.assert_not_called()

    _make_save(media / "ankimon.db", pokemon=42, badges=8, history=99)   # download lands
    st.run_media_migration(MagicMock(), logger, after_media_sync=True)

    assert (media / st.MEDIA_SAVE_NAME).is_file()               # protected
    ask.assert_called_once()                                    # and offered


def test_empty_media_after_a_completed_sync_does_settle(media, live_db, logger, monkeypatch):
    """The flag must still be reachable: once a media sync has completed and the
    folder is genuinely empty, stop scanning on every launch."""
    monkeypatch.setattr(st, "_media_sync_completed_this_session", False)
    marked = MagicMock()
    monkeypatch.setattr(st, "_mark_migration_done", marked)

    st.run_media_migration(MagicMock(), logger, after_media_sync=True)

    marked.assert_called_once()


# --------------------------------------------------------------------------
# Developer-mode partitioning, and not trusting an unreadable protected copy
# --------------------------------------------------------------------------
def test_dev_and_normal_saves_are_not_ranked_against_each_other(media, tmp_path, logger, monkeypatch):
    """A developer save with more test captures must not be crowned 'best',
    become the single protected copy, and be offered over the real save."""
    active = _make_save(tmp_path / "ankimon.db", pokemon=3)
    monkeypatch.setattr(st, "_active_db_path", lambda: active)
    _make_save(media / "ankimon.db", pokemon=1)
    _make_save(media / "ankimonDEV.db", pokemon=500, badges=50, history=900)
    ask = MagicMock(return_value=False)
    monkeypatch.setattr(st, "askUser", ask)

    st.run_media_migration(MagicMock(), logger, after_media_sync=True)

    # The dev save is not what got preserved, and no rescue was offered from it.
    assert st.get_db_stats(media / st.MEDIA_SAVE_NAME)["pokemon"] == 1
    ask.assert_not_called()


def test_a_readable_but_staler_candidate_does_not_replace_the_protected_copy(media, live_db, logger, monkeypatch):
    """_progress_key floors an unreadable save below everything, so without an
    explicit guard a readable-but-stale candidate would overwrite a protected
    copy that merely failed to open this once."""
    _make_save(media / st.MEDIA_SAVE_NAME, pokemon=80, badges=9, history=120)
    _make_save(media / "ankimon.db", pokemon=1)
    monkeypatch.setattr(st, "askUser", lambda *a, **k: False)

    st.run_media_migration(MagicMock(), logger, after_media_sync=True)

    assert st.get_db_stats(media / st.MEDIA_SAVE_NAME)["pokemon"] == 80


def test_rescue_is_deferred_off_the_profile_open_stack(media, live_db, logger, monkeypatch):
    """close_anki() -> mw.close() starts unloadProfileAndExit(); running that
    from inside profile_did_open would tear the collection down and then return
    into loadProfile, which proceeds straight to maybe_auto_sync_on_open_close."""
    _make_save(media / "ankimon.db", pokemon=42, badges=8, history=99)
    monkeypatch.setattr(st, "askUser", lambda *a, **k: True)
    monkeypatch.setattr(st, "showInfo", MagicMock())
    monkeypatch.setattr(st, "close_anki", MagicMock())
    _stub_sync(monkeypatch)

    scheduled = []
    monkeypatch.setattr(
        st.mw.progress, "single_shot",
        lambda ms, fn, requires_collection=True: scheduled.append(fn),
    )

    st.run_media_migration(MagicMock(), logger, after_media_sync=True)

    assert len(scheduled) == 1                       # deferred, not called inline
    assert st.get_db_stats(live_db)["pokemon"] == 3  # nothing replaced yet
    scheduled[0]()                                   # next event-loop turn
    assert st.get_db_stats(live_db)["pokemon"] == 42
