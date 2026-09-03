"""Tier-1 seam tests for the Mobile & Web Reviews sync engine (F14/F25/F29).

These exercise the mobile-review backend through the service seam (``services``
+ ``events``) rather than exp's direct ``aqt.mw.*`` access:

* the deferred ``AnkimonDB`` mobile accessors (watermark / queue / history /
  cross-DB resolution sync);
* the desktop-session bookkeeping + revlog detection + post-sync queueing
  pipeline;
* the pure helpers (``_parse_cards_per_round`` / ``_normalize_ev_yield``);
* ``menu_buttons.update_mobile_badge`` as a guarded no-op before F36 builds the
  menu action.

The engine imports are stdlib-only at module load, so this runs Qt-free in the
Tier-1 venv; ``aqt`` / ``anki`` / ``PyQt6`` are stubbed so any lazy import of a
sibling module resolves without a real Anki/Qt runtime.
"""

import os
import sys
import types
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# --- Tier-1 bootstrap: real addon modules, faked Anki/Qt runtime ------------
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

# Point the DB layer's user_path at a scratch dir before it is imported.
_USER_DIR = Path(tempfile.mkdtemp(prefix="ankimon_mobile_ut_"))
os.environ.setdefault("ANKIMON_USER_PATH", str(_USER_DIR))

from Ankimon.services import services  # noqa: E402
from Ankimon.functions import mobile_sync as ms  # noqa: E402
from Ankimon.pyobj.database_manager import AnkimonDB  # noqa: E402


class _Logger:
    def log(self, *a, **k): pass
    def game_log(self, *a, **k): pass
    def log_and_showinfo(self, *a, **k): pass


class _Settings:
    def __init__(self, d=None):
        self.d = dict(d or {})

    def get(self, key, default=None):
        return self.d.get(key, default)

    def set(self, key, value):
        self.d[key] = value


@pytest.fixture
def mobile_db(tmp_path):
    """Fresh AnkimonDB wired into the service seam; state reset afterwards."""
    db = AnkimonDB(_Logger(), db_path=str(tmp_path / "ankimon.db"))
    prev_db, prev_col = services.db, services.col
    services.db = db
    services.col = None
    ms.clear_desktop_session()
    try:
        yield db, tmp_path
    finally:
        ms.clear_desktop_session()
        services.db, services.col = prev_db, prev_col
        try:
            db.close()
        except Exception:
            pass


# --- DB mobile accessors ----------------------------------------------------

def test_watermark_get_set_defaults_to_zero(mobile_db):
    db, _ = mobile_db
    assert db.get_mobile_watermark() == 0
    db.set_mobile_watermark(123456)
    assert db.get_mobile_watermark() == 123456


def test_queue_dedup_and_pending_count(mobile_db):
    db, _ = mobile_db
    reviews = [
        {"id": 101, "cid": 1001, "ease": 3, "time": 15000, "type": 1},
        {"id": 102, "cid": 1002, "ease": 2, "time": 20000, "type": 2},
    ]
    assert db.queue_mobile_battles(reviews) == 2
    assert db.get_pending_mobile_count() == 2
    # revlog_id is UNIQUE -> a re-queue of the same ids inserts nothing.
    assert db.queue_mobile_battles(reviews) == 0
    assert db.get_pending_mobile_count() == 2


def test_next_batch_ordering_and_mark_resolved(mobile_db):
    db, _ = mobile_db
    db.queue_mobile_battles([
        {"id": 205, "cid": 1, "ease": 3, "time": 1, "type": 1},
        {"id": 101, "cid": 2, "ease": 3, "time": 1, "type": 1},
    ])
    # oldest-first == lowest revlog_id first
    batch = db.get_next_pending_mobile_batch(limit=1)
    assert len(batch) == 1
    assert batch[0]["revlog_id"] == 101

    db.mark_mobile_battle_resolved(batch[0]["queue_id"])
    assert db.get_pending_mobile_count() == 1
    remaining = db.get_next_pending_mobile_batch(limit=10)
    assert [r["revlog_id"] for r in remaining] == [205]


def test_get_next_batch_empty(mobile_db):
    db, _ = mobile_db
    assert db.get_next_pending_mobile_batch(limit=5) == []


# --- Cross-DB resolution sync ----------------------------------------------

def test_cross_db_resolution_sync(mobile_db, monkeypatch):
    db, tmp_path = mobile_db
    # Route the "other DB" lookup at this test's scratch dir. Patch the module
    # globals the method actually reads (another test may have loaded a second
    # copy of database_manager into sys.modules, so patch via the class method).
    monkeypatch.setitem(
        AnkimonDB.sync_resolutions_to_other_db.__globals__, "user_path", tmp_path
    )

    other = tmp_path / "ankimonDEV.db"
    import sqlite3
    conn = sqlite3.connect(str(other))
    conn.execute(
        """CREATE TABLE pending_mobile_battles (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               revlog_id INTEGER UNIQUE NOT NULL, card_id INTEGER NOT NULL,
               ease INTEGER NOT NULL, review_time INTEGER NOT NULL,
               review_type INTEGER NOT NULL, queued_at INTEGER NOT NULL,
               resolved INTEGER NOT NULL DEFAULT 0, resolved_at INTEGER)"""
    )
    conn.execute(
        "INSERT INTO pending_mobile_battles (revlog_id, card_id, ease, review_time, review_type, queued_at) "
        "VALUES (777, 1, 3, 1, 1, 1)"
    )
    conn.commit()
    conn.close()

    db.sync_resolutions_to_other_db([777], resolved_at=999)

    conn = sqlite3.connect(str(other))
    row = conn.execute("SELECT resolved, resolved_at FROM pending_mobile_battles WHERE revlog_id=777").fetchone()
    conn.close()
    assert row == (1, 999)


# --- History table ----------------------------------------------------------

def test_history_add_get_clear_and_none_safety(mobile_db):
    db, _ = mobile_db
    assert db.get_mobile_history() == []
    # companion_* None and a missing xp_gained must not crash the insert.
    ok = db.add_mobile_history_entry({
        "timestamp": 10, "enemy_id": 25, "enemy_name": "Pikachu",
        "enemy_level": 5, "enemy_shiny": True, "companion_name": None,
        "companion_level": None, "outcome": "caught",
    })
    assert ok is True
    hist = db.get_mobile_history()
    assert len(hist) == 1
    assert hist[0]["enemy_name"] == "Pikachu"
    assert hist[0]["enemy_shiny"] is True
    assert hist[0]["xp_gained"] == 0
    assert db.clear_mobile_history() is True
    assert db.get_mobile_history() == []


def test_history_trims_to_500(mobile_db):
    db, _ = mobile_db
    entries = [
        {"timestamp": i, "enemy_id": i, "enemy_name": f"e{i}", "enemy_level": 1,
         "enemy_shiny": False, "outcome": "defeated", "xp_gained": 1}
        for i in range(520)
    ]
    assert db.add_mobile_history_entries_batch(entries) is True
    hist = db.get_mobile_history(limit=1000)
    assert len(hist) == 500
    # Newest kept, oldest trimmed.
    assert hist[0]["timestamp"] == 519
    assert min(h["timestamp"] for h in hist) == 20


# --- Desktop-session bookkeeping + watermark ------------------------------

def test_record_desktop_review_durably_records_without_advancing_watermark(mobile_db):
    db, _ = mobile_db
    prev_settings = services.settings
    services.settings = _Settings({"mobile.enabled": True, "misc.ankiweb_sync": True})
    try:
        db.set_mobile_watermark(1000)
        ms.record_desktop_review(1200)
        ms.record_desktop_review(1500)
        # The watermark is NOT advanced: advancing it would permanently skip an
        # older, not-yet-synced mobile review whose revlog id is below a desktop
        # review's timestamp.
        assert db.get_mobile_watermark() == 1000
        # Ids are tracked via the in-memory session set + the durable store.
        assert ms.get_desktop_session_revlog_ids() == frozenset({1200, 1500})
        # Durable: they survive an in-memory session clear (a mid-session restart).
        ms.clear_desktop_session()
        assert ms.get_desktop_session_revlog_ids() == frozenset({1200, 1500})
        # Advancing the watermark past an id prunes it from the durable store (it
        # is already excluded by the `id > watermark` detection filter).
        db.set_mobile_watermark(1300)
        assert ms.get_desktop_session_revlog_ids() == frozenset({1500})
    finally:
        services.settings = prev_settings
        db.clear_desktop_processed_reviews()
        ms.clear_desktop_session()


def test_record_desktop_review_durable_write_follows_mobile_enabled(mobile_db):
    db, _ = mobile_db
    prev_settings = services.settings
    # Detection runs whenever mobile.enabled — it is driven by Anki's native
    # AnkiWeb sync and is DECOUPLED from the legacy misc.ankiweb_sync file-sync
    # toggle — so the durable de-dupe record must be written in that same config.
    # Gating it on misc.ankiweb_sync (off by default) while detection ignored the
    # flag meant a restart lost the in-memory set and re-queued already-battled
    # desktop reviews as phantom mobile battles (double XP).
    services.settings = _Settings({"mobile.enabled": True, "misc.ankiweb_sync": False})
    try:
        ms.record_desktop_review(2000)
        assert 2000 in ms.get_desktop_session_revlog_ids()
        # Durable write happens even with misc.ankiweb_sync off (the fix):
        assert db.get_desktop_processed_revlog_ids() == {2000}
        # It survives an in-memory clear (a mid-session restart):
        ms.clear_desktop_session()
        assert 2000 in ms.get_desktop_session_revlog_ids()
    finally:
        services.settings = prev_settings
        db.clear_desktop_processed_reviews()
        ms.clear_desktop_session()


def test_record_desktop_review_skips_durable_write_when_mobile_disabled(mobile_db):
    db, _ = mobile_db
    prev_settings = services.settings
    # With mobile disabled, detection never runs, so the durable record (an fsync)
    # is pointless and skipped; the in-memory session set still de-dupes.
    services.settings = _Settings({"mobile.enabled": False, "misc.ankiweb_sync": False})
    try:
        ms.record_desktop_review(2000)
        assert 2000 in ms.get_desktop_session_revlog_ids()
        assert db.get_desktop_processed_revlog_ids() == set()
    finally:
        services.settings = prev_settings
        ms.clear_desktop_session()


def test_get_desktop_session_resolves_card_ids_via_col(mobile_db):
    db, _ = mobile_db
    ms.record_desktop_review(0, card_id=42)   # revlog_id falsy -> only card recorded

    class _Col:
        class db:
            @staticmethod
            def list(q, *cids):
                return [9001] if 42 in cids else []

    ids = ms.get_desktop_session_revlog_ids(_Col())
    assert 9001 in ids


# --- detect / process pipeline ---------------------------------------------

class _FakeCol:
    """Minimal stand-in for mw.col with just the revlog queries the engine uses."""

    def __init__(self, rows):
        self._rows = rows  # list of (id, cid, ease, time, type)
        outer = self

        class _DB:
            def all(self, _q, watermark):
                return [r for r in outer._rows if r[0] > watermark]

            def scalar(self, _q):
                return max((r[0] for r in outer._rows), default=0)

        self.db = _DB()


def test_detect_mobile_reviews_filters_watermark_and_session():
    col = _FakeCol([(10, 1, 3, 100, 1), (20, 2, 2, 200, 1), (30, 3, 4, 300, 0)])
    result = ms.detect_mobile_reviews(col, watermark_ms=5, desktop_revlog_ids=frozenset({20}))
    # > watermark AND not in the desktop session set.
    assert [r["id"] for r in result] == [10, 30]


def test_process_mobile_reviews_queues_and_advances_watermark(mobile_db):
    db, _ = mobile_db
    col = _FakeCol([(11, 1, 3, 100, 1), (22, 2, 2, 200, 1), (33, 3, 4, 300, 0)])
    queued = ms.process_mobile_reviews_after_sync(col, db, _Settings({"mobile.enabled": True}), _Logger())
    assert queued == 3
    assert db.get_pending_mobile_count() == 3
    assert db.get_mobile_watermark() == 33


def test_process_respects_mobile_disabled(mobile_db):
    db, _ = mobile_db
    col = _FakeCol([(11, 1, 3, 100, 1)])
    assert ms.process_mobile_reviews_after_sync(col, db, _Settings({"mobile.enabled": False}), _Logger()) == 0
    assert db.get_pending_mobile_count() == 0


def test_process_applies_queue_cap(mobile_db, monkeypatch):
    db, _ = mobile_db
    monkeypatch.setattr(ms, "MOBILE_QUEUE_CAP", 2)
    col = _FakeCol([(1, 1, 3, 1, 1), (2, 2, 3, 1, 1), (3, 3, 3, 1, 1)])
    queued = ms.process_mobile_reviews_after_sync(col, db, _Settings({"mobile.enabled": True}), _Logger())
    # cap keeps the most recent 2 (highest ids).
    assert queued == 2
    batch = db.get_next_pending_mobile_batch(limit=10)
    assert sorted(r["revlog_id"] for r in batch) == [2, 3]


# --- Pure helpers -----------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (4, 4),
    ("3", 3),
    ("1-3", 2),
    # cards_per_round must never be 0 — a range whose average truncates to 0
    # (e.g. "0-0" / "0-1") or a plain 0/negative would ZeroDivide at the
    # mobile-sync entry point; every branch clamps to >= 1.
    ("0-0", 1),
    ("0-1", 1),
    (0, 1),
    (-5, 1),
    (None, 2),
])
def test_parse_cards_per_round(value, expected):
    settings = _Settings({"battle.cards_per_round": value}) if value is not None else None
    assert ms._parse_cards_per_round(settings)[0] == expected


@pytest.mark.parametrize("earned,earner,share_id,expected", [
    (100, "A", "B", (50, 50)),      # even split
    (101, "A", "B", (51, 50)),      # earner keeps the odd remainder, no XP lost
    (100, "A", "A", (100, 0)),      # can't share with yourself
    (100, "A", None, (100, 0)),     # XP-Share not configured
    (0, "A", "B", (0, 0)),          # nothing to split
    (100, "A", "b", (50, 50)),      # distinct ids -> split applies
])
def test_xp_share_split(earned, earner, share_id, expected):
    assert ms._xp_share_split(earned, earner, share_id) == expected


def test_commit_replay_defeat_applies_xp_share(monkeypatch):
    """XP-Share parity on the MANUAL replay-resolve path: commit_replay_outcome
    ('defeat', ...) must split the battling companion's XP 50/50 with the
    configured trainer.xp_share target, exactly like the bulk-resolve commit
    block. Without the split this path grants the companion 100% and the
    XP-Share target nothing (finding: mobile_sync commit_replay_outcome)."""
    class _S:
        def __init__(self, d): self._d = dict(d)
        def get(self, k, default=None): return self._d.get(k, default)
        def set(self, k, v): self._d[k] = v

    # Spy on the attribution seam so we can see who got how much XP.
    calls = []
    monkeypatch.setattr(
        ms, "_attribute_xp_and_evs_to_companion",
        lambda cid, xp, evs, settings_obj, battles_fought=1, db=None, logger=None: calls.append((str(cid), xp)),
    )
    # Patch on the imported module object (not a dotted string path, which is
    # import-order fragile across the full suite).
    import Ankimon.business as _biz
    monkeypatch.setattr(_biz, "calculate_cp_from_dict", lambda d: 10)

    settings = _S({"trainer.xp_share": "TARGET"})
    db = MagicMock()
    db.get_pending_mobile_count.return_value = 0
    enemy = MagicMock()
    outcome_data = {
        "enemy_pokemon": enemy,
        "battle_xp": 100,
        "total_xp": 100,
        "accumulated_evs": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
        "total_trainer_xp": 0,
        "gained_cash": 0,
        "companion_id": "COMP",
        "companion_name": "Comp",
        "companion_level": 5,
        "review_ids": [],
        "companion_fainted": False,
    }

    res = ms.commit_replay_outcome("defeat", outcome_data, db, settings, None, None)
    assert res.get("success") is True
    # Companion keeps half; the XP-Share target receives the other half.
    assert ("COMP", 50) in calls
    assert ("TARGET", 50) in calls


def test_commit_replay_defeat_no_share_when_unset(monkeypatch):
    """With XP-Share unset the manual replay-defeat path grants the companion the
    full battle XP and makes no second attribution call."""
    class _S:
        def __init__(self, d): self._d = dict(d)
        def get(self, k, default=None): return self._d.get(k, default)
        def set(self, k, v): self._d[k] = v

    calls = []
    monkeypatch.setattr(
        ms, "_attribute_xp_and_evs_to_companion",
        lambda cid, xp, evs, settings_obj, battles_fought=1, db=None, logger=None: calls.append((str(cid), xp)),
    )
    import Ankimon.business as _biz
    monkeypatch.setattr(_biz, "calculate_cp_from_dict", lambda d: 10)

    settings = _S({})  # no trainer.xp_share
    db = MagicMock()
    db.get_pending_mobile_count.return_value = 0
    outcome_data = {
        "enemy_pokemon": MagicMock(),
        "battle_xp": 100, "total_xp": 100,
        "accumulated_evs": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
        "total_trainer_xp": 0, "gained_cash": 0,
        "companion_id": "COMP", "companion_name": "Comp", "companion_level": 5,
        "review_ids": [], "companion_fainted": False,
    }
    res = ms.commit_replay_outcome("defeat", outcome_data, db, settings, None, None)
    assert res.get("success") is True
    assert calls == [("COMP", 100)]


def test_normalize_ev_yield_renames_keys():
    assert ms._normalize_ev_yield({"attack": 4, "speed": 2, "hp": 1}) == {"atk": 4, "spe": 2, "hp": 1}
    assert ms._normalize_ev_yield({}) == {}


def test_answercard_after_records_desktop_review(monkeypatch):
    """F29 de-dupe: answerCard_after must record the just-resolved review's
    revlog id + card id into the mobile-sync exclusion set, so the next
    detect pass does not re-queue it as a mobile battle (double XP/catches)."""
    # card_hooks pulls the heavy singletons module (markdown/Qt) transitively;
    # stub it with just the two names the module binds at import.
    stub = types.ModuleType("Ankimon.singletons")
    stub.ankimon_tracker_obj = MagicMock()
    stub.reviewer_obj = MagicMock()
    monkeypatch.setitem(sys.modules, "Ankimon.singletons", stub)
    monkeypatch.delitem(sys.modules, "Ankimon.card_hooks", raising=False)

    import Ankimon.card_hooks as ch

    ms.clear_desktop_session()

    class _Card:
        id = 42

    class _DB:
        def scalar(self, _sql, cid):
            return 9999 if cid == 42 else 0

    class _Sched:
        def answerButtons(self, _card):
            return 4

    rev = types.SimpleNamespace(
        mw=types.SimpleNamespace(col=types.SimpleNamespace(db=_DB(), sched=_Sched()))
    )

    try:
        ch.answerCard_after(rev, _Card(), ease=4)
        ids = ms.get_desktop_session_revlog_ids()
        assert 9999 in ids
    finally:
        ms.clear_desktop_session()


def test_commit_replay_mobile_cash_cap(monkeypatch):
    """Verifies that commit_replay_outcome caps cash at 400 per day."""
    class _S:
        def __init__(self, d): self._d = dict(d)
        def get(self, k, default=None): return self._d.get(k, default)
        def set(self, k, v): self._d[k] = v

    import Ankimon.business as _biz
    monkeypatch.setattr(_biz, "calculate_cp_from_dict", lambda d: 10)

    from datetime import date
    today_str = str(date.today())

    # Mock the companion attribution call so it doesn't try to query the mock DB
    monkeypatch.setattr(
        ms, "_attribute_xp_and_evs_to_companion",
        lambda *args, **kwargs: None
    )

    settings = _S({
        "trainer.mobile_cash_earned_today": 380,
        "trainer.last_mobile_cash_reward_date": today_str,
        "trainer.cash_reward_interval": 5,
        "trainer.cash_reward_amount": 50,
        "trainer.mobile_reviews_resolved_since_payout": 0,
    })
    db = MagicMock()
    db.get_pending_mobile_count.return_value = 0
    outcome_data = {
        "enemy_pokemon": MagicMock(),
        "battle_xp": 100, "total_xp": 100,
        "accumulated_evs": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
        "total_trainer_xp": 0, "gained_cash": 0,
        "companion_id": "COMP", "companion_name": "Comp", "companion_level": 5,
        "review_ids": [1, 2, 3, 4, 5], "companion_fainted": False,
    }

    res = ms.commit_replay_outcome("defeat", outcome_data, db, settings, None, None)
    assert res.get("success") is True
    assert res.get("cash_gained") == 20


def test_run_mobile_battles_no_companions_or_main_pokemon(mobile_db, monkeypatch):
    """Verify that run_mobile_battles returns failure rather than crashing when no companion or main Pokémon is available."""
    db, _ = mobile_db
    
    # Mock services.db.get_all_pokemon_ids inside load_collected_pokemon_ids
    monkeypatch.setattr(db, "get_all_pokemon_ids", lambda: [])
    
    settings = _Settings({"mobile.inactive_companions": []})
    
    # Mode all
    res_all = ms.run_mobile_battles(
        reviews=[{"id": 1, "ease": 3}],
        commit=True,
        db=db,
        settings_obj=settings,
        tracker=None,
        trainer_card=None,
        main_pokemon=None
    )
    assert res_all.get("success") is False
    assert "No active companion or main Pokémon" in res_all.get("error")

    # Mode next (manual replay)
    db.execute = MagicMock()
    db.execute().fetchall.return_value = [(1, 100, 10, 3, 1000, 1, 12345)]
    res_next = ms.run_mobile_battles(
        reviews=None,
        commit=True,
        db=db,
        settings_obj=settings,
        tracker=None,
        trainer_card=None,
        main_pokemon=None,
        mode="next"
    )
    assert res_next.get("success") is False
    assert "No active companion or main Pokémon" in res_next.get("error")


def test_make_safe_clone_does_not_mask_stats_property(monkeypatch):
    """Verify load_active_team_clones clones safely without copying stats to __dict__, which would mask the dynamic stats property."""
    import sys
    import importlib
    sys.modules.pop("Ankimon.pyobj.pokemon_obj", None)
    importlib.invalidate_caches()
    import Ankimon.pyobj.pokemon_obj
    importlib.reload(Ankimon.pyobj.pokemon_obj)
    PokemonObject = Ankimon.pyobj.pokemon_obj.PokemonObject

    # Now patch calc_stat on the fresh, real class
    monkeypatch.setattr(PokemonObject, "calc_stat", lambda stat, val, level, iv, ev, nature: 10 + ev)
    
    p = PokemonObject(
        type=["Electric"], name="Pikachu", id=25, shiny=False, level=5,
        ability="Run Away", gender="M", growth_rate="Medium", captured_date=None,
        tier="Normal", individual_id="PIKA"
    )
    
    clones = ms.load_active_team_clones(None, _Settings(), p)
    p_clone = clones[0]
    assert "stats" not in p_clone.__dict__
    
    # Changing EV should change stats property dynamically
    old_hp_stat = p_clone.stats["hp"]
    p_clone.ev["hp"] = 252
    assert p_clone.stats["hp"] > old_hp_stat


def test_base_stats_validator_rejects_impossible_values():
    from Ankimon.functions.pokedex_functions import is_valid_base_stats

    valid = {"hp": 35, "atk": 55, "def": 40, "spa": 50, "spd": 50, "spe": 90}
    assert is_valid_base_stats(valid)

    for invalid_value in (True, -1, float("nan"), float("inf"), "35", None):
        candidate = valid.copy()
        candidate["hp"] = invalid_value
        assert not is_valid_base_stats(candidate)

    partial = valid.copy()
    partial.pop("spe")
    assert not is_valid_base_stats(partial)


def test_diagnostics_uses_shared_base_stats_validator(mobile_db, monkeypatch):
    db, _ = mobile_db
    malformed = {
        "individual_id": "BAD-STATS",
        "name": "pikachu",
        "base_stats": {
            "hp": "35", "atk": "55", "def": "40",
            "spa": "50", "spd": "50", "spe": "90",
        },
    }
    db.save_pokemon(malformed)

    import importlib
    monkeypatch.delitem(sys.modules, "Ankimon.pyobj.db_diagnostics", raising=False)
    diagnostics = importlib.import_module("Ankimon.pyobj.db_diagnostics")
    cursor = db._get_connection().cursor()

    assert diagnostics._count_invalid_base_stats(db, cursor) == 1

    malformed["base_stats"] = {
        "hp": 35, "atk": 55, "def": 40, "spa": 50, "spd": 50, "spe": 90,
    }
    db.save_pokemon(malformed)
    assert diagnostics._count_invalid_base_stats(db, cursor) == 0


def test_load_active_team_clones_normalizes_malformed_ivs(mobile_db):
    db, _ = mobile_db
    pokemon_data = {
        "individual_id": "BAD-IV",
        "name": "pikachu",
        "id": 25,
        "shiny": False,
        "level": 5,
        "ability": "Static",
        "type": ["Electric"],
        "gender": "M",
        "growth_rate": "medium-fast",
        "captured_date": None,
        "tier": "Normal",
        "base_stats": {
            "hp": 35, "atk": 55, "def": 40, "spa": 50, "spd": 50, "spe": 90,
        },
        "ev": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
        "iv": {
            "hp": None,
            "atk": "unknown",
            "def": -4,
            "spa": 99,
            "spd": "12",
            "spe": 4.8,
        },
    }
    db.save_pokemon(pokemon_data)
    db.save_team([{"individual_id": "BAD-IV"}])

    clones = ms.load_active_team_clones(db, _Settings(), None)

    assert len(clones) == 1
    assert clones[0].iv == {
        "hp": 15, "atk": 15, "def": 0, "spa": 31, "spd": 12, "spe": 4,
    }


def test_attribute_xp_and_evs_defaults_missing_iv_to_15_and_ev_to_0(mobile_db, monkeypatch):
    """Verify that _attribute_xp_and_evs_to_companion defaults missing IVs to 15 and EVs to 0 instead of 0 for IVs."""
    db, _ = mobile_db
    pkmndata = {
        "individual_id": "TEST", "name": "Pikachu", "id": 25, "level": 5, "xp": 0,
        "base_stats": {"hp": 35, "atk": 55, "def": 40, "spa": 50, "spd": 50, "spe": 90},
        "growth_rate": "medium-fast",
    }
    db.save_pokemon(pkmndata)
    
    import sys
    import importlib
    sys.modules.pop("Ankimon.pyobj.pokemon_obj", None)
    importlib.invalidate_caches()
    import Ankimon.pyobj.pokemon_obj
    importlib.reload(Ankimon.pyobj.pokemon_obj)
    PokemonObject = Ankimon.pyobj.pokemon_obj.PokemonObject

    monkeypatch.setattr(PokemonObject, "calc_stat", lambda *args, **kwargs: 10)
    
    ms._attribute_xp_and_evs_to_companion("TEST", 10, {}, _Settings(), db=db)
    
    updated = db.get_pokemon("TEST")
    assert updated["iv"] == {"hp": 15, "atk": 15, "def": 15, "spa": 15, "spd": 15, "spe": 15}
    assert updated["ev"] == {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0}




# --- deferred move-replacement prompts (off the GUI thread) ------------------
#
# _attribute_xp_and_evs_to_companion runs on a QueryOp worker thread on every
# real mobile sync, and a QDialog cannot be built or exec()'d there. The guard
# that recognised this used to just skip the prompt, which silently threw the
# learned move away: the companion kept its old four moves with no tooltip, no
# log line and nothing queued. The decision is now parked and replayed on the
# GUI thread instead.

import threading as _threading


@pytest.fixture(autouse=True)
def _drain_pending_move_learns():
    """Never leak a parked decision into the next test."""
    with ms._pending_move_learns_lock:
        del ms._pending_move_learns[:]
    yield
    with ms._pending_move_learns_lock:
        del ms._pending_move_learns[:]


def _run_off_thread(fn, *args, **kwargs):
    box = {}

    def _target():
        try:
            box["value"] = fn(*args, **kwargs)
        except BaseException as exc:  # surfaced on the calling thread below
            box["error"] = exc

    # daemon so a deadlocked fn cannot keep the interpreter alive at exit —
    # the join(timeout) below already gave up on it, and a live non-daemon
    # thread would hang the whole suite on shutdown anyway.
    worker = _threading.Thread(target=_target, daemon=True)
    worker.start()
    worker.join(timeout=30)
    assert not worker.is_alive(), "worker thread hung"
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _full_moveset_row(individual_id="OFFTHREAD"):
    return {
        "individual_id": individual_id,
        "name": "Pikachu",
        "id": 25,
        "level": 5,
        "xp": 0,
        "attacks": ["tackle", "growl", "quick-attack", "thunder-shock"],
        "base_stats": {"hp": 35, "atk": 55, "def": 40, "spa": 50, "spd": 50, "spe": 90},
        "growth_rate": "medium-fast",
    }


def _full_moveset_companion(db, individual_id="OFFTHREAD"):
    pkmndata = _full_moveset_row(individual_id)
    db.save_pokemon(pkmndata)
    return pkmndata


class _FakeCompanionDB:
    """Just the two accessors _attribute_xp_and_evs_to_companion touches."""

    def __init__(self, *rows):
        self.rows = {row["individual_id"]: dict(row) for row in rows}

    def get_pokemon(self, individual_id):
        row = self.rows.get(individual_id)
        return dict(row) if row else None

    def save_pokemon(self, pkmndata):
        self.rows[pkmndata["individual_id"]] = dict(pkmndata)


class _FakeCompanionSingleton:
    def __init__(self, individual_id):
        self.individual_id = individual_id
        self.attacks = []
        self.xp = 0
        self.level = 1
        self.ev = {}
        self.friendship = 0
        self.pokemon_defeated = 0

    def invalidate_cp_cache(self):
        pass


def test_off_main_thread_move_learn_is_queued_not_dropped(monkeypatch):
    """The regression: off the GUI thread the learned move used to vanish.

    The production path this stands in for: resolve_next (mode="next", which
    unlike resolve_all never sets utils.in_bulk_resolve) -> run_mobile_battles'
    XP-Share grant, when the XP Share holder IS the main Pokemon — that is the
    one call reaching this branch with is_active True and in_bulk False, and it
    runs on a QueryOp worker thread.

    Driven against a fake DB and a pinned level-up table so it stays hermetic —
    several other files in this suite leave stubs for the pokedex/pokemon
    modules in ``sys.modules``, and the point here is the thread guard, not the
    XP curve.
    """
    from Ankimon import utils as _utils

    # The real submodule object mobile_sync's function-local
    # ``from .pokemon_functions import ...`` resolves — NOT
    # ``Ankimon.functions.pokemon_functions`` as an attribute of the package,
    # which other test files in this suite replace with a MagicMock.
    _pf = sys.modules["Ankimon.functions.pokemon_functions"]
    monkeypatch.setattr(
        _pf, "find_experience_for_level", lambda *a, **k: 50, raising=False
    )
    monkeypatch.setattr(
        _pf,
        "get_levelup_move_for_pokemon",
        lambda name, level: ["thunderbolt"],
        raising=False,
    )
    monkeypatch.setattr(
        services, "main_pokemon", _FakeCompanionSingleton("OFFTHREAD"), raising=False
    )
    # This is about the GUI-thread guard, not bulk mode — and in_bulk_resolve is
    # module-level state a failed resolve_all elsewhere in the suite can leave
    # set, which would suppress the prompt for an unrelated reason.
    monkeypatch.setattr(_utils, "in_bulk_resolve", False, raising=False)
    # Assert on the queue itself, not on whatever the scheduler manages to do
    # with it in a stubbed-Qt test environment.
    monkeypatch.setattr(ms, "_schedule_move_learn_flush", lambda **kw: None)

    db = _FakeCompanionDB(_full_moveset_row("OFFTHREAD"))

    _run_off_thread(
        ms._attribute_xp_and_evs_to_companion,
        "OFFTHREAD",
        100,
        {},
        _Settings(),
        db=db,
    )

    with ms._pending_move_learns_lock:
        parked = list(ms._pending_move_learns)

    assert [(p["individual_id"], p["new_attack"]) for p in parked] == [
        ("OFFTHREAD", "thunderbolt")
    ]
    # The level-up itself still landed...
    assert db.rows["OFFTHREAD"]["level"] == 6
    # ...and the existing four moves are untouched until the player chooses.
    assert db.rows["OFFTHREAD"]["attacks"] == [
        "tackle", "growl", "quick-attack", "thunder-shock",
    ]


def test_flush_landing_mid_attribution_cannot_lose_the_move(monkeypatch):
    """A queued decision must not be drained before its own row is written.

    The queue is process-wide and every flush drains all of it, but each
    attribution only schedules its flush after its own ``save_pokemon()``. A
    grant earlier in the same batch can therefore have a ``run_on_main``
    callback already in flight when the worker reaches the next grant — and
    that callback lands on the GUI thread whenever Anki gets round to it,
    including in the window between this grant queuing its decision and
    writing its row. Draining there means the flush saves the swap and this
    grant's in-flight ``save_pokemon()`` immediately overwrites it with the
    pre-swap snapshot: the move is gone, with the queue empty and nothing
    logged. The decision is held locally until the row is written instead.

    The interleave is forced deterministically (no second thread, no sleeps)
    by flushing from inside ``save_pokemon`` — exactly the moment that is
    unsafe.
    """
    from Ankimon import utils as _utils

    # Same submodule object mobile_sync's function-local import resolves; the
    # explicit import keeps this test runnable on its own (``-k`` a single
    # name), not just after whichever earlier test happened to pull it in.
    import Ankimon.functions.pokemon_functions  # noqa: F401

    _pf = sys.modules["Ankimon.functions.pokemon_functions"]
    monkeypatch.setattr(
        _pf, "find_experience_for_level", lambda *a, **k: 50, raising=False
    )
    monkeypatch.setattr(
        _pf,
        "get_levelup_move_for_pokemon",
        lambda name, level: ["thunderbolt"],
        raising=False,
    )
    monkeypatch.setattr(
        services, "main_pokemon", _FakeCompanionSingleton("RACE"), raising=False
    )
    monkeypatch.setattr(_utils, "in_bulk_resolve", False, raising=False)
    # The flush under test is the interleaved one below, not whatever the
    # scheduler would manage in a stubbed-Qt environment.
    monkeypatch.setattr(ms, "_schedule_move_learn_flush", lambda **kw: None)

    class _InterleavingDB(_FakeCompanionDB):
        """Stands in for the earlier grant's GUI callback firing mid-save."""

        def __init__(self, *rows):
            super().__init__(*rows)
            self.interleaved = False

        def save_pokemon(self, pkmndata):
            if not self.interleaved:
                self.interleaved = True  # set first: flush saves through here too
                ms.flush_pending_move_learns(db=self, logger=_Logger())
            super().save_pokemon(pkmndata)

    class _Presenter:
        def choose_attack_to_replace(self, attacks, new_attack):
            return "growl"

    monkeypatch.setattr(services, "ui", _Presenter(), raising=False)

    db = _InterleavingDB(_full_moveset_row("RACE"))

    _run_off_thread(
        ms._attribute_xp_and_evs_to_companion,
        "RACE",
        100,
        {},
        _Settings(),
        db=db,
    )

    assert db.interleaved, "the mid-save flush never ran — test proves nothing"

    # The next GUI-thread pass, now that the row is written.
    ms.flush_pending_move_learns(db=db, logger=_Logger())

    assert db.rows["RACE"]["attacks"] == [
        "tackle", "thunderbolt", "quick-attack", "thunder-shock",
    ]
    with ms._pending_move_learns_lock:
        assert ms._pending_move_learns == []


def test_queued_move_learns_are_deduped(mobile_db):
    ms._queue_move_learn_prompt("A", "Pikachu", "thunderbolt")
    ms._queue_move_learn_prompt("A", "Pikachu", "thunderbolt")
    ms._queue_move_learn_prompt("A", "Pikachu", "iron-tail")
    ms._queue_move_learn_prompt("", "Pikachu", "thunderbolt")  # no id -> ignored

    with ms._pending_move_learns_lock:
        parked = [(p["individual_id"], p["new_attack"]) for p in ms._pending_move_learns]

    assert parked == [("A", "thunderbolt"), ("A", "iron-tail")]


def test_flush_prompts_persists_the_swap_and_syncs_the_singleton(mobile_db, monkeypatch):
    db, _ = mobile_db
    _full_moveset_companion(db, "FLUSH")
    singleton = _FakeCompanionSingleton("FLUSH")
    monkeypatch.setattr(services, "main_pokemon", singleton, raising=False)

    asked = []

    class _Presenter:
        def choose_attack_to_replace(self, attacks, new_attack):
            asked.append((list(attacks), new_attack))
            return "growl"

    monkeypatch.setattr(services, "ui", _Presenter(), raising=False)
    ms._queue_move_learn_prompt("FLUSH", "Pikachu", "thunderbolt")

    ms.flush_pending_move_learns(db=db, logger=_Logger())

    assert asked == [
        (["tackle", "growl", "quick-attack", "thunder-shock"], "thunderbolt")
    ]
    assert db.get_pokemon("FLUSH")["attacks"] == [
        "tackle", "thunderbolt", "quick-attack", "thunder-shock",
    ]
    assert singleton.attacks == [
        "tackle", "thunderbolt", "quick-attack", "thunder-shock",
    ]
    with ms._pending_move_learns_lock:
        assert ms._pending_move_learns == []  # drained


def test_flush_declined_prompt_keeps_the_current_moves(mobile_db, monkeypatch):
    db, _ = mobile_db
    _full_moveset_companion(db, "DECLINE")
    monkeypatch.setattr(services, "main_pokemon", None, raising=False)

    class _Presenter:
        def choose_attack_to_replace(self, attacks, new_attack):
            return None  # player closed the dialog

    monkeypatch.setattr(services, "ui", _Presenter(), raising=False)
    ms._queue_move_learn_prompt("DECLINE", "Pikachu", "thunderbolt")

    ms.flush_pending_move_learns(db=db, logger=_Logger())

    assert db.get_pokemon("DECLINE")["attacks"] == [
        "tackle", "growl", "quick-attack", "thunder-shock",
    ]


def test_flush_rereads_the_db_and_just_learns_when_a_slot_opened_up(mobile_db, monkeypatch):
    """The worker wrote more rows after parking the decision, and the player may
    have freed a slot in between — re-read rather than trusting the snapshot."""
    db, _ = mobile_db
    _full_moveset_companion(db, "SLOT")
    monkeypatch.setattr(services, "main_pokemon", None, raising=False)

    asked = []

    class _Presenter:
        def choose_attack_to_replace(self, attacks, new_attack):
            asked.append(new_attack)
            return attacks[0]

    monkeypatch.setattr(services, "ui", _Presenter(), raising=False)
    ms._queue_move_learn_prompt("SLOT", "Pikachu", "thunderbolt")

    # The player drops a move from Pokémon Details before the flush runs.
    pkmndata = db.get_pokemon("SLOT")
    pkmndata["attacks"] = ["tackle", "growl", "quick-attack"]
    db.save_pokemon(pkmndata)

    ms.flush_pending_move_learns(db=db, logger=_Logger())

    assert asked == []  # nothing to choose between
    assert db.get_pokemon("SLOT")["attacks"] == [
        "tackle", "growl", "quick-attack", "thunderbolt",
    ]


def test_flush_without_a_presenter_reports_instead_of_silently_dropping(mobile_db, monkeypatch):
    db, _ = mobile_db
    _full_moveset_companion(db, "NOUI")
    monkeypatch.setattr(services, "main_pokemon", None, raising=False)
    monkeypatch.setattr(services, "ui", None, raising=False)

    from Ankimon.functions import drawing_utils as _drawing_utils

    seen = []
    monkeypatch.setattr(
        _drawing_utils, "tooltipWithColour", lambda msg, *a, **k: seen.append(msg)
    )

    warnings = []

    class _WarnLogger(_Logger):
        def log(self, level, msg, *a, **k):
            warnings.append((level, msg))

    ms._queue_move_learn_prompt("NOUI", "Pikachu", "thunderbolt")
    ms.flush_pending_move_learns(db=db, logger=_WarnLogger())

    assert any("thunderbolt" in msg for msg in seen)
    assert any(level == "warning" and "thunderbolt" in msg for level, msg in warnings)
    # Nothing guessed on the player's behalf.
    assert db.get_pokemon("NOUI")["attacks"] == [
        "tackle", "growl", "quick-attack", "thunder-shock",
    ]


def test_flush_requeues_an_entry_whose_save_blew_up(mobile_db, monkeypatch):
    """A failed replay must stay pending, not vanish.

    flush drains the whole queue up front, so before this every exception below
    the drain took the parked decision with it: the player was told nothing, the
    move was never written, and there was no entry left for a later pass to
    retry — the same silent loss the deferral mechanism exists to prevent, just
    moved one step later.
    """
    db, _ = mobile_db
    _full_moveset_companion(db, "BOOM")
    monkeypatch.setattr(services, "main_pokemon", None, raising=False)

    class _Presenter:
        def choose_attack_to_replace(self, attacks, new_attack):
            return "growl"

    monkeypatch.setattr(services, "ui", _Presenter(), raising=False)

    def _explode(_pkmndata):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(db, "save_pokemon", _explode)

    errors = []

    class _ErrLogger(_Logger):
        def log(self, level, msg, *a, **k):
            errors.append((level, msg))

    ms._queue_move_learn_prompt("BOOM", "Pikachu", "thunderbolt")
    ms.flush_pending_move_learns(db=db, logger=_ErrLogger())

    assert any(level == "error" and "BOOM" in msg for level, msg in errors)
    with ms._pending_move_learns_lock:
        parked = [(p["individual_id"], p["new_attack"]) for p in ms._pending_move_learns]
    assert parked == [("BOOM", "thunderbolt")], "the failed entry was dropped"


def test_flush_retries_a_requeued_entry_on_the_next_pass(mobile_db, monkeypatch):
    """Requeueing is only worth anything if the retry actually lands."""
    db, _ = mobile_db
    _full_moveset_companion(db, "RETRY")
    monkeypatch.setattr(services, "main_pokemon", None, raising=False)

    class _Presenter:
        def choose_attack_to_replace(self, attacks, new_attack):
            return "growl"

    monkeypatch.setattr(services, "ui", _Presenter(), raising=False)

    real_save = db.save_pokemon
    calls = []

    def _fail_once(pkmndata):
        calls.append(pkmndata["individual_id"])
        if len(calls) == 1:
            raise RuntimeError("database is locked")
        return real_save(pkmndata)

    monkeypatch.setattr(db, "save_pokemon", _fail_once)

    ms._queue_move_learn_prompt("RETRY", "Pikachu", "thunderbolt")
    ms.flush_pending_move_learns(db=db, logger=_Logger())   # fails, requeues
    ms.flush_pending_move_learns(db=db, logger=_Logger())   # succeeds

    assert db.get_pokemon("RETRY")["attacks"] == [
        "tackle", "thunderbolt", "quick-attack", "thunder-shock",
    ]
    with ms._pending_move_learns_lock:
        assert ms._pending_move_learns == []  # drained for good this time


def test_flush_schedules_its_own_retry_after_a_failure(mobile_db, monkeypatch):
    """A raised replay has no other attribution event guaranteed behind it, so
    the flush must arrange its own bounded retry rather than leave the entry to
    rot in memory until shutdown."""
    db, _ = mobile_db
    _full_moveset_companion(db, "SELFRETRY")
    monkeypatch.setattr(services, "main_pokemon", None, raising=False)

    class _Presenter:
        def choose_attack_to_replace(self, attacks, new_attack):
            return "growl"

    monkeypatch.setattr(services, "ui", _Presenter(), raising=False)
    from Ankimon.functions import drawing_utils as _du

    monkeypatch.setattr(_du, "tooltipWithColour", lambda *a, **k: None)

    scheduled = []
    fake_qt = types.ModuleType("aqt.qt")
    fake_qt.QTimer = types.SimpleNamespace(
        singleShot=lambda ms_delay, cb: scheduled.append((ms_delay, cb))
    )
    monkeypatch.setitem(sys.modules, "aqt.qt", fake_qt)

    fail = {"on": True}
    real_save = db.save_pokemon

    def _save(pkmndata):
        if fail["on"]:
            raise RuntimeError("database is locked")
        return real_save(pkmndata)

    monkeypatch.setattr(db, "save_pokemon", _save)

    ms._queue_move_learn_prompt("SELFRETRY", "Pikachu", "thunderbolt")
    ms.flush_pending_move_learns(db=db, logger=_Logger())

    # Entry kept AND a retry queued onto the GUI-thread timer.
    with ms._pending_move_learns_lock:
        assert [e["individual_id"] for e in ms._pending_move_learns] == ["SELFRETRY"]
    assert len(scheduled) == 1
    assert scheduled[0][0] == ms._MOVE_LEARN_RETRY_DELAY_MS

    # Let the scheduled retry run against a now-healthy DB: it lands.
    fail["on"] = False
    scheduled[0][1]()
    assert db.get_pokemon("SELFRETRY")["attacks"] == [
        "tackle", "thunderbolt", "quick-attack", "thunder-shock",
    ]
    with ms._pending_move_learns_lock:
        assert ms._pending_move_learns == []


def test_flush_stops_retrying_at_the_ceiling_but_keeps_the_move(mobile_db, monkeypatch):
    """Hitting the retry ceiling must stop retrying, NOT discard the move.

    The in-memory queue alone used to drop the entry here, telling the player
    the move "was discarded" -- with the mobile battle already resolved there
    was nothing left to reconstruct it from. The durable pending_move_learns
    row must survive so a later pass can still offer the swap.
    """
    db, _ = mobile_db
    _full_moveset_companion(db, "DOOMED")
    monkeypatch.setattr(services, "main_pokemon", None, raising=False)

    class _Presenter:
        def choose_attack_to_replace(self, attacks, new_attack):
            return "growl"

    monkeypatch.setattr(services, "ui", _Presenter(), raising=False)

    scheduled = []
    monkeypatch.setitem(
        sys.modules,
        "aqt.qt",
        types.SimpleNamespace(
            QTimer=types.SimpleNamespace(
                singleShot=lambda delay, cb: scheduled.append((delay, cb))
            )
        ),
    )
    working_save = db.save_pokemon
    monkeypatch.setattr(
        db, "save_pokemon", lambda *_a: (_ for _ in ()).throw(RuntimeError("locked"))
    )

    records = []

    class _CapturingLogger:
        def log(self, *args, **kwargs):
            records.append(args)

    ms._queue_move_learn_prompt("DOOMED", "Pikachu", "thunderbolt", db=db)
    for _ in range(ms._MOVE_LEARN_MAX_ATTEMPTS):
        ms.flush_pending_move_learns(db=db, logger=_CapturingLogger())

    # The in-memory queue is drained (no unbounded loop) and no further retry
    # is scheduled once the ceiling is hit.
    with ms._pending_move_learns_lock:
        assert ms._pending_move_learns == []
    assert len(scheduled) == ms._MOVE_LEARN_MAX_ATTEMPTS - 1
    assert all(delay == ms._MOVE_LEARN_RETRY_DELAY_MS for delay, _ in scheduled)
    assert any(
        "DOOMED" in str(rec) and "parked" in str(rec) for rec in records
    ), records

    # THE POINT: the decision is still on disk, so the move is not lost.
    parked = db.get_pending_move_learns()
    assert [(r["individual_id"], r["new_attack"]) for r in parked] == [
        ("DOOMED", "thunderbolt")
    ]

    # A later pass, once the database is writable again, still lands the swap.
    monkeypatch.setattr(db, "save_pokemon", working_save)
    ms.flush_pending_move_learns(db=db, logger=_CapturingLogger())

    assert db.get_pokemon("DOOMED")["attacks"] == [
        "tackle", "thunderbolt", "quick-attack", "thunder-shock",
    ]
    assert db.get_pending_move_learns() == []  # settled, so the row is gone


def test_a_move_parked_before_a_crash_is_recovered_on_the_next_flush(
    mobile_db, monkeypatch
):
    """The crash case: the process goes away between parking the decision and
    showing the prompt, so the in-memory queue is gone. The durable row must
    bring it back."""
    db, _ = mobile_db
    _full_moveset_companion(db, "CRASHED")
    monkeypatch.setattr(services, "main_pokemon", None, raising=False)

    ms._queue_move_learn_prompt("CRASHED", "Pikachu", "thunderbolt", db=db)

    # Simulate the restart: everything in RAM is gone, the DB row is not.
    with ms._pending_move_learns_lock:
        del ms._pending_move_learns[:]
    assert len(db.get_pending_move_learns()) == 1

    asked = []

    class _Presenter:
        def choose_attack_to_replace(self, attacks, new_attack):
            asked.append(new_attack)
            return "growl"

    monkeypatch.setattr(services, "ui", _Presenter(), raising=False)
    ms.flush_pending_move_learns(db=db, logger=_Logger())

    assert asked == ["thunderbolt"], "the parked decision was never re-offered"
    assert db.get_pokemon("CRASHED")["attacks"] == [
        "tackle", "thunderbolt", "quick-attack", "thunder-shock",
    ]
    assert db.get_pending_move_learns() == []


def test_no_ui_available_keeps_the_decision_parked(mobile_db, monkeypatch):
    """With nothing able to show the dialog the move is NOT settled -- the row
    stays so a later pass with a UI can still put the choice to the player."""
    db, _ = mobile_db
    _full_moveset_companion(db, "NOUI")
    monkeypatch.setattr(services, "main_pokemon", None, raising=False)
    monkeypatch.setattr(services, "ui", None, raising=False)

    ms._queue_move_learn_prompt("NOUI", "Pikachu", "thunderbolt", db=db)
    ms.flush_pending_move_learns(db=db, logger=_Logger())

    assert [r["new_attack"] for r in db.get_pending_move_learns()] == ["thunderbolt"]


def test_flush_does_not_requeue_a_declined_prompt(mobile_db, monkeypatch):
    """Declining is a DECISION, not a failure — re-asking every pass would nag."""
    db, _ = mobile_db
    _full_moveset_companion(db, "NONAG")
    monkeypatch.setattr(services, "main_pokemon", None, raising=False)

    class _Presenter:
        def choose_attack_to_replace(self, attacks, new_attack):
            return None  # player closed the dialog

    monkeypatch.setattr(services, "ui", _Presenter(), raising=False)
    ms._queue_move_learn_prompt("NONAG", "Pikachu", "thunderbolt")

    ms.flush_pending_move_learns(db=db, logger=_Logger())

    with ms._pending_move_learns_lock:
        assert ms._pending_move_learns == []


# --- The move choice must be applied to the CURRENT row, not a stale snapshot -
#
# choose_attack_to_replace() blocks on a modal, during which a sync worker on
# its own connection can change the same row -- including its moves. Re-reading
# the row is not enough on its own: the pre-dialog move list must not be written
# back over it.


def test_moves_changed_during_the_dialog_are_not_clobbered(mobile_db, monkeypatch):
    db, _ = mobile_db
    _full_moveset_companion(db, "RACE")
    monkeypatch.setattr(services, "main_pokemon", None, raising=False)

    prompts = []

    class _Presenter:
        def choose_attack_to_replace(self, attacks, new_attack):
            prompts.append(list(attacks))
            if len(prompts) == 1:
                # A sync worker commits a different move set (and a level bump)
                # for this same row while the modal is open.
                row = db.get_pokemon("RACE")
                row["attacks"] = ["surf", "growl", "quick-attack", "thunder-shock"]
                row["level"] = 9
                db.save_pokemon(row)
                return "tackle"  # no longer on the row by the time we save
            return "surf"

    monkeypatch.setattr(services, "ui", _Presenter(), raising=False)
    ms._queue_move_learn_prompt("RACE", "Pikachu", "thunderbolt")

    ms.flush_pending_move_learns(db=db, logger=_Logger())

    # The stale choice is re-put to the player against the CURRENT moves.
    assert len(prompts) == 2, "a selection that vanished must trigger a re-prompt"
    assert prompts[1] == ["surf", "growl", "quick-attack", "thunder-shock"]

    row = db.get_pokemon("RACE")
    # The worker's move rewrite survived -- "tackle" was not resurrected.
    assert row["attacks"] == ["thunderbolt", "growl", "quick-attack", "thunder-shock"]
    assert row["level"] == 9, "the concurrent level write must not be reverted"


def test_a_slot_freed_during_the_dialog_just_learns_the_move(mobile_db, monkeypatch):
    """If the worker dropped a move while the modal was open there is now room,
    so the replacement the player picked is not needed at all."""
    db, _ = mobile_db
    _full_moveset_companion(db, "FREED")
    monkeypatch.setattr(services, "main_pokemon", None, raising=False)

    prompts = []

    class _Presenter:
        def choose_attack_to_replace(self, attacks, new_attack):
            prompts.append(list(attacks))
            row = db.get_pokemon("FREED")
            row["attacks"] = ["tackle", "growl"]
            db.save_pokemon(row)
            return "tackle"

    monkeypatch.setattr(services, "ui", _Presenter(), raising=False)
    ms._queue_move_learn_prompt("FREED", "Pikachu", "thunderbolt")

    ms.flush_pending_move_learns(db=db, logger=_Logger())

    assert len(prompts) == 1, "no re-prompt is needed once a slot is free"
    assert db.get_pokemon("FREED")["attacks"] == ["tackle", "growl", "thunderbolt"]
