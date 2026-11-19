"""Regression test for the Active Companion clearing bug: handle_save_team()
used to treat ANY falsy companion_id (including "team.js never touched the
companion picker this save") as an explicit "clear the main Pokémon" —
meaning every ordinary team save that didn't touch the crown (reordering the
team, swapping an unrelated slot, XP Share only) wiped whatever main Pokémon
was already set, even one assigned through an older pathway (starter
selection, PC box) team.js never knew about.

The fix: team.js now sends a distinct ``_COMPANION_UNCHANGED`` sentinel when
the companion selection was never touched this session, and handle_save_team
leaves the DB's is_main row completely alone in that case.

A companion *change* never ends with the game having no battler at all,
either — ``update_main_pokemon()`` falls back to ``MAIN_POKEMON_DEFAULT`` (the
level-5 Ditto named "Please Restart Anki") the moment ``get_main_pokemon()``
returns None, so any path to zero ``is_main=1`` rows is user-hostile. An
explicit clear therefore promotes the first member of the team being saved, and
the two changes that cannot be honoured — an id that isn't in the team being
saved, and a clear with an empty team — both leave the existing is_main row
exactly where it is. handle_save_team never drops it.

Loads profile_data.py directly with its own dependencies stubbed (it's a
plain, non-Qt data layer per its own module docstring), so this pins the
branching logic without needing a real Qt/services stack.
"""

import importlib.util
import re
import sys
import types
from pathlib import Path

import pytest

_SRC = Path(__file__).parent.parent / "src"
_MODULE_NAME = "Ankimon.ankimon_profile_web.profile_data"


class _FakeDB:
    def __init__(self, set_main_result=True):
        self.saved_team = None
        self.set_main_calls = []
        self.clear_main_calls = 0
        # The real set_main_pokemon() returns False when individual_id has no
        # captured_pokemon row, which handle_save_team has to treat as "nothing
        # was written" rather than a successful change.
        self.set_main_result = set_main_result

    def save_team(self, team_data):
        self.saved_team = team_data

    def set_main_pokemon(self, individual_id):
        self.set_main_calls.append(individual_id)
        return self.set_main_result

    def clear_main_pokemon(self):
        self.clear_main_calls += 1


class _FakeTestWindow:
    """Stands in for the Ankimon Window on the companion repaint branch.

    Only the attributes that branch touches: ``is_alive()`` probes
    ``objectName()``, and the repaint is gated on visibility + the battle view.
    """

    def __init__(self, *, visible=True, current_view="battle"):
        self._visible = visible
        self.current_view = current_view
        self.main_pokemon = None
        self.force_display_calls = 0

    def objectName(self):
        return "AnkimonWindow"

    def isVisible(self):
        return self._visible

    def force_display_battle(self):
        self.force_display_calls += 1


class _FakeSettings:
    def __init__(self):
        self.values = {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, val):
        self.values[key] = val


@pytest.fixture
def pd_module(monkeypatch, tmp_path):
    # Every sys.modules write below goes through monkeypatch.setitem (and
    # delitem for the module actually loaded fresh) so pytest restores the
    # real entries after this test, instead of leaking these fakes into
    # whatever test file happens to run next in the same session — the same
    # cross-file sys.modules pollution pattern this suite has bitten on
    # before (see test_pokemon_details_gui.py's own isolation notes).
    for name in ("Ankimon", "Ankimon.ankimon_profile_web"):
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(_SRC / name.replace(".", "/"))]
        pkg.__package__ = name
        monkeypatch.setitem(sys.modules, name, pkg)

    services_mod = types.ModuleType("Ankimon.services")
    fake_services = types.SimpleNamespace(
        db=_FakeDB(),
        main_pokemon=None,
        reviewer=None,
        test_window=None,
    )
    services_mod.services = fake_services
    monkeypatch.setitem(sys.modules, "Ankimon.services", services_mod)

    utils_mod = types.ModuleType("Ankimon.utils")
    utils_mod.get_all_sprites = lambda *a, **k: []
    utils_mod.POKEMON_NAME_LOOKUP = {}
    # handle_save_team does `from ..utils import is_alive` inside a bare
    # `except Exception: pass` on the companion repaint branch. Without this
    # stub that import raises ImportError, the except swallows it, and the
    # repaint branch silently no-ops on EVERY test in this file — a regression
    # there would never be caught.
    utils_mod.is_alive = lambda obj: obj is not None
    monkeypatch.setitem(sys.modules, "Ankimon.utils", utils_mod)

    resources_mod = types.ModuleType("Ankimon.resources")
    resources_mod.trainer_sprites_path = tmp_path
    monkeypatch.setitem(sys.modules, "Ankimon.resources", resources_mod)

    for name in ("Ankimon.functions",):
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(_SRC / name.replace(".", "/"))]
        pkg.__package__ = name
        monkeypatch.setitem(sys.modules, name, pkg)

    update_main_pokemon_mod = types.ModuleType("Ankimon.functions.update_main_pokemon")
    update_main_pokemon_mod.update_main_pokemon = lambda *a, **k: None
    monkeypatch.setitem(
        sys.modules, "Ankimon.functions.update_main_pokemon", update_main_pokemon_mod
    )

    monkeypatch.delitem(sys.modules, _MODULE_NAME, raising=False)
    spec = importlib.util.spec_from_file_location(
        _MODULE_NAME, _SRC / "Ankimon" / "ankimon_profile_web" / "profile_data.py"
    )
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, _MODULE_NAME, mod)
    spec.loader.exec_module(mod)

    yield mod, fake_services


def _make_pd(pd_module):
    mod, _ = pd_module
    return mod.ProfileData(
        # Never read/written in the handle_save_team path this file exercises
        # — just needs to be a real Path, not an actual writable location.
        addon_dir=Path(__file__).parent,
        trainer_card=None,
        settings_obj=_FakeSettings(),
        logger=None,
    )


def test_unchanged_sentinel_leaves_existing_main_pokemon_alone(pd_module):
    """The core regression: a normal save (companion never touched) must NOT
    clear an existing main Pokémon."""
    mod, fake_services = pd_module
    pd = _make_pd(pd_module)

    result = pd.handle_save_team(
        ["a", "b"], "", mod.ProfileData._COMPANION_UNCHANGED
    )

    assert result["ok"] is True
    assert fake_services.db.clear_main_calls == 0
    assert fake_services.db.set_main_calls == []


def test_empty_companion_when_touched_promotes_the_first_team_member(pd_module):
    """An explicit clear means "somebody else battles now", not "nobody does".

    Un-crowning used to call clear_main_pokemon(), leaving zero is_main rows —
    and the next Anki start then handed the game MAIN_POKEMON_DEFAULT, the
    level-5 Ditto named "Please Restart Anki". The same destructive path was
    reachable without ever touching the crown: team.js marks the companion
    touched and sends "" whenever the slot holding it is removed or replaced,
    which is an ordinary team edit.
    """
    mod, fake_services = pd_module
    pd = _make_pd(pd_module)

    result = pd.handle_save_team(["a", "b"], "", "")

    assert result["ok"] is True
    assert fake_services.db.set_main_calls == ["a"]
    assert fake_services.db.clear_main_calls == 0


def test_empty_companion_with_an_empty_team_leaves_main_pokemon_alone(pd_module):
    """Clearing the last team member must not leave the game with no battler.

    There is nobody to promote here, so the save simply doesn't touch is_main.
    Dropping the row instead would hand the next Anki start
    MAIN_POKEMON_DEFAULT — a stale-but-real battler beats the placeholder
    Ditto in every case, and a player who empties their whole team to rebuild
    it should not lose their Pokémon over it.
    """
    mod, fake_services = pd_module
    pd = _make_pd(pd_module)

    result = pd.handle_save_team([], "", "")

    assert result["ok"] is True
    assert fake_services.db.clear_main_calls == 0
    assert fake_services.db.set_main_calls == []
    # Nothing authoritative happened, so nothing to report back to team.js.
    assert "companion" not in result


def test_handle_save_team_never_clears_the_main_pokemon(pd_module):
    """No input reaches clear_main_pokemon() — the placeholder-Ditto path is
    closed off entirely, not just narrowed."""
    mod, fake_services = pd_module
    pd = _make_pd(pd_module)

    for team_ids, companion in (
        (["a", "b"], mod.ProfileData._COMPANION_UNCHANGED),
        (["a", "b"], ""),
        (["a", "b"], "a"),
        (["a", "b"], "not-on-team"),
        ([], ""),
        ([], mod.ProfileData._COMPANION_UNCHANGED),
        ([], "not-on-team"),
    ):
        assert pd.handle_save_team(team_ids, "", companion)["ok"] is True

    assert fake_services.db.clear_main_calls == 0


def test_the_resulting_companion_is_reported_back_to_team_js(pd_module):
    """A clear can come back as a promotion, so the page has to be told what
    the save actually left set or its crown silently diverges from the DB."""
    mod, fake_services = pd_module
    pd = _make_pd(pd_module)

    assert pd.handle_save_team(["a", "b"], "", "b")["companion"] == "b"
    assert pd.handle_save_team(["a", "b"], "", "")["companion"] == "a"
    # Nothing authoritative: no key at all, so team.js keeps what it has.
    assert "companion" not in pd.handle_save_team(
        ["a", "b"], "", mod.ProfileData._COMPANION_UNCHANGED
    )
    assert "companion" not in pd.handle_save_team(["a", "b"], "", "not-on-team")


def test_valid_companion_sets_main_pokemon(pd_module):
    mod, fake_services = pd_module
    pd = _make_pd(pd_module)

    result = pd.handle_save_team(["a", "b"], "", "a")

    assert result["ok"] is True
    assert fake_services.db.set_main_calls == ["a"]
    assert fake_services.db.clear_main_calls == 0


def test_setting_the_companion_repaints_an_open_ankimon_window(pd_module):
    """The repaint branch is real code, so pin that it actually runs.

    It sits behind `from ..utils import is_alive` inside a bare
    `except Exception: pass`, which means any import or attribute error in
    there degrades to a silent no-op — the failure mode is "the crown changes
    but the battle scene keeps showing the old battler until the next turn",
    which no other assertion in this file would notice.
    """
    mod, fake_services = pd_module
    window = _FakeTestWindow()
    fake_services.test_window = window
    # A distinct sentinel, NOT the None both sides already default to: the
    # window has to receive the battler this save promoted, and `is None`
    # would hold just as well if the branch never assigned anything at all.
    battler = object()
    fake_services.main_pokemon = battler
    pd = _make_pd(pd_module)

    result = pd.handle_save_team(["a", "b"], "", "b")

    assert result["ok"] is True
    assert fake_services.db.set_main_calls == ["b"]
    assert window.force_display_calls == 1
    assert window.main_pokemon is battler


def test_a_window_off_the_battle_view_is_updated_but_not_repainted(pd_module):
    """The death/catch screen must not be painted over by a team save.

    The window still takes the new battler — it just isn't repainted until it
    is back on the battle view, so the next render shows the right Pokémon.
    """
    mod, fake_services = pd_module
    window = _FakeTestWindow(current_view="death")
    fake_services.test_window = window
    battler = object()
    fake_services.main_pokemon = battler
    pd = _make_pd(pd_module)

    assert pd.handle_save_team(["a", "b"], "", "b")["ok"] is True
    assert window.main_pokemon is battler
    assert window.force_display_calls == 0


def test_a_vanished_companion_row_is_not_reported_as_a_change(pd_module):
    """set_main_pokemon() returning False means nothing was written.

    companion_id is only validated against the team team.js just sent, never
    against captured_pokemon — so an id released (or dropped by a stale roster
    cache) between page load and save reaches the DB and is refused there. The
    old is_main row still stands, so this save changed nothing: reporting a
    ``companion`` back would make team.js park its crown on a Pokémon the DB
    never accepted, and the repaint would advertise a switch that didn't
    happen.
    """
    mod, fake_services = pd_module
    fake_services.db.set_main_result = False
    window = _FakeTestWindow()
    fake_services.test_window = window
    pd = _make_pd(pd_module)

    result = pd.handle_save_team(["a", "b"], "", "a")

    assert result["ok"] is True
    assert fake_services.db.set_main_calls == ["a"]  # it was attempted...
    assert "companion" not in result  # ...and reported as unchanged
    assert window.force_display_calls == 0
    assert fake_services.db.clear_main_calls == 0


def test_companion_not_in_saved_team_is_rejected_without_touching_is_main(pd_module):
    """A companion id that isn't part of the team being saved is bad INPUT.

    It must not be set (it isn't on the team) — but it must not be treated as
    a clear either: a bridge race, a stale cached team.js or a third-party
    caller sending a known-good-but-benched id would otherwise delete the
    player's battler. Reject the field, change nothing.
    """
    mod, fake_services = pd_module
    pd = _make_pd(pd_module)

    result = pd.handle_save_team(["a", "b"], "", "not-on-team")

    assert result["ok"] is True
    assert fake_services.db.set_main_calls == []
    assert fake_services.db.clear_main_calls == 0


def test_sentinel_literal_is_mirrored_in_team_js():
    """The protocol sentinel is hand-duplicated in team.js and profile_data.py.

    Nothing at runtime asserts the two literals match, and a rename on either
    side silently makes ``companion_touched`` True on every ordinary save —
    resurrecting the exact regression this file exists to pin. Read the JS off
    disk and require the Python literal to appear in it.
    """
    profile_data_src = (
        _SRC / "Ankimon" / "ankimon_profile_web" / "profile_data.py"
    ).read_text(encoding="utf-8")
    team_js = (_SRC / "Ankimon" / "ankimon_profile_web" / "team.js").read_text(
        encoding="utf-8"
    )

    # Pulled straight out of the source text so this test needs none of the
    # module's import-time dependencies stubbed.
    match = re.search(
        r"_COMPANION_UNCHANGED\s*=\s*[\"\'](?P<literal>[^\"\']+)[\"\']",
        profile_data_src,
    )
    assert match, "profile_data.py no longer defines _COMPANION_UNCHANGED"
    literal = match.group("literal")

    assert f"'{literal}'" in team_js or f'"{literal}"' in team_js, (
        f"team.js does not send the {literal!r} sentinel profile_data.py expects "
        "— the two hand-duplicated literals have diverged"
    )
