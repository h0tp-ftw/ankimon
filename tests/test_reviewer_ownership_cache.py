"""Parity oracle for F34 — reviewer HUD ownership cache + team-cycle hotkeys.

Ported from BRRRR_Experimental's ``tests/test_reviewer_ownership_cache.py`` and
re-fitted to main's service seam:

* HUD ownership DB reads go through ``services.db`` (exp read ``mw.ankimon_db``).
* Team-cycle DB/settings reads go through ``services.db`` / ``services.settings``.
* The HUD repaint entry point is ``reviewer_obj.refresh_hud()`` (main/F22), so the
  ``new_pokemon(..., update_hud=True)`` contract is asserted against ``refresh_hud``
  rather than exp's direct ``update_life_bar(reviewer, 0, 0)`` call.

The exp oracle also exercised cache-invalidation hooks that live in
``pyobj/database_manager.py`` (``_clear_reviewer_ownership_cache`` /
``_all_pokemon_ids_cache`` / ``mark_as_caught``). Those hooks are not present on
main and ``database_manager.py`` is outside F34's manifest (shared DB hotspot),
so they are deferred to whichever unit owns that file — see the PR's Deferred
section. The primary invalidation path (a fresh encounter clears the cache) is
provided by ``new_pokemon`` on main and is covered here.
"""

import sys
import types
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_src = Path(__file__).parent.parent / "src"
_orig_modules = {}


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #
class FakeCursor:
    def __init__(self, found):
        self._found = found

    def fetchone(self):
        return (1,) if self._found else None


class FakeDB:
    """Minimal stand-in for AnkimonDB exposing what the HUD/team-cycle touch."""

    def __init__(self, owned_ids=(), team=None, pokemon=None):
        self.owned = set(owned_ids)
        self._team = team or []
        self._pokemon = pokemon or {}
        self.query_count = 0
        self.get_team_calls = 0
        self.get_pokemon_args = []

    def execute(self, sql, params=()):
        self.query_count += 1
        pid = params[0] if params else None
        return FakeCursor(pid in self.owned)

    def get_team(self):
        self.get_team_calls += 1
        return self._team

    def get_pokemon(self, individual_id):
        self.get_pokemon_args.append(individual_id)
        return self._pokemon


from Ankimon.pyobj.settings import DEFAULT_CONFIG

class FakeSettings:
    def __init__(self, overrides=None):
        self.values = {
            "gui.show_mainpkmn_in_reviewer": 0,
            "gui.reviewer_image_gif": False,
            "gui.hud_player_sprite": True,
            "gui.hud_enemy_sprite": True,
            "gui.hud_xp_bar": True,
            "gui.hud_hp_bars": True,
            "gui.hud_hp_text": True,
            "gui.hud_pokemon_id": True,
            "gui.hud_pokemon_gen": True,
            "gui.hud_pokemon_lvl": True,
            "gui.hud_pokemon_name": True,
            "gui.hud_status_badge": True,
            "gui.hud_owned_indicator": True,
            "gui.hud_enemy_shiny_indicator": True,
            "gui.hud_player_shiny_indicator": True,
            "misc.language": 9,
            "gui.review_hp_bar_thickness": 1,
            "gui.hud_hidden_on_startup": False,
            "misc.remove_level_cap": False,
            "controls.team_cycle_key": "9",
            "controls.team_cycle_count": 3,
        }
        if overrides:
            self.values.update(overrides)

    def get(self, key, default=None):
        if key in self.values:
            return self.values[key]
        return DEFAULT_CONFIG.get(key, default)

    def compute_special_variable(self, name):
        return 0


class FakePokemon:
    def __init__(self, pid=25):
        self.id = pid
        self.hp = 10
        self.max_hp = 20
        self.battle_status = ""
        self.shiny = False
        self.level = 5
        self.xp = 0
        self.display_name = "Pikachu"
        self.pokedex_id = pid
        self.generation = 1
        self.growth_rate = "medium"

    def get_sprite_path(self, side, image_format):
        return "/tmp/sprite." + image_format

    def to_engine_format(self):
        return {}


# --------------------------------------------------------------------------- #
# Module (un)loading — snapshot/restore so this file cannot leak mocks
# --------------------------------------------------------------------------- #
def setup_module():
    global _orig_modules
    _orig_modules = sys.modules.copy()


def teardown_module():
    for k in [k for k in sys.modules if k not in _orig_modules]:
        del sys.modules[k]
    sys.modules.update(_orig_modules)


def _install_common_mocks(fake_services):
    for name in [
        "aqt",
        "aqt.qt",
        "aqt.utils",
        "aqt.reviewer",
        "aqt.gui_hooks",
        "anki",
        "anki.hooks",
    ]:
        sys.modules[name] = MagicMock()

    services_mod = types.ModuleType("Ankimon.services")
    services_mod.services = fake_services
    sys.modules["Ankimon.services"] = services_mod

    events_mod = types.ModuleType("Ankimon.events")
    events_mod.events = MagicMock()
    sys.modules["Ankimon.events"] = events_mod


def _load_module(mod_name, rel_path):
    spec = importlib.util.spec_from_file_location(mod_name, _src / rel_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_reviewer_obj(fake_services):
    _install_common_mocks(fake_services)
    for sub in [
        "Ankimon.pyobj.pokemon_obj",
        "Ankimon.functions.pokemon_functions",
        "Ankimon.functions.create_css_for_reviewer",
        "Ankimon.functions.create_gui_functions",
        "Ankimon.resources",
    ]:
        sys.modules[sub] = MagicMock()

    mod = _load_module("Ankimon.pyobj.reviewer_obj", "Ankimon/pyobj/reviewer_obj.py")
    # Render helpers are imported from now-mocked modules; make them return
    # concrete strings/ints so update_life_bar runs end to end.
    mod.create_status_html = lambda *a, **k: ""
    mod.create_css_for_reviewer = lambda *a, **k: ""
    mod.find_experience_for_level = lambda *a, **k: 100
    return mod


def _new_manager(fake_services, enemy_id=25):
    mod = _load_reviewer_obj(fake_services)
    mgr = mod.Reviewer_Manager(
        FakeSettings(), FakePokemon(1), FakePokemon(enemy_id), MagicMock()
    )
    return mod, mgr


# --------------------------------------------------------------------------- #
# reviewer_obj: ownership cache semantics
# --------------------------------------------------------------------------- #
def test_ownership_cache_starts_empty():
    fake_services = types.SimpleNamespace(db=FakeDB(), settings=None, reviewer=None)
    _, mgr = _new_manager(fake_services)
    assert mgr._ownership_cache == {}
    assert mgr._last_state is None


def test_reset_clears_ownership_cache_and_last_state():
    fake_services = types.SimpleNamespace(db=FakeDB(), settings=None, reviewer=None)
    _, mgr = _new_manager(fake_services)
    mgr._ownership_cache[25] = True
    mgr._last_state = ("stale",)
    mgr.reviewer_reset_life_bar_inject()
    assert mgr._ownership_cache == {}
    assert mgr._last_state is None


def test_update_life_bar_populates_and_reuses_ownership_cache():
    db = FakeDB(owned_ids={25})
    fake_services = types.SimpleNamespace(db=db, settings=None, reviewer=None)
    _, mgr = _new_manager(fake_services, enemy_id=25)
    reviewer = MagicMock()

    mgr.update_life_bar(reviewer, 0, 0)
    assert mgr._ownership_cache == {25: True}
    assert db.query_count == 1

    # Second repaint of the same enemy uses the cache — no new DB query.
    mgr.update_life_bar(reviewer, 0, 0)
    assert db.query_count == 1


def test_reset_forces_a_fresh_ownership_query():
    db = FakeDB(owned_ids={25})
    fake_services = types.SimpleNamespace(db=db, settings=None, reviewer=None)
    _, mgr = _new_manager(fake_services, enemy_id=25)
    reviewer = MagicMock()

    mgr.update_life_bar(reviewer, 0, 0)
    assert db.query_count == 1
    mgr.reviewer_reset_life_bar_inject()
    mgr.update_life_bar(reviewer, 0, 0)
    assert db.query_count == 2


def test_ownership_read_goes_through_services_db_not_mw():
    # A fresh manager whose services.db is a distinct object; the query must land
    # on services.db (the seam), proving the read was re-fit off mw.ankimon_db.
    db = FakeDB(owned_ids=set())
    fake_services = types.SimpleNamespace(db=db, settings=None, reviewer=None)
    _, mgr = _new_manager(fake_services, enemy_id=99)
    mgr.update_life_bar(MagicMock(), 0, 0)
    assert db.query_count == 1
    assert mgr._ownership_cache == {99: False}


def test_ease_guard_blocks_answer_hook_calls():
    db = FakeDB(owned_ids={25})
    fake_services = types.SimpleNamespace(db=db, settings=None, reviewer=None)
    _, mgr = _new_manager(fake_services, enemy_id=25)
    reviewer = MagicMock()

    # reviewer_did_answer_card fires with a real Card + non-zero ease; guarded off.
    mgr.update_life_bar(reviewer, card=object(), ease=3)
    reviewer.web.eval.assert_not_called()
    assert db.query_count == 0


def test_card_object_guard_blocks_hook_calls():
    db = FakeDB(owned_ids={25})
    fake_services = types.SimpleNamespace(db=db, settings=None, reviewer=None)
    _, mgr = _new_manager(fake_services, enemy_id=25)
    reviewer = MagicMock()

    # ease==0 but a Card object (not int) sneaks in -> still guarded off.
    mgr.update_life_bar(reviewer, card=object(), ease=0)
    reviewer.web.eval.assert_not_called()
    assert db.query_count == 0


# --------------------------------------------------------------------------- #
# reviewer_ui: team-cycle + hotkeys + idempotent wrapping
# --------------------------------------------------------------------------- #
class _DummyReviewerFactory:
    """Fresh Reviewer class per test so ``_ankimon_orig_*`` never leaks across."""

    @staticmethod
    def make():
        class DummyReviewer:
            def _shortcutKeys(self):
                return [("x", lambda: None)]

            def _linkHandler(self, url):
                return False

            def _bottomHTML(self):
                return "base"

        return DummyReviewer


def _make_fake_wrap(counter):
    def fake_wrap(old, new, pos="after"):
        counter.append((old, new, pos))

        def wrapped(self, *a, **k):
            return new(self, *a, _old=old, **k)

        return wrapped

    return fake_wrap


def _load_reviewer_ui(fake_services, dummy_reviewer=None, wrap_counter=None):
    _install_common_mocks(fake_services)

    # anki.hooks.wrap + aqt.reviewer.Reviewer must be real-ish for the wrapping.
    if wrap_counter is not None:
        sys.modules["anki.hooks"].wrap = _make_fake_wrap(wrap_counter)
    if dummy_reviewer is not None:
        sys.modules["aqt.reviewer"].Reviewer = dummy_reviewer

    singletons = types.ModuleType("Ankimon.singletons")
    for attr in (
        "enemy_pokemon",
        "main_pokemon",
        "ankimon_tracker_obj",
        "get_test_window",
        "get_evo_window",
        "logger",
        "achievements",
        "trainer_card",
        "reviewer_obj",
    ):
        setattr(singletons, attr, MagicMock())
    sys.modules["Ankimon.singletons"] = singletons

    for sub in [
        "Ankimon.functions.encounter_functions",
        "Ankimon.functions.update_main_pokemon",
        "Ankimon.functions.pokedex_functions",
        "Ankimon.texts",
        "Ankimon.utils",
    ]:
        sys.modules[sub] = MagicMock()

    return _load_module("Ankimon.reviewer_ui", "Ankimon/reviewer_ui.py")


def test_resolve_team_cycle_key_explicit_and_lowercased():
    fake_services = types.SimpleNamespace(db=None, settings=FakeSettings())
    ui = _load_reviewer_ui(fake_services)
    assert ui._resolve_team_cycle_key("7") == "7"
    assert ui._resolve_team_cycle_key("K") == "k"


def test_resolve_team_cycle_key_defaults_from_services_settings():
    fake_services = types.SimpleNamespace(
        db=None, settings=FakeSettings({"controls.team_cycle_key": "3"})
    )
    ui = _load_reviewer_ui(fake_services)
    # 4th arg omitted (None) -> read controls.team_cycle_key via the seam.
    assert ui._resolve_team_cycle_key(None) == "3"


def test_setup_reviewer_ui_three_and_four_args_idempotent():
    fake_services = types.SimpleNamespace(db=None, settings=FakeSettings())
    dummy = _DummyReviewerFactory.make()
    orig_sk = dummy._shortcutKeys
    orig_lh = dummy._linkHandler
    orig_bh = dummy._bottomHTML
    counter = []
    ui = _load_reviewer_ui(fake_services, dummy_reviewer=dummy, wrap_counter=counter)

    # 4-arg call (settings_window style).
    ui.setup_reviewer_ui("6", "5", True, "9")
    assert len(counter) == 3  # shortcutKeys + linkHandler + bottomHTML, once each
    assert dummy._ankimon_orig_shortcutKeys is orig_sk
    assert dummy._ankimon_orig_linkHandler is orig_lh
    assert dummy._ankimon_orig_bottomHTML is orig_bh
    assert ui._current_keys["team_cycle"] == "9"

    # 3-arg call (base __init__ style) must still work and NOT re-wrap.
    ui.setup_reviewer_ui("7", "5", True)
    assert len(counter) == 3  # no double-wrap
    assert ui._current_keys["catch"] == "7"
    # 4th arg defaulted from services.settings.
    assert ui._current_keys["team_cycle"] == "9"


def test_setup_reviewer_ui_reload_does_not_double_wrap():
    # An add-on reload re-execs reviewer_ui (module guard flags reset to False)
    # while the Reviewer *class* persists in memory with the first boot's wrappers
    # still installed. The pristine method must be restored before re-wrapping, or
    # every Ankimon shortcut would be appended twice (fires twice per keypress).
    fake_services = types.SimpleNamespace(db=None, settings=FakeSettings())
    dummy = _DummyReviewerFactory.make()
    orig_sk = dummy._shortcutKeys
    counter = []

    ui = _load_reviewer_ui(fake_services, dummy_reviewer=dummy, wrap_counter=counter)
    ui.is_dev_mode = lambda: False
    ui.setup_reviewer_ui("6", "5", True, "9")

    # Second boot: same persistent Reviewer class, fresh module namespace.
    ui2 = _load_reviewer_ui(fake_services, dummy_reviewer=dummy, wrap_counter=counter)
    ui2.is_dev_mode = lambda: False
    ui2.setup_reviewer_ui("6", "5", True, "9")

    # Pristine method preserved across reloads (reload-teardown contract).
    assert dummy._ankimon_orig_shortcutKeys is orig_sk
    # The live _shortcutKeys wraps the pristine ONCE: each Ankimon shortcut is
    # appended exactly once. A stacked (double) wrap would list every key twice.
    keys = dummy._shortcutKeys(dummy())
    ankimon_keys = sorted(k for k, _ in keys if k in ("6", "5", "9"))
    assert ankimon_keys == ["5", "6", "9"]


def test_cycle_team_pokemon_reads_services_db_and_refreshes_hud():
    db = FakeDB(
        team=[{"individual_id": "a"}, {"individual_id": "b"}],
        pokemon={"id": 25, "name": "Pikachu", "level": 5},
    )
    fake_services = types.SimpleNamespace(
        db=db, settings=FakeSettings({"controls.team_cycle_count": 3})
    )
    ui = _load_reviewer_ui(fake_services)
    # base_stats guard needs a valid dict back from the (mocked) pokedex lookup.
    sys.modules["Ankimon.functions.pokedex_functions"].search_pokedex.return_value = {
        "hp": 45,
        "atk": 49,
        "def": 49,
        "spa": 65,
        "spd": 65,
        "spe": 90,
    }
    ui.main_pokemon = MagicMock()
    ui.main_pokemon.individual_id = "a"  # active pokemon is team slot 0
    ui.reviewer_obj = MagicMock()

    ui.cycle_team_pokemon()

    assert db.get_team_calls == 1
    # active pokemon is slot 0, so cycling advances to slot 1 ('b').
    assert db.get_pokemon_args == ["b"]
    ui.reviewer_obj.refresh_hud.assert_called_once()


def test_cycle_team_pokemon_syncs_index_to_active_pokemon():
    # Regression for the team-cycle index desync: if the active pokemon is
    # swapped in outside this hotkey (Team Select / PC), cycling must advance to
    # the member AFTER the active one, not blindly increment a stale index.
    db = FakeDB(
        team=[
            {"individual_id": "a"},
            {"individual_id": "b"},
            {"individual_id": "c"},
        ],
        pokemon={"id": 25, "name": "Pikachu", "level": 5},
    )
    fake_services = types.SimpleNamespace(
        db=db, settings=FakeSettings({"controls.team_cycle_count": 3})
    )
    ui = _load_reviewer_ui(fake_services)
    sys.modules["Ankimon.functions.pokedex_functions"].search_pokedex.return_value = {
        "hp": 45,
        "atk": 49,
        "def": 49,
        "spa": 65,
        "spd": 65,
        "spe": 90,
    }
    ui.reviewer_obj = MagicMock()
    ui.main_pokemon = MagicMock()
    ui.main_pokemon.individual_id = "b"  # active pokemon is slot 1
    ui._team_cycle_index = 0  # stale running index (would wrongly go to 'b')

    ui.cycle_team_pokemon()

    # Synced to slot 1 ('b'), then advanced -> slot 2 ('c').
    assert db.get_pokemon_args == ["c"]


def test_cycle_team_pokemon_missing_base_stats_aborts_without_mutation():
    # search_pokedex returns [] (not None) for an unknown name; the guard must
    # abort before update_stats so main_pokemon is never partially mutated.
    db = FakeDB(
        team=[{"individual_id": "a"}, {"individual_id": "b"}],
        pokemon={"id": 25, "name": "Pikachu", "level": 5},
    )
    fake_services = types.SimpleNamespace(
        db=db, settings=FakeSettings({"controls.team_cycle_count": 3})
    )
    ui = _load_reviewer_ui(fake_services)
    sys.modules["Ankimon.functions.pokedex_functions"].search_pokedex.return_value = []
    ui.main_pokemon = MagicMock()
    ui.main_pokemon.individual_id = "a"
    ui.reviewer_obj = MagicMock()

    ui.cycle_team_pokemon()

    ui.main_pokemon.update_stats.assert_not_called()
    ui.reviewer_obj.refresh_hud.assert_not_called()


def test_cycle_team_pokemon_disabled_when_count_le_one():
    db = FakeDB(team=[{"individual_id": "a"}, {"individual_id": "b"}])
    fake_services = types.SimpleNamespace(
        db=db, settings=FakeSettings({"controls.team_cycle_count": 1})
    )
    ui = _load_reviewer_ui(fake_services)
    ui.reviewer_obj = MagicMock()

    ui.cycle_team_pokemon()

    assert db.get_team_calls == 0
    ui.reviewer_obj.refresh_hud.assert_not_called()


def test_hotkey_0_triggers_new_encounter_with_update_hud():
    fake_services = types.SimpleNamespace(db=None, settings=FakeSettings())
    ui = _load_reviewer_ui(fake_services)
    ui.is_dev_mode = lambda: True
    ui.new_pokemon = MagicMock()

    ui.test_encounter_shortcut_function()

    ui.new_pokemon.assert_called_once()
    _, kwargs = ui.new_pokemon.call_args
    assert kwargs.get("update_hud") is True


def test_hotkey_0_noop_when_not_dev_mode():
    fake_services = types.SimpleNamespace(db=None, settings=FakeSettings())
    ui = _load_reviewer_ui(fake_services)
    ui.is_dev_mode = lambda: False
    ui.new_pokemon = MagicMock()

    ui.test_encounter_shortcut_function()

    ui.new_pokemon.assert_not_called()


# --------------------------------------------------------------------------- #
# new_pokemon integration — the F22 contract F34's cache relies on
# --------------------------------------------------------------------------- #
def _load_encounter_functions():
    for name in ["aqt", "aqt.qt", "aqt.utils"]:
        sys.modules[name] = MagicMock()
    for module in [
        "Ankimon.pyobj.ankimon_tracker",
        "Ankimon.pyobj.pokemon_obj",
        "Ankimon.pyobj.reviewer_obj",
        "Ankimon.pyobj.test_window",
        "Ankimon.pyobj.trainer_card",
        "Ankimon.pyobj.InfoLogger",
        "Ankimon.pyobj.evolution_window",
        "Ankimon.pyobj.attack_dialog",
        "Ankimon.pyobj.translator",
        "Ankimon.pyobj.error_handler",
        "Ankimon.functions.pokemon_functions",
        "Ankimon.functions.pokedex_functions",
        "Ankimon.functions.trainer_functions",
        "Ankimon.functions.badges_functions",
        "Ankimon.functions.drawing_utils",
        "Ankimon.functions.friendship_evolution",
        "Ankimon.functions.encounter_data",
        "Ankimon.utils",
        "Ankimon.business",
        "Ankimon.const",
        "Ankimon.singletons",
        "Ankimon.resources",
        "Ankimon.services",
        "Ankimon.events",
    ]:
        sys.modules[module] = MagicMock()

    # new_pokemon only needs a canned generate_random_pokemon (patched below) plus
    # the reviewer/services/events seams — no real pokedex/encounter data — so the
    # heavy data modules stay mocked. This keeps the load order-independent.
    ef = _load_module(
        "Ankimon.functions.encounter_functions",
        "Ankimon/functions/encounter_functions.py",
    )
    ef.main_pokemon = MagicMock()
    ef.settings_obj = MagicMock()
    ef.ankimon_tracker_obj = MagicMock()
    ef.trainer_card = MagicMock()
    ef.services = MagicMock()
    ef.events = MagicMock()
    # Canned generator so new_pokemon does no RNG work.
    ef.generate_random_pokemon = lambda *a, **k: (
        "Pikachu",
        25,
        5,
        "static",
        ["electric"],
        {},
        [],
        112,
        "medium",
        {},
        {},
        "N",
        "",
        {},
        "Normal",
        {},
        False,
        "hardy",
    )
    return ef


class _ReviewerStub:
    def __init__(self):
        self._ownership_cache = {}
        self._last_state = ("stale",)
        self.refresh_hud = MagicMock()

    def invalidate_hud_cache(self):
        # Mirrors Reviewer_Manager.invalidate_hud_cache: new_pokemon now clears
        # the HUD perf caches through this public method instead of poking the
        # private attributes directly.
        self._last_state = None
        self._ownership_cache.clear()


def test_new_pokemon_update_hud_clears_cache_and_refreshes():
    ef = _load_encounter_functions()
    reviewer = _ReviewerStub()
    reviewer._ownership_cache[25] = False

    ef.new_pokemon(MagicMock(), None, MagicMock(), reviewer, update_hud=True)

    assert reviewer._ownership_cache == {}
    assert reviewer._last_state is None
    reviewer.refresh_hud.assert_called_once()


def test_new_pokemon_without_update_hud_still_clears_cache_but_no_repaint():
    ef = _load_encounter_functions()
    reviewer = _ReviewerStub()
    reviewer._ownership_cache[25] = True

    ef.new_pokemon(MagicMock(), None, MagicMock(), reviewer, update_hud=False)

    assert reviewer._ownership_cache == {}
    reviewer.refresh_hud.assert_not_called()
