"""Ankidex caught/seen history durability (``mark_as_caught``).

Exercises the *real* ``AnkimonDB`` SQLite layer, not a stub. The flow under test
is the one ``save_pokemon`` / ``save_main_pokemon`` / the evolution window all
drive:

    row committed  ->  mark_as_caught(id)  ->  pokedex_caught + pokedex_seen

Before the fix those two lists were written by two ``set_user_data`` calls, each
with its own commit, and every caller swallowed a failure — so an interrupted
write could persist a caught id whose matching seen id never landed, and a
failed write vanished into a log line while the save still reported success.
The tests below pin the three properties that closes:

* both lists move in ONE transaction (never one without the other);
* the writes belong to the caller's transaction, so a rolled-back bulk resolve
  does not leave Pokedex entries behind for Pokemon whose saves were undone;
* a mark that never landed is re-derived from the stored Pokemon on the next
  launch instead of being lost for good.
"""

import importlib.util
import sqlite3
import sys
import threading
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_SRC = Path(__file__).parent.parent / "src"


def _load_ankimon_db():
    """Load the real ``database_manager`` against stubbed Anki/aqt deps.

    Every stub is torn down again as soon as the module has executed. pytest
    imports all test modules during collection, so MagicMock entries left in
    ``sys.modules`` for ``aqt`` / ``anki`` / ``Ankimon.resources`` would poison
    unrelated test modules that import those for real. ``database_manager``
    only needs them while it executes: afterwards it holds its own references,
    and its one lazy ``..services`` import is already except-wrapped.
    """
    stub_names = [
        "aqt",
        "aqt.qt",
        "aqt.utils",
        "aqt.gui_hooks",
        "aqt.operations",
        "aqt.reviewer",
        "aqt.webview",
        "aqt.main",
        "anki",
        "anki.hooks",
        "anki.collection",
        "anki.models",
        "anki.notes",
        "anki.template",
        "anki.buildinfo",
    ]

    class MockResources(types.ModuleType):
        user_path = Path("/tmp")

        def __getattr__(self, name):
            return Path("/tmp") / name

    module_name = "Ankimon.pyobj.database_manager"
    saved = {
        name: sys.modules.get(name)
        for name in stub_names + ["Ankimon.resources", module_name]
    }

    try:
        for name in stub_names:
            sys.modules[name] = MagicMock()
        sys.modules["Ankimon.resources"] = MockResources("Ankimon.resources")

        spec = importlib.util.spec_from_file_location(
            module_name, _SRC / "Ankimon" / "pyobj" / "database_manager.py"
        )
        db_mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = db_mod
        spec.loader.exec_module(db_mod)
        return db_mod
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


_DB_MOD = _load_ankimon_db()
AnkimonDB = _DB_MOD.AnkimonDB


class _MockLogger:
    def __init__(self):
        self.records = []

    def log(self, level, msg):
        self.records.append((level, str(msg)))

    def log_and_showinfo(self, level, msg):
        self.records.append((level, str(msg)))


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "ankimon.db"


@pytest.fixture
def open_db(db_path):
    """Factory opening a real AnkimonDB on one shared file (re-openable)."""
    opened = []

    def _open():
        db = AnkimonDB(_MockLogger(), db_path=db_path)
        opened.append(db)
        return db

    yield _open

    for db in opened:
        try:
            db.close()
        except Exception:
            pass


def _pokemon(individual_id, pokedex_id, name):
    return {
        "individual_id": individual_id,
        "id": pokedex_id,
        "name": name,
        "level": 5,
        "xp": 0,
        "shiny": False,
        "attacks": ["Tackle"],
        "base_stats": {"hp": 45, "atk": 49, "def": 49, "spa": 65, "spd": 65, "spe": 45},
        "stats": {"hp": 45, "atk": 49, "def": 49, "spa": 65, "spd": 65, "spe": 45},
        "ev": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
        "iv": {"hp": 15, "atk": 15, "def": 15, "spa": 15, "spd": 15, "spe": 15},
        "ability": "Overgrow",
        "growth_rate": "medium",
        "base_experience": 64,
        "gender": "M",
    }


def _raw_user_data(db_path):
    """Read user_data on a SEPARATE connection: only committed rows are visible."""
    conn = sqlite3.connect(str(db_path))
    try:
        return dict(conn.execute("SELECT key, value FROM user_data").fetchall())
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# 1. caught and seen move together
# --------------------------------------------------------------------------- #
def test_mark_as_caught_records_both_lists(open_db):
    db = open_db()
    db.mark_as_caught(25)

    assert db.get_caught_ids() == {25}
    assert db.get_seen_ids() == {25}
    # Catching implies seeing: caught must never outrun seen.
    assert db.get_caught_ids() <= db.get_seen_ids()


def test_repeated_marks_do_not_duplicate(open_db):
    db = open_db()
    for _ in range(3):
        db.mark_as_caught(25)
    db.mark_as_caught(26)

    assert db.get_user_data("pokedex_caught", []) == [25, 26]
    assert db.get_user_data("pokedex_seen", []) == [25, 26]


def test_legacy_string_ids_are_normalised_on_the_next_write(open_db):
    """A legacy list holding "25" and 25 is read as {25} and stored back clean."""
    db = open_db()
    db.set_user_data("pokedex_caught", ["25", 25, "junk", None])
    db.set_user_data("pokedex_seen", ["25"])

    assert db.get_caught_ids() == {25}

    db.mark_as_caught(26)

    assert db.get_user_data("pokedex_caught", []) == [25, 26]
    assert db.get_caught_ids() == {25, 26}
    assert db.get_seen_ids() == {25, 26}


def test_non_numeric_id_is_ignored(open_db):
    db = open_db()
    db.mark_as_caught("not-an-id")

    assert db.get_caught_ids() == set()
    assert db.get_seen_ids() == set()


# --------------------------------------------------------------------------- #
# 2. a failed write leaves NEITHER list changed
# --------------------------------------------------------------------------- #
def test_failed_commit_persists_neither_list(open_db, db_path):
    """The P2 case: the write fails, so no half-written history is committed."""
    db = open_db()
    db.mark_as_caught(1)  # a committed baseline to diverge from

    conn = db._get_connection()
    with patch.object(
        type(conn), "commit", side_effect=sqlite3.OperationalError("disk I/O error")
    ):
        with pytest.raises(sqlite3.OperationalError):
            db.mark_as_caught(2)

    # Nothing from the failed mark reached the file: not the caught id, and not
    # a seen id orphaned from it.
    persisted = _raw_user_data(db_path)
    assert persisted["pokedex_caught"] == "[1]"
    assert persisted["pokedex_seen"] == "[1]"

    conn.rollback()
    assert db.get_caught_ids() == {1}
    assert db.get_seen_ids() == {1}


def test_save_pokemon_survives_a_failed_mark(open_db):
    """A dex-mark failure must not fail the catch — but must be logged loudly."""
    db = open_db()
    with patch.object(
        AnkimonDB, "mark_as_caught", side_effect=sqlite3.OperationalError("locked")
    ):
        assert db.save_pokemon(_pokemon("pika-uuid", 25, "Pikachu")) is True

    assert db.get_pokemon("pika-uuid") is not None
    assert any(
        level == "error" and "caught" in msg for level, msg in db.logger.records
    ), db.logger.records


# --------------------------------------------------------------------------- #
# 3. the marks belong to the caller's transaction
# --------------------------------------------------------------------------- #
def test_marks_roll_back_with_the_enclosing_transaction(open_db, db_path):
    """Mobile "Resolve All" holds one long transaction (``_disable_commit``).

    Committing the Pokedex write independently would leave the dex crediting a
    species whose save was rolled back with that transaction.
    """
    db = open_db()
    conn = db._get_connection()
    conn._disable_commit = True
    try:
        db.mark_as_caught(151)
        # Visible to this connection (uncommitted), invisible to any other.
        assert db.get_caught_ids() == {151}
        assert "pokedex_caught" not in _raw_user_data(db_path)
    finally:
        conn._disable_commit = False
        conn.rollback()

    assert db.get_caught_ids() == set()
    assert db.get_seen_ids() == set()


# --------------------------------------------------------------------------- #
# 4. a mark that never landed heals on the next launch
# --------------------------------------------------------------------------- #
def test_startup_reconcile_recovers_a_dropped_mark(open_db):
    """save_pokemon reports success even when the mark fails; reopening heals it."""
    db = open_db()
    with patch.object(
        AnkimonDB, "mark_as_caught", side_effect=sqlite3.OperationalError("locked")
    ):
        db.save_pokemon(_pokemon("pika-uuid", 25, "Pikachu"))

    assert db.get_caught_ids() == set()
    db.close()

    healed = open_db()
    assert 25 in healed.get_caught_ids()
    assert 25 in healed.get_seen_ids()


def test_startup_reconcile_backfills_a_pre_existing_collection(open_db, db_path):
    """A database written before the caught list existed does not start empty."""
    db = open_db()
    db.save_pokemon(_pokemon("bulba-uuid", 1, "Bulbasaur"))
    db.save_pokemon(_pokemon("chari-uuid", 6, "Charizard"))
    db.add_to_history(_pokemon("squirt-uuid", 7, "Squirtle"))

    # Simulate the pre-upgrade state: rows present, no pokedex history at all.
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "DELETE FROM user_data WHERE key IN ('pokedex_caught', 'pokedex_seen')"
    )
    conn.commit()
    conn.close()
    db.close()

    backfilled = open_db()
    assert {1, 6, 7} <= backfilled.get_caught_ids()
    assert {1, 6, 7} <= backfilled.get_seen_ids()


def test_startup_reconcile_is_idempotent(open_db):
    db = open_db()
    db.save_pokemon(_pokemon("bulba-uuid", 1, "Bulbasaur"))
    db.close()

    second = open_db()
    stored = second.get_user_data("pokedex_caught", [])
    second.close()

    third = open_db()
    assert third.get_user_data("pokedex_caught", []) == stored


def test_startup_reconcile_writes_nothing_for_an_empty_collection(open_db, db_path):
    open_db().close()
    assert _raw_user_data(db_path).get("pokedex_caught") is None


# --------------------------------------------------------------------------- #
# 5. lock ordering: the sweep must never run under the connection lock
# --------------------------------------------------------------------------- #
def test_reconcile_never_runs_under_the_connection_lock(open_db, tmp_path, monkeypatch):
    """``switch_database`` holds ``_conn_lock`` for its whole ``quiesce`` block.

    The sweep takes ``_pokedex_lock`` and *then* a connection, the reverse of
    that order, so running it inside the block would deadlock against a
    background ``mark_as_caught`` parked between the two.
    """
    db = open_db()
    if not hasattr(db._conn_lock, "_is_owned"):
        pytest.skip("RLock._is_owned() unavailable on this interpreter")

    owned_at_call = []
    real_reconcile = db._reconcile_pokedex_history

    def spy():
        owned_at_call.append(db._conn_lock._is_owned())
        return real_reconcile()

    monkeypatch.setattr(db, "_reconcile_pokedex_history", spy)
    monkeypatch.setattr(_DB_MOD, "user_path", tmp_path)

    assert db.switch_database("other_profile.db") is True
    assert owned_at_call == [False], "pokedex reconcile ran while holding _conn_lock"


def test_concurrent_marks_keep_every_id(open_db):
    """Interleaved saves rewrite both lists wholesale; none may be lost."""
    db = open_db()
    ids = list(range(1, 61))
    barrier = threading.Barrier(4)

    def worker(chunk):
        barrier.wait()
        for pokemon_id in chunk:
            db.mark_as_caught(pokemon_id)

    threads = [
        threading.Thread(target=worker, args=(ids[i::4],)) for i in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)

    assert db.get_caught_ids() == set(ids)
    assert db.get_seen_ids() == set(ids)
