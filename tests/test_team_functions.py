"""Tests for ``functions/team_functions.py`` — the Pokémon PC's party seam.

Runs against a real :class:`AnkimonDB` in a temp directory so the ``team``
table, the six-slot cap, stale-id pruning and the trade/release consistency
fixes are all exercised for real. The aqt/anki stubs the DB module needs are
installed per test inside ``conftest.isolated_modules`` and removed again, so
the file is safe both standalone and inside the full suite.
"""

import csv
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_SRC = Path(__file__).parent.parent / "src"

# aqt/anki names ``database_manager`` touches at import time.
_HOST_MODULES = [
    "aqt", "aqt.qt", "aqt.utils", "aqt.gui_hooks", "aqt.operations",
    "aqt.reviewer", "aqt.webview", "aqt.main", "aqt.operations.QueryOp",
    "anki", "anki.hooks", "anki.collection", "anki.models", "anki.notes",
    "anki.template", "anki.buildinfo",
]


class _MockResources:
    """Minimal stand-in for ``Ankimon.resources``; every path resolves under /tmp."""

    user_path = Path("/tmp")
    csv_file_items_cost = Path("/tmp/items.csv")
    items_path = Path("/tmp/items.json")
    badges_path = Path("/tmp/badges.json")
    mypokemon_path = Path("/tmp/mypokemon.json")
    mainpokemon_path = Path("/tmp/mainpokemon.json")

    def __getattr__(self, name):
        """Any other resource path resolves under /tmp."""
        return Path("/tmp") / name


def _package(name):
    """A bare package stub whose ``__path__`` points at the real source tree."""
    pkg = types.ModuleType(name)
    pkg.__path__ = [str(_SRC / name.replace(".", "/"))]
    pkg.__package__ = name
    return pkg


def _load(name, relpath):
    """Load a source module by file path under the stubbed ``Ankimon`` package."""
    spec = importlib.util.spec_from_file_location(name, _SRC / relpath)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def team_env(tmp_path):
    """Load ``database_manager``, ``services`` and ``team_functions`` in isolation.

    Everything under ``aqt``, ``anki`` and ``Ankimon`` is swapped out for the
    duration of one test and put back exactly afterwards (``isolated_modules``
    from conftest), so the module-level mocks the DB needs never leak into the
    rest of the suite. Yields a namespace with ``db``, ``team`` (the module
    under test), ``services`` and ``db_mod``.
    """
    from conftest import isolated_modules

    with isolated_modules("aqt", "anki", "Ankimon"):
        for name in _HOST_MODULES:
            sys.modules[name] = MagicMock()
        for name in ("Ankimon", "Ankimon.functions", "Ankimon.pyobj"):
            sys.modules[name] = _package(name)
        sys.modules["Ankimon.resources"] = _MockResources()
        sys.modules["Ankimon.singletons"] = MagicMock()
        sys.modules["Ankimon.utils"] = MagicMock()

        db_mod = _load("Ankimon.pyobj.database_manager", "Ankimon/pyobj/database_manager.py")
        services_mod = _load("Ankimon.services", "Ankimon/services.py")
        team_mod = _load(
            "Ankimon.functions.team_functions", "Ankimon/functions/team_functions.py"
        )
        services_mod.services.trainer_card = None
        services_mod.services.logger = None

        with patch.object(db_mod, "user_path", tmp_path), \
             patch.object(db_mod, "csv_file_items_cost", str(tmp_path / "items.csv")), \
             patch.object(db_mod, "items_path", tmp_path / "items_mig.json"), \
             patch.object(db_mod, "badges_path", tmp_path / "badges_mig.json"):
            with open(tmp_path / "items.csv", "w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(["id", "identifier", "category_id", "cost", "fling_power", "fling_effect_id"])
                writer.writerow(["1", "master-ball", "34", "0", "", ""])
            db = db_mod.AnkimonDB(MockLogger())
            yield types.SimpleNamespace(
                db=db, team=team_mod, services=services_mod.services, db_mod=db_mod
            )


class MockLogger:
    """Records log calls so tests can assert on pruning notices."""

    def __init__(self):
        """Start with an empty call list."""
        self.calls = []

    def log(self, level, msg):
        """Record a (level, message) pair."""
        self.calls.append((level, msg))

    def log_and_showinfo(self, level, msg):
        """Same as :meth:`log`; nothing is shown headless."""
        self.calls.append((level, msg))

    def _log(self, level, msg):
        """Alias used by ``AnkimonDB`` internals."""
        self.calls.append((level, msg))


def _mon(ind_id, name="pikachu", level=5, pokedex_id=25):
    """Build a minimal saved-Pokémon record."""
    return {
        "individual_id": ind_id,
        "name": name,
        "id": pokedex_id,
        "level": level,
        "nickname": "",
        "gender": "M",
        "shiny": False,
    }


def _seed(db, *ids):
    """Insert one Pokémon per id and return the ids."""
    for ind_id in ids:
        db.save_pokemon(_mon(ind_id))
    return list(ids)


def _team_ids(db):
    """Raw ordered ids from the team table."""
    return [row["individual_id"] for row in db.get_team()]


def test_add_fills_slots_in_order(team_env):
    """Adding appends to the next free slot and reports the 1-based slot."""
    db, team_functions = team_env.db, team_env.team
    a, b = _seed(db, "a", "b")
    first = team_functions.add_to_party(db, a)
    second = team_functions.add_to_party(db, b)
    assert (first.ok, first.outcome, first.slot) == (True, "added", 1)
    assert (second.ok, second.outcome, second.slot) == (True, "added", 2)
    assert _team_ids(db) == [a, b]


def test_add_rejects_duplicate_and_reports_existing_slot(team_env):
    """A Pokémon already in the party is not added twice."""
    db, team_functions = team_env.db, team_env.team
    (a,) = _seed(db, "a")
    team_functions.add_to_party(db, a)
    again = team_functions.add_to_party(db, a)
    assert (again.ok, again.outcome, again.slot) == (False, "already_on_team", 1)
    assert _team_ids(db) == [a]


def test_add_enforces_six_slot_cap(team_env):
    """The seventh add is refused and the table is left untouched."""
    db, team_functions = team_env.db, team_env.team
    ids = _seed(db, *[f"m{i}" for i in range(7)])
    for ind_id in ids[:6]:
        assert team_functions.add_to_party(db, ind_id).ok
    seventh = team_functions.add_to_party(db, ids[6])
    assert (seventh.ok, seventh.outcome) == (False, "team_full")
    assert _team_ids(db) == ids[:6]


def test_add_refuses_pokemon_missing_from_collection(team_env):
    """An id with no ``captured_pokemon`` row never enters the team table."""
    db, team_functions = team_env.db, team_env.team
    result = team_functions.add_to_party(db, "ghost")
    assert (result.ok, result.outcome) == (False, "missing_pokemon")
    assert _team_ids(db) == []
    assert team_functions.add_to_party(db, None).outcome == "missing_pokemon"


def test_remove_closes_the_gap(team_env):
    """Removing the middle member shifts later members up a slot."""
    db, team_functions = team_env.db, team_env.team
    a, b, c = _seed(db, "a", "b", "c")
    for ind_id in (a, b, c):
        team_functions.add_to_party(db, ind_id)
    result = team_functions.remove_from_party(db, b)
    assert (result.ok, result.outcome) == (True, "removed")
    assert _team_ids(db) == [a, c]
    rows = db.execute("SELECT slot_position FROM team ORDER BY slot_position").fetchall()
    assert [row[0] for row in rows] == [1, 2]


def test_remove_reports_not_on_team(team_env):
    """Removing a boxed Pokémon is a no-op with a distinct outcome."""
    db, team_functions = team_env.db, team_env.team
    a, b = _seed(db, "a", "b")
    team_functions.add_to_party(db, a)
    result = team_functions.remove_from_party(db, b)
    assert (result.ok, result.outcome) == (False, "not_on_team")
    assert _team_ids(db) == [a]


def test_stale_ids_are_pruned_on_write_and_do_not_count_against_cap(team_env):
    """Team rows whose Pokémon vanished are dropped when the party is rewritten."""
    db, team_functions = team_env.db, team_env.team
    ids = _seed(db, *[f"m{i}" for i in range(6)])
    db.save_team([{"individual_id": i} for i in ids[:5]] + [{"individual_id": "released"}])
    logger = MockLogger()
    result = team_functions.add_to_party(db, ids[5], logger)
    assert (result.ok, result.outcome, result.slot, result.pruned) == (True, "added", 6, 1)
    assert _team_ids(db) == ids
    assert any("stale" in msg for _, msg in logger.calls)


def test_load_party_resolves_records_in_slot_order_and_skips_stale(team_env):
    """``load_party`` returns full records, in order, without touching the table."""
    db, team_functions = team_env.db, team_env.team
    a, b = _seed(db, "a", "b")
    db.save_team(
        [{"individual_id": b}, {"individual_id": "released"}, {"individual_id": a}]
    )
    logger = MockLogger()
    party = team_functions.load_party(db, logger)
    assert [p["individual_id"] for p in party] == [b, a]
    assert party[0]["name"] == "pikachu"
    assert _team_ids(db) == [b, "released", a]  # read-only
    assert any("missing" in msg for _, msg in logger.calls)


def test_load_party_empty_when_no_team(team_env):
    """No team rows means an empty party, not the first six Pokémon."""
    db, team_functions = team_env.db, team_env.team
    _seed(db, "a", "b")
    assert team_functions.load_party(db) == []
    assert team_functions.get_team_ids(db) == []


def test_write_refreshes_trainer_card_best_effort(team_env):
    """A successful save reloads the trainer card; a failing card is logged, not raised."""
    db, team_functions = team_env.db, team_env.team
    (a,) = _seed(db, "a")
    card = MagicMock()
    card.reload_team.side_effect = RuntimeError("boom")
    team_env.services.trainer_card = card
    logger = MockLogger()
    result = team_functions.add_to_party(db, a, logger)
    assert result.ok
    card.reload_team.assert_called_once()
    assert any("trainer card" in msg for _, msg in logger.calls)


def test_db_error_leaves_table_untouched(team_env):
    """A failing ``save_team`` surfaces as ``db_error`` with nothing written."""
    db, team_functions = team_env.db, team_env.team
    (a,) = _seed(db, "a")
    with patch.object(db, "save_team", side_effect=RuntimeError("disk")):
        result = team_functions.add_to_party(db, a)
    assert (result.ok, result.outcome) == (False, "db_error")
    assert _team_ids(db) == []


@pytest.mark.parametrize("mutator", ["add_to_party", "place_in_party"])
def test_read_failure_fails_closed_and_keeps_the_party(team_env, mutator):
    """A team read that raises must not be mistaken for an empty team.

    Otherwise the write path would append to ``[]`` and ``save_team`` would
    replace the whole saved party with the single new member.
    """
    db, team_functions = team_env.db, team_env.team
    a, b, c, d = _seed(db, "a", "b", "c", "d")
    for ind_id in (a, b, c):
        assert team_functions.add_to_party(db, ind_id).ok
    fn = getattr(team_functions, mutator)
    args = (db, d) if mutator == "add_to_party" else (db, d, 0)
    with patch.object(db, "get_team", side_effect=RuntimeError("database is locked")):
        result = fn(*args)
    assert (result.ok, result.outcome) == (False, "db_error")
    assert _team_ids(db) == [a, b, c]


def test_partial_save_is_rolled_back(team_env):
    """A ``save_team`` that fails after its DELETE must not leave the wipe pending.

    ``save_team`` deletes every row before re-inserting; if an insert raises
    the open transaction would otherwise persist an empty team on the next
    unrelated commit.
    """
    db, team_functions = team_env.db, team_env.team
    a, b, c = _seed(db, "a", "b", "c")
    for ind_id in (a, b):
        assert team_functions.add_to_party(db, ind_id).ok

    def half_applied(team_list, *, commit=True):
        """Mimic save_team dying between its DELETE and its INSERTs."""
        db._get_connection().cursor().execute("DELETE FROM team")
        raise RuntimeError("disk full")

    with patch.object(db, "save_team", side_effect=half_applied):
        result = team_functions.add_to_party(db, c)
    assert (result.ok, result.outcome) == (False, "db_error")
    assert _team_ids(db) == [a, b]
    db._get_connection().commit()  # a later unrelated commit must not finish the wipe
    assert _team_ids(db) == [a, b]


def test_delete_pokemon_frees_its_party_slot(team_env):
    """Releasing a party member removes its team row too."""
    db, team_functions = team_env.db, team_env.team
    a, b = _seed(db, "a", "b")
    db.save_team([{"individual_id": a}, {"individual_id": b}])
    assert db.delete_pokemon(a)
    assert _team_ids(db) == [b]


def test_replace_pokemon_hands_the_slot_to_the_incoming_pokemon(team_env):
    """A trade keeps the slot and points it at the new individual id."""
    db, team_functions = team_env.db, team_env.team
    a, b = _seed(db, "a", "b")
    db.save_team([{"individual_id": a}, {"individual_id": b}])
    assert db.replace_pokemon(_mon("traded", name="eevee", pokedex_id=133), a)
    assert _team_ids(db) == ["traded", b]


def test_replace_pokemon_outside_the_party_still_reports_success(team_env):
    """A trade of a boxed Pokémon returns True and leaves the team table alone."""
    db, team_functions = team_env.db, team_env.team
    a, b = _seed(db, "a", "b")
    db.save_team([{"individual_id": a}])
    assert db.replace_pokemon(_mon("traded", name="eevee", pokedex_id=133), b) is True
    assert _team_ids(db) == [a]
    assert db.get_pokemon("traded")["name"] == "eevee"


def test_move_in_party_reorders_and_clamps(team_env):
    """Moving shifts the others along; an index past the end lands last."""
    db, team_functions = team_env.db, team_env.team
    a, b, c = _seed(db, "a", "b", "c")
    db.save_team([{"individual_id": i} for i in (a, b, c)])
    result = team_functions.move_in_party(db, c, 0)
    assert (result.ok, result.outcome, result.slot) == (True, "moved", 1)
    assert _team_ids(db) == [c, a, b]
    result = team_functions.move_in_party(db, c, 99)
    assert (result.ok, result.slot) == (True, 3)
    assert _team_ids(db) == [a, b, c]
    assert team_functions.move_in_party(db, "zzz", 0).outcome == "not_on_team"


def test_place_in_party_appends_swaps_or_moves(team_env):
    """Drop past the end appends; onto an occupant swaps it out; a member just moves."""
    db, team_functions = team_env.db, team_env.team
    a, b, c = _seed(db, "a", "b", "c")
    db.save_team([{"individual_id": a}])

    appended = team_functions.place_in_party(db, b, 5)
    assert (appended.outcome, appended.slot, appended.replaced) == ("added", 2, None)
    assert _team_ids(db) == [a, b]

    swapped = team_functions.place_in_party(db, c, 0)
    assert (swapped.outcome, swapped.slot, swapped.replaced) == ("swapped", 1, a)
    assert _team_ids(db) == [c, b]

    moved = team_functions.place_in_party(db, b, 0)
    assert (moved.outcome, moved.slot) == ("moved", 1)
    assert _team_ids(db) == [b, c]


def test_place_in_party_respects_cap_but_allows_swap_when_full(team_env):
    """A full party refuses an append yet still lets a drop replace an occupant."""
    db, team_functions = team_env.db, team_env.team
    ids = _seed(db, *[f"m{i}" for i in range(7)])
    db.save_team([{"individual_id": i} for i in ids[:6]])
    assert team_functions.place_in_party(db, ids[6], 6).outcome == "team_full"
    assert _team_ids(db) == ids[:6]
    swapped = team_functions.place_in_party(db, ids[6], 2)
    assert (swapped.outcome, swapped.replaced) == ("swapped", ids[2])
    assert _team_ids(db) == ids[:2] + [ids[6]] + ids[3:6]
    assert team_functions.place_in_party(db, "ghost", 0).outcome == "missing_pokemon"


def test_unreadable_team_member_keeps_its_slot_as_a_placeholder(team_env):
    """A team row whose record cannot be read is neither hidden nor pruned.

    The column shows a placeholder in that slot and the write seam keeps the id,
    so slot indices agree between what the user sees and what a drop targets.
    """
    db, team_functions = team_env.db, team_env.team
    (a,) = _seed(db, "a")
    # The table's generated columns reject non-JSON, so "unreadable" in practice
    # means valid JSON that is not a Pokémon record.
    db.execute(
        "INSERT INTO captured_pokemon (individual_id, is_main, data)"
        " VALUES ('broken', 0, '\"not a record\"')"
    )
    db._get_connection().commit()
    db.save_team([{"individual_id": "broken"}, {"individual_id": a}])

    logger = MockLogger()
    party = team_functions.load_party(db, logger)
    assert [p["individual_id"] for p in party] == ["broken", a]
    assert party[0]["name"] == "???" and party[0].get("unreadable") is True
    assert any("cannot be read" in msg for _, msg in logger.calls)

    # The write seam agrees: the broken row still occupies slot 1 and counts.
    clean, pruned = team_functions._clean_team_ids(db)
    assert (clean, pruned) == (["broken", a], 0)
    (b,) = _seed(db, "b")
    placed = team_functions.place_in_party(db, b, 0)
    assert (placed.outcome, placed.replaced) == ("swapped", "broken")
    assert _team_ids(db) == [b, a]
