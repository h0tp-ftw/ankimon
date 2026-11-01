"""Tier-1 seam tests for the post-sync mobile detection hook (F29).

Drives ``setup_ankimon_sync_hooks`` -> ``on_sync_did_finish`` end to end with a
faked Anki/Qt runtime, a real ``AnkimonDB`` behind ``services.db``, and a fake
collection behind ``services.col``. Verifies the dual-DB detection/queue path,
watermark advance, and the auto-vs-manual resolution routing (the auto branch
reaches ``MobileBridge`` via the web-shell seam).
"""

import os
import sys
import types
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"

for _name in (
    "aqt",
    "aqt.qt",
    "aqt.utils",
    "aqt.gui_hooks",
    "aqt.operations",
    "aqt.theme",
    "aqt.sound",
    "aqt.webview",
    "aqt.main",
    "anki",
    "anki.hooks",
    "anki.collection",
    "anki.utils",
    "PyQt6",
    "PyQt6.QtGui",
    "PyQt6.QtWidgets",
    "PyQt6.QtCore",
    "PyQt6.QtWebChannel",
    "PyQt6.QtWebEngineWidgets",
):
    sys.modules.setdefault(_name, MagicMock())

for _pkg in (
    "Ankimon",
    "Ankimon.functions",
    "Ankimon.pyobj",
    "Ankimon.ankimon_items_web",
):
    _existing = sys.modules.get(_pkg)
    if _existing is None or not hasattr(_existing, "__path__"):
        _mod = types.ModuleType(_pkg)
        _mod.__path__ = [str(_SRC / _pkg.replace(".", "/"))]
        _mod.__package__ = _pkg
        sys.modules[_pkg] = _mod

if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

_USER_DIR = Path(tempfile.mkdtemp(prefix="ankimon_autoresolve_ut_"))
os.environ.setdefault("ANKIMON_USER_PATH", str(_USER_DIR))

from Ankimon.services import services  # noqa: E402
from Ankimon.pyobj.database_manager import AnkimonDB  # noqa: E402
from Ankimon.pyobj import ankimon_sync as asy  # noqa: E402


class _Logger:
    def log(self, *a, **k):
        pass

    def game_log(self, *a, **k):
        pass

    def log_and_showinfo(self, *a, **k):
        pass


class _Settings:
    def __init__(self, d=None):
        self.d = dict(d or {})

    def get(self, key, default=None):
        return self.d.get(key, default)

    def set(self, key, value):
        self.d[key] = value


class _FakeCol:
    """Stand-in for mw.col carrying the revlog rows the detector reads."""

    def __init__(self, review_ids):
        rows = [(rid, 1000 + rid, 3, 10000, 1) for rid in review_ids]
        maxid = max(review_ids, default=0)

        class _DB:
            def all(self, _q, watermark):
                return [r for r in rows if r[0] > watermark]

            def scalar(self, _q):
                return maxid

            def list(self, _q, *cids):
                return []

        self.db = _DB()


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """AnkimonDB + fake col on the seam, a captured sync-finish hook, stub deps."""
    from Ankimon import background_ops

    monkeypatch.setattr(
        background_ops,
        "call_when_anki_idle",
        lambda callback, reason, **kwargs: callback(),
    )
    db = AnkimonDB(_Logger(), db_path=str(tmp_path / "ankimon.db"))
    prev_db, prev_col = services.db, services.col
    services.db = db

    # Route the module-level user_path (dev-db probe) at this test's scratch dir.
    monkeypatch.setattr(asy, "user_path", tmp_path, raising=False)

    # Stub the sibling leaves the hook lazily imports so the heavy real modules
    # (menu_buttons pulls in markdown/Qt) stay out of the Qt-free Tier-1 venv.
    badge = types.ModuleType("Ankimon.menu_buttons")
    badge.update_mobile_badge = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "Ankimon.menu_buttons", badge)

    mock_bridge = MagicMock()
    mock_bridge.resolveAll.return_value = {
        "success": True,
        "resolved": 3,
        "xp_gained": 100,
        "cash_gained": 50,
        "caught_list": [{"name": "Pikachu"}],
    }
    shop_stub = types.ModuleType("Ankimon.ankimon_items_web.shop_obj")
    shop_stub.MobileBridge = lambda *a, **k: mock_bridge
    monkeypatch.setitem(sys.modules, "Ankimon.ankimon_items_web.shop_obj", shop_stub)

    tooltip = MagicMock()
    monkeypatch.setattr(asy, "tooltip", tooltip, raising=False)

    def build(review_ids, resolution_mode="manual", mobile_enabled=True):
        services.col = _FakeCol(review_ids)
        settings = _Settings(
            {
                "mobile.enabled": mobile_enabled,
                "mobile.resolution_mode": resolution_mode,
            }
        )
        # Capture through ankimon_sync's OWN gui_hooks reference: another test
        # module may have swapped sys.modules["aqt"] for a fresh MagicMock, so a
        # local ``from aqt import gui_hooks`` could resolve to a different object
        # than the one setup_ankimon_sync_hooks appends to.
        asy.gui_hooks.sync_did_finish = MagicMock()
        asy.setup_ankimon_sync_hooks(settings, _Logger())
        return asy.gui_hooks.sync_did_finish.append.call_args[0][0]

    try:
        yield db, build, tooltip, mock_bridge
    finally:
        services.db, services.col = prev_db, prev_col


def test_manual_mode_queues_and_notifies(wired):
    db, build, tooltip, mock_bridge = wired
    hook = build([1, 2, 3, 4, 5], resolution_mode="manual")
    hook()
    # Reviews were queued into the real DB and the watermark advanced.
    assert db.get_pending_mobile_count() == 5
    assert db.get_mobile_watermark() == 5
    # Manual mode does not auto-resolve.
    assert not mock_bridge.resolveAll.called
    tooltip.assert_called_with(
        "⚔ Mobile/web reviews synced: 5 in Normal! Open Ankimon → Mobile & Web Reviews to resolve."
    )


def test_auto_mode_resolves_via_bridge(wired):
    db, build, tooltip, mock_bridge = wired
    hook = build([1, 2, 3], resolution_mode="auto")
    hook()
    assert mock_bridge.resolveAll.called
    tooltip.assert_called_with(
        "⚔ Auto-resolved 3 mobile/web reviews! +100 XP, +50¥. Caught: Pikachu."
    )


def test_disabled_skips_detection(wired):
    db, build, tooltip, mock_bridge = wired
    hook = build([1, 2, 3], resolution_mode="auto", mobile_enabled=False)
    hook()
    assert db.get_pending_mobile_count() == 0
    assert not mock_bridge.resolveAll.called
    assert not tooltip.called


def test_no_new_reviews_no_notify(wired):
    db, build, tooltip, mock_bridge = wired
    # Watermark already at the max revlog id -> nothing new to queue.
    db.set_mobile_watermark(3)
    hook = build([1, 2, 3], resolution_mode="manual")
    hook()
    assert db.get_pending_mobile_count() == 0
    assert not tooltip.called


def test_setup_hooks_reload_safe(wired):
    """A second setup in the same session removes the first handlers before
    re-appending (F31), so on_sync_did_finish is never double-registered."""
    db, build, tooltip, mock_bridge = wired
    settings = _Settings(
        {
            "mobile.enabled": True,
            "mobile.resolution_mode": "manual",
        }
    )
    sdf = MagicMock()
    sws = MagicMock()
    asy.gui_hooks.sync_did_finish = sdf
    asy.gui_hooks.sync_will_start = sws
    setattr(services, asy._SYNC_HOOK_RECORD, ())  # start from a clean record

    asy.setup_ankimon_sync_hooks(settings, _Logger())
    asy.setup_ankimon_sync_hooks(settings, _Logger())

    # Two setups -> two appends, but the second removed the first's handler.
    assert sdf.append.call_count == 2
    assert sdf.remove.call_count == 1
    first_handler = sdf.append.call_args_list[0][0][0]
    assert sdf.remove.call_args[0][0] is first_handler


def test_mobile_detection_registers_with_shipped_defaults(wired):
    """Regression guard for the mobile-sync decoupling fix.

    Mobile reviews arrive via Anki's own AnkiWeb sync, independent of Ankimon's
    legacy ``misc.ankiweb_sync`` file-sync toggle (default False, never
    auto-enabled). Previously ``setup_ankimon_sync_hooks`` early-returned on that
    False flag, so ``on_sync_did_finish`` was never attached and a mid-session
    sync never turned phone reviews into battles. Every other test here forces
    ``ankiweb_sync=True`` and so masked the bug; this one pins the DEFAULT."""
    db, build, tooltip, mock_bridge = wired

    settings = _Settings(
        {
            "mobile.enabled": True,
            "mobile.resolution_mode": "manual",
        }
    )
    services.col = _FakeCol([101, 102, 103])
    db.set_mobile_watermark(0)

    sdf = MagicMock()
    asy.gui_hooks.sync_did_finish = sdf
    setattr(services, asy._SYNC_HOOK_RECORD, ())  # start from a clean record

    asy.setup_ankimon_sync_hooks(settings, _Logger())

    # The detection hook IS registered even though ankiweb_sync is False.
    assert sdf.append.called, (
        "on_sync_did_finish was not registered for a default-config user"
    )

    # Firing it (exactly what a real mid-session AnkiWeb sync does) queues the
    # reviews into pending battles — the behaviour a default user was missing.
    handler = sdf.append.call_args[0][0]
    handler()
    assert db.get_pending_mobile_count() == 3

    # Idempotent: a second sync (or the startup-pass/sync-hook overlap) does not
    # double-count — revlog_id is UNIQUE and the watermark has advanced.
    handler()
    assert db.get_pending_mobile_count() == 3


def test_sync_did_finish_applies_queue_cap(wired, monkeypatch):
    """on_sync_did_finish bounds each queueing pass by MOBILE_QUEUE_CAP the same
    way the startup pass does: keep the newest N, discard the oldest."""
    from Ankimon.functions import mobile_sync as ms

    db, build, tooltip, mock_bridge = wired
    monkeypatch.setattr(ms, "MOBILE_QUEUE_CAP", 2)
    hook = build([1, 2, 3, 4], resolution_mode="manual")
    hook()
    assert db.get_pending_mobile_count() == 2
    batch = db.get_next_pending_mobile_batch(limit=10)
    assert sorted(r["revlog_id"] for r in batch) == [3, 4]


def test_sync_did_finish_holds_lock_during_switch(wired, monkeypatch):
    """The dual-DB switch/queue pass runs under _mobile_sync_lock so it cannot
    interleave with an in-flight background mobile-resolve."""
    from Ankimon.functions import mobile_sync as ms

    db, build, tooltip, mock_bridge = wired
    observed = {}
    real_detect = ms.detect_mobile_reviews

    def spy(col, watermark, ids):
        observed["locked"] = ms._mobile_sync_lock.locked()
        return real_detect(col, watermark, ids)

    monkeypatch.setattr(ms, "detect_mobile_reviews", spy)
    hook = build([1, 2, 3], resolution_mode="manual")
    hook()
    assert observed.get("locked") is True


@pytest.fixture
def paused_mobile_worker():
    """Hold the real lock as a paused bulk replay would, with a failure deadline."""
    import threading
    from Ankimon.functions import mobile_sync as ms

    acquired = threading.Event()
    resume = threading.Event()
    expired = threading.Event()

    def worker():
        with ms._mobile_sync_lock:
            acquired.set()
            if not resume.wait(2):
                expired.set()

    thread = threading.Thread(target=worker)
    thread.start()
    assert acquired.wait(1)
    try:
        yield resume, expired
    finally:
        resume.set()
        thread.join(3)
        assert not thread.is_alive()


def test_sync_yields_to_paused_replay_then_retries(
    wired, monkeypatch, paused_mobile_worker
):
    """A paused replay cannot block the GUI event that resumes it (#996)."""
    from Ankimon import background_ops

    db, build, _, _ = wired
    scheduled = []
    monkeypatch.setattr(
        background_ops,
        "call_when_anki_idle",
        lambda callback, reason, **kwargs: scheduled.append((callback, kwargs)),
    )
    hook = build([1, 2, 3])
    hook()
    assert db.get_pending_mobile_count() == 0
    scheduled.pop(0)[0]()
    assert db.get_pending_mobile_count() == 0
    assert len(scheduled) == 1
    assert scheduled[0][1]["delay_ms"] == 100
    resume, expired = paused_mobile_worker
    assert not expired.is_set(), "GUI waited for the worker instead of yielding"
    resume.set()
    from Ankimon.functions import mobile_sync as ms

    # Synchronize with actual worker completion, not a guessed sleep.
    with ms._mobile_sync_lock:
        pass
    scheduled.pop(0)[0]()
    assert db.get_pending_mobile_count() == 3
    assert db.get_mobile_watermark() == 3
    assert scheduled == []


def test_gui_resolve_does_not_lease_database_while_worker_is_paused(
    paused_mobile_worker,
):
    from Ankimon.functions import mobile_sync as ms

    db = MagicMock()
    with pytest.raises(ms.MobileSyncBusy, match="Resume or stop"):
        ms.resolve_all(db, None, None, None, None, day_cutoff=0)
    db._get_connection.assert_not_called()
    assert not paused_mobile_worker[1].is_set()


def test_cap_warning_is_delivered_after_mobile_lock_release(wired, monkeypatch):
    from Ankimon.functions import mobile_sync as ms

    db, build, _, _ = wired
    monkeypatch.setattr(ms, "MOBILE_QUEUE_CAP", 2)
    observed = []

    def warn(self, level, message):
        acquired = ms._mobile_sync_lock.acquire(blocking=False)
        if acquired:
            ms._mobile_sync_lock.release()
        observed.append((acquired, db.db_path.name, db.get_pending_mobile_count()))

    monkeypatch.setattr(_Logger, "log_and_showinfo", warn)
    build([1, 2, 3, 4])()
    assert observed == [(True, "ankimon.db", 2)]


def test_mobile_resolve_exception_releases_transaction_and_lock(wired, monkeypatch):
    from Ankimon.functions import mobile_sync as ms
    from Ankimon import utils

    db, _, _, _ = wired

    def fail(**kwargs):
        assert ms._mobile_sync_lock.locked()
        db.set_mobile_watermark(99)
        raise RuntimeError("simulation failed")

    monkeypatch.setattr(ms, "_run_mobile_battles_impl", fail)
    with pytest.raises(RuntimeError, match="simulation failed"):
        ms.resolve_all(db, None, None, None, None, day_cutoff=0)
    assert not ms._mobile_sync_lock.locked()
    assert not db._get_connection()._disable_commit
    assert not utils.in_bulk_resolve
    assert db.get_mobile_watermark() == 0


def test_deferred_mobile_detection_drops_changed_profile(wired, monkeypatch):
    from Ankimon import background_ops

    db, build, _, _ = wired
    scheduled = []
    monkeypatch.setattr(
        background_ops,
        "call_when_anki_idle",
        lambda callback, reason, **kwargs: scheduled.append(callback),
    )
    build([1, 2, 3])()
    services.col = _FakeCol([100, 101])
    scheduled.pop()()
    assert db.get_pending_mobile_count() == 0
    assert db.get_mobile_watermark() == 0


@pytest.mark.parametrize("day_cutoff", [None, 0, 123456])
def test_mobile_worker_never_reads_anki_collection(wired, monkeypatch, day_cutoff, anki_host):
    from concurrent.futures import ThreadPoolExecutor
    from Ankimon.functions import mobile_sync as ms

    db, _, _, _ = wired

    class NoCollectionAccess:
        @property
        def col(self):
            raise AssertionError("collection-free worker read mw.col")

    monkeypatch.setattr(sys.modules["aqt"], "mw", NoCollectionAccess())
    # Empty next-battle queue is enough to exercise scheduler lookup on the
    # actual worker entry path, without replacing the engine implementation.
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(
            ms.resolve_next,
            "",
            db,
            None,
            None,
            None,
            None,
            day_cutoff=day_cutoff,
        ).result(timeout=2)
    assert result == {"done": True}


def _open_mobile_profile(monkeypatch, review_ids):
    """Run the real profile initializer and sync hook against the real test DB."""
    from test_profile_hooks import _exec_profile_hooks, _fresh_gui_hooks

    profile = _exec_profile_hooks(monkeypatch, _fresh_gui_hooks())
    monkeypatch.setitem(sys.modules, "Ankimon.pyobj.ankimon_sync", asy)
    monkeypatch.setattr(
        profile, "setup_ankimon_sync_hooks", asy.setup_ankimon_sync_hooks
    )
    monkeypatch.setattr(asy, "mw", profile.mw)
    asy.gui_hooks.sync_did_finish = MagicMock()
    profile.settings_obj = _Settings(
        {"mobile.enabled": True, "mobile.resolution_mode": "auto"}
    )
    profile.mw.taskman = types.SimpleNamespace(run_in_background=lambda *args: None)
    profile.mw.col = services.col = _FakeCol(review_ids)
    profile._on_profile_did_open(False)()
    return profile, asy.gui_hooks.sync_did_finish.append.call_args[0][0]


@pytest.mark.parametrize("watermark", [0, 100])
def test_profile_baseline_precedes_first_auto_sync(wired, monkeypatch, watermark):
    db, _, _, bridge = wired
    db.set_mobile_watermark(watermark)
    profile, sync_hook = _open_mobile_profile(monkeypatch, [100])
    assert db.get_mobile_watermark() == 100
    assert db.get_pending_mobile_count() == 0
    # Anki downloads reviews only after profile_did_open has returned.
    profile.mw.col.db = _FakeCol([100, 101, 102]).db
    sync_hook()
    assert [r["revlog_id"] for r in db.get_next_pending_mobile_batch(10)] == [101, 102]
    bridge.resolveAll.assert_called_once()


@pytest.mark.parametrize("sync_callback_first", [False, True])
@pytest.mark.parametrize("initial_state", ["first_run", "existing", "import"])
def test_contended_profile_initializes_once_before_either_sync_callback(
    wired,
    monkeypatch,
    paused_mobile_worker,
    sync_callback_first,
    initial_state,
):
    from Ankimon import background_ops
    from Ankimon.functions import mobile_sync as ms

    db, _, _, bridge = wired
    clear_session = MagicMock(wraps=ms.clear_desktop_session)
    monkeypatch.setattr(ms, "clear_desktop_session", clear_session)
    if initial_state == "existing":
        db.set_mobile_watermark(100)
    elif initial_state == "import":
        db.set_mobile_watermark(900)
        with db._get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('import_rebase_pending', '1')"
            )
    scheduled = []
    monkeypatch.setattr(
        background_ops,
        "call_when_anki_idle",
        lambda callback, reason, **kwargs: scheduled.append(callback),
    )
    profile, sync_hook = _open_mobile_profile(monkeypatch, [100])
    assert len(scheduled) == 1
    profile.mw.col.db = _FakeCol([100, 101, 102]).db
    sync_hook()
    assert len(scheduled) == 2
    assert not paused_mobile_worker[1].is_set()
    paused_mobile_worker[0].set()
    with ms._mobile_sync_lock:
        pass
    first = scheduled.pop(1 if sync_callback_first else 0)
    first()
    scheduled.pop()()
    # Once for initialization, then once for each completed detection pass.
    # A repeated initializer would clear the new session a fourth time.
    assert clear_session.call_count == 3
    assert [r["revlog_id"] for r in db.get_next_pending_mobile_batch(10)] == [101, 102]
    assert db.get_mobile_watermark() == 102
    assert (
        db.execute(
            "SELECT value FROM metadata WHERE key='import_rebase_pending'"
        ).fetchone()
        is None
    )
    bridge.resolveAll.assert_called_once()
    assert not scheduled


@pytest.mark.parametrize("stale_reason", ["profile", "reload"])
def test_contended_profile_initializer_drops_stale_context(
    wired,
    monkeypatch,
    paused_mobile_worker,
    stale_reason,
):
    from Ankimon import background_ops
    from Ankimon.functions import mobile_sync as ms

    db, _, _, bridge = wired
    scheduled = []
    monkeypatch.setattr(
        background_ops,
        "call_when_anki_idle",
        lambda callback, reason, **kwargs: scheduled.append(callback),
    )
    _open_mobile_profile(monkeypatch, [100])
    if stale_reason == "reload":
        generation = getattr(services, "_background_generation", 0)
        monkeypatch.setattr(
            services, "_background_generation", generation + 1, raising=False
        )
    else:
        services.col = _FakeCol([900])
    paused_mobile_worker[0].set()
    with ms._mobile_sync_lock:
        pass
    scheduled.pop()()
    assert db.get_mobile_watermark() == 0
    assert db.get_pending_mobile_count() == 0
    bridge.resolveAll.assert_not_called()


def test_contended_initialization_keeps_desktop_answers_during_wait(
    wired,
    monkeypatch,
    paused_mobile_worker,
):
    from Ankimon import background_ops
    from Ankimon.functions import mobile_sync as ms

    db, _, _, bridge = wired
    scheduled = []
    monkeypatch.setattr(
        background_ops,
        "call_when_anki_idle",
        lambda callback, reason, **kwargs: scheduled.append(callback),
    )
    profile, _ = _open_mobile_profile(monkeypatch, [100])
    profile.mw.col.db = _FakeCol([100, 101, 102]).db
    # The fallback records the card ID when Anki has not exposed its revlog ID.
    # It can only be resolved after the new row appears in the collection.
    ms.record_desktop_review(None, card_id=555)
    profile.mw.col.db.list = lambda query, *cids: [101] if 555 in cids else []
    paused_mobile_worker[0].set()
    with ms._mobile_sync_lock:
        pass
    scheduled.pop()()
    assert [r["revlog_id"] for r in db.get_next_pending_mobile_batch(10)] == [102]
    assert db.get_mobile_watermark() == 102
    bridge.resolveAll.assert_called_once()
