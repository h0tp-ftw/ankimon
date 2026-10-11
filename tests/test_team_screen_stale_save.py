"""The Team screen must not overwrite a party edited elsewhere (reviewer item on PR #950).

``ProfileData.handle_save_team`` receives the party the page loaded and refuses
the save when the stored party no longer matches it. Loads the real
``AnkimonDB`` + ``ProfileData`` + ``team_functions`` hermetically (monkeypatched
``sys.modules``, restored after each test) so nothing leaks into the suite.
"""

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"


class _Logger:
    """Minimal logger with the methods ProfileData and AnkimonDB call."""

    def log(self, level, msg):
        """Swallow."""

    def log_and_showinfo(self, level, msg):
        """Swallow."""

    def _log(self, level, msg):
        """Swallow."""


def _load(name, relpath, monkeypatch):
    """Load one real source file under ``name`` via monkeypatched sys.modules."""
    spec = importlib.util.spec_from_file_location(name, _SRC / relpath)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, mod)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Real DB + ProfileData + team seam with every Qt/Anki import stubbed."""
    for name in [
        "aqt", "aqt.qt", "aqt.utils", "aqt.gui_hooks", "aqt.operations",
        "aqt.reviewer", "aqt.webview", "aqt.main",
        "anki", "anki.hooks", "anki.collection", "anki.models",
        "anki.notes", "anki.template", "anki.buildinfo",
    ]:
        monkeypatch.setitem(sys.modules, name, MagicMock())

    class _Resources:
        """Point every resource path at the temp dir."""

        user_path = tmp_path

        def __getattr__(self, name):
            """Any other resource path lives under the temp dir."""
            return tmp_path / name

    monkeypatch.setitem(sys.modules, "Ankimon", types.ModuleType("Ankimon"))
    monkeypatch.setitem(sys.modules, "Ankimon.resources", _Resources())
    monkeypatch.setitem(sys.modules, "Ankimon.singletons", MagicMock())
    monkeypatch.setitem(sys.modules, "Ankimon.utils", MagicMock())
    monkeypatch.setitem(sys.modules, "Ankimon.pyobj", MagicMock())
    monkeypatch.setitem(sys.modules, "Ankimon.functions", types.ModuleType("Ankimon.functions"))
    monkeypatch.setitem(
        sys.modules, "Ankimon.ankimon_profile_web", types.ModuleType("Ankimon.ankimon_profile_web")
    )
    services_mod = types.ModuleType("Ankimon.services")
    services_mod.services = types.SimpleNamespace(
        db=None, main_pokemon=None, settings=None, logger=None, trainer_card=None, items_web=None
    )
    monkeypatch.setitem(sys.modules, "Ankimon.services", services_mod)
    monkeypatch.setitem(sys.modules, "Ankimon.pyobj.error_handler", MagicMock())
    monkeypatch.setitem(sys.modules, "Ankimon.pyobj.pokemon_obj", MagicMock())
    monkeypatch.setitem(sys.modules, "Ankimon.functions.learnset_retrieval", MagicMock())

    db_mod = _load("Ankimon.pyobj.database_manager", "Ankimon/pyobj/database_manager.py", monkeypatch)
    monkeypatch.setattr(db_mod, "user_path", tmp_path, raising=False)
    _load("Ankimon.functions.pokedex_functions", "Ankimon/functions/pokedex_functions.py", monkeypatch)
    team_mod = _load("Ankimon.functions.team_functions", "Ankimon/functions/team_functions.py", monkeypatch)
    profile_mod = _load(
        "Ankimon.ankimon_profile_web.profile_data", "Ankimon/ankimon_profile_web/profile_data.py", monkeypatch
    )

    db = db_mod.AnkimonDB(_Logger())
    for ind_id in ("a", "b", "c"):
        db.save_pokemon({"individual_id": ind_id, "name": "pikachu", "id": 25, "level": 5})
    settings = MagicMock()
    settings.get.return_value = None
    services_mod.services.db = db
    services_mod.services.settings = settings
    profile = profile_mod.ProfileData(tmp_path, None, settings, _Logger())
    return types.SimpleNamespace(db=db, profile=profile, team=team_mod, settings=settings)


def _team(db):
    """Ordered ids in the team table."""
    return [r["individual_id"] for r in db.get_team()]


def test_save_with_matching_loaded_team_writes(env):
    """A save whose loaded party still matches the stored one goes through."""
    env.db.save_team([{"individual_id": "a"}])
    res = env.profile.handle_save_team(["a", "b"], None, None, loaded_team_ids=["a"])
    assert res["ok"] is True and res["count"] == 2
    assert _team(env.db) == ["a", "b"]


def test_stale_save_is_refused_and_keeps_the_newer_party(env):
    """The page loaded [a]; the PC added c meanwhile; the page's save of [a, b] is refused."""
    env.db.save_team([{"individual_id": "a"}])
    assert env.team.add_to_party(env.db, "c").ok  # edit made in the Pokémon PC
    res = env.profile.handle_save_team(["a", "b"], "a", None, loaded_team_ids=["a"])
    assert res["ok"] is False and res.get("stale") is True
    assert "changed elsewhere" in res["message"] and "Choose Pokemon Team" in res["message"]
    assert _team(env.db) == ["a", "c"]  # the PC edit survived
    env.settings.set.assert_not_called()  # XP Share untouched too


def test_team_row_without_a_record_does_not_make_every_save_stale(env):
    """A stored id with no Pokémon row is invisible on the page but part of the
    raw ids it loads; the save must still go through when nothing changed."""
    env.db.save_team([{"individual_id": "a"}, {"individual_id": "ghost"}])
    payload = env.profile.get_team_data()
    assert [m["id"] for m in payload["team"]] == ["a"]  # ghost cannot be rendered
    assert payload["team_ids"] == ["a", "ghost"]  # but it is part of the loaded ids
    res = env.profile.handle_save_team(["a", "b"], None, None, loaded_team_ids=payload["team_ids"])
    assert res["ok"] is True, res
    assert _team(env.db) == ["a", "b"]  # the unreadable row is pruned by the save itself


def test_save_without_loaded_team_keeps_legacy_behaviour(env):
    """Callers that do not send the loaded party (older pages) still save."""
    env.db.save_team([{"individual_id": "a"}])
    res = env.profile.handle_save_team(["b"], None, None)
    assert res["ok"] is True
    assert _team(env.db) == ["b"]


def test_unreadable_team_table_refuses_the_save_without_calling_it_stale(env):
    """A team read failure is reported as a failure, not as a stale page."""
    env.db.save_team([{"individual_id": "a"}])
    original = env.db.get_team
    env.db.get_team = lambda: (_ for _ in ()).throw(RuntimeError("database is locked"))
    try:
        res = env.profile.handle_save_team(["a", "b"], None, None, loaded_team_ids=["a"])
    finally:
        env.db.get_team = original
    assert res["ok"] is False and not res.get("stale")
    assert _team(env.db) == ["a"]
