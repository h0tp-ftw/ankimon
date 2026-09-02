"""Anki's theme has to reach the HUD, and has to reach it when it *changes*.

The HUD is rendered inside a closed shadow root whose host is appended to
``<html>`` (``web/ankimon_hud_portal.js``), so none of Anki's theme signals can
reach it on their own. Anki announces a theme flip by evaluating a snippet that
toggles classes on ``<html>``/``<body>`` (``aqt.webview.AnkiWebView
.on_theme_did_change``) and never reloads the page — that snippet cannot cross
the shadow boundary, and ``<body>`` is not even an ancestor of the host.

So two things have to hold, and neither is visible from the stylesheet alone:
the emitted markup must carry the resolved theme as a class on ``#ankimon-hud``
itself, and ``Reviewer_Manager`` must subscribe to ``theme_did_change`` and
repaint. Without the subscription the HUD keeps the old palette until the next
answered card happens to repaint it — and for anyone running Anki's
"Automatic", that is a regression against the ``@media (prefers-color-scheme:
dark)`` rule this replaced, which repainted live.
"""

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

_src = Path(__file__).parent.parent / "src"
_orig_modules = {}


# Module (un)loading — snapshot/restore so this file cannot leak its mocks into
# the rest of the suite (same guard as test_reviewer_ownership_cache.py).
def setup_module():
    global _orig_modules
    _orig_modules = sys.modules.copy()


def teardown_module():
    for k in [k for k in sys.modules if k not in _orig_modules]:
        del sys.modules[k]
    sys.modules.update(_orig_modules)


class FakeHooks:
    """gui_hooks stub whose hooks are plain lists, so an unmatched remove()
    fails loudly instead of being absorbed by a MagicMock."""

    def __init__(self):
        self.reviewer_will_end = []
        self.reviewer_did_answer_card = []
        self.theme_did_change = []


class FakeCursor:
    def fetchone(self):
        return None


class FakeDB:
    def execute(self, sql, params=()):
        return FakeCursor()


class FakeSettings:
    def __init__(self):
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
            "gui.reviewer_text_message_box": False,
            "gui.review_hp_bar_thickness": 1,
            "gui.hud_hidden_on_startup": False,
            "misc.language": 9,
            "misc.remove_level_cap": False,
        }

    def get(self, key, default=None):
        from Ankimon.pyobj.settings import DEFAULT_CONFIG

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


def _load_reviewer_obj(hooks, fake_services):
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
    sys.modules["aqt"].gui_hooks = hooks

    services_mod = types.ModuleType("Ankimon.services")
    services_mod.services = fake_services
    sys.modules["Ankimon.services"] = services_mod
    events_mod = types.ModuleType("Ankimon.events")
    events_mod.events = MagicMock()
    sys.modules["Ankimon.events"] = events_mod

    for sub in [
        "Ankimon.pyobj.pokemon_obj",
        "Ankimon.functions.pokemon_functions",
        "Ankimon.functions.create_css_for_reviewer",
        "Ankimon.functions.create_gui_functions",
        "Ankimon.resources",
    ]:
        sys.modules[sub] = MagicMock()

    spec = importlib.util.spec_from_file_location(
        "Ankimon.pyobj.reviewer_obj", _src / "Ankimon" / "pyobj" / "reviewer_obj.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["Ankimon.pyobj.reviewer_obj"] = mod
    spec.loader.exec_module(mod)

    # Render helpers come from now-mocked modules; make them concrete so
    # update_life_bar runs end to end and emits real markup.
    mod.create_status_html = lambda *a, **k: ""
    mod.create_css_for_reviewer = lambda *a, **k: ""
    mod.find_experience_for_level = lambda *a, **k: 100
    return mod


def _manager(night_mode=False):
    hooks = FakeHooks()
    fake_services = types.SimpleNamespace(db=FakeDB(), settings=None, reviewer=None)
    mod = _load_reviewer_obj(hooks, fake_services)
    mod._anki_night_mode = lambda: night_mode
    mgr = mod.Reviewer_Manager(
        FakeSettings(), FakePokemon(1), FakePokemon(25), MagicMock()
    )
    return mod, mgr, hooks, fake_services


def _painted_markup(reviewer):
    """Everything the manager pushed into the webview this far."""
    return "".join(str(call.args[0]) for call in reviewer.web.eval.call_args_list)


def test_theme_did_change_repaints_the_hud():
    _, mgr, hooks, _ = _manager()
    assert mgr.refresh_hud in hooks.theme_did_change, (
        "Reviewer_Manager does not subscribe to theme_did_change, so flipping "
        "Anki's theme leaves the already-rendered HUD on the old palette — "
        "Anki's own class toggle cannot reach into the closed shadow root"
    )


def test_a_rebuilt_manager_does_not_double_subscribe():
    """Reload safety: the registry-anchored handler records must unregister the
    previous instance's theme handler, or an add-on reload repaints N times."""
    mod, _, hooks, fake_services = _manager()
    second = mod.Reviewer_Manager(
        FakeSettings(), FakePokemon(1), FakePokemon(25), MagicMock()
    )
    assert hooks.theme_did_change == [second.refresh_hud], (
        "a rebuilt Reviewer_Manager left the old instance subscribed to "
        "theme_did_change"
    )


def test_dark_mode_reaches_the_hud_as_a_class_on_the_hud_itself():
    _, mgr, _, _ = _manager(night_mode=True)
    reviewer = MagicMock()
    mgr.update_life_bar(reviewer, 0, 0)
    assert '<div id=\\"ankimon-hud\\" class=\\"night_mode\\">' in _painted_markup(
        reviewer
    ), "Anki's dark theme did not reach the emitted HUD markup"


def test_light_mode_emits_no_theme_class():
    _, mgr, _, _ = _manager(night_mode=False)
    reviewer = MagicMock()
    mgr.update_life_bar(reviewer, 0, 0)
    painted = _painted_markup(reviewer)
    # The stylesheet always ships BOTH palettes — the dark one is inert until
    # the class appears — so this asserts on the markup, not on the CSS.
    assert '<div id=\\"ankimon-hud\\">' in painted, (
        "the HUD did not emit its container in light mode"
    )
    assert 'class=\\"night_mode\\"' not in painted, (
        "the HUD claimed Anki's dark theme while Anki was in light mode"
    )


def test_flipping_the_theme_repaints_rather_than_being_cached_away():
    """The repaint cache short-circuits when nothing else about the encounter
    changed. A theme flip changes nothing *but* the theme, so the theme has to
    be part of the cache key or the repaint that theme_did_change triggers is
    silently dropped."""
    mod, mgr, _, _ = _manager(night_mode=False)
    reviewer = MagicMock()
    mgr.update_life_bar(reviewer, 0, 0)
    light_calls = len(reviewer.web.eval.call_args_list)

    mod._anki_night_mode = lambda: True
    mgr.update_life_bar(reviewer, 0, 0)

    assert len(reviewer.web.eval.call_args_list) > light_calls, (
        "the second repaint was skipped: the theme is missing from the HUD "
        "repaint cache key, so a theme flip alone never reaches the webview"
    )
    assert 'class=\\"night_mode\\"' in _painted_markup(reviewer), (
        "the repaint after the theme flip still carried the light palette"
    )
