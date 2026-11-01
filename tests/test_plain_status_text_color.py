"""Plain-mode status text must not reuse the styled badge's text colour.

`status_colors_html["fainted"]` sets text_color #FFFFFF for the styled badge's
black background. Plain mode (gui.hud_styling off) draws no background, so
forcing that colour made "— FAINTED" invisible on Anki's light theme.
"""

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

SRC = Path(__file__).resolve().parents[1] / "src" / "Ankimon"

_saved = {}


def setup_module():
    _saved.update(sys.modules)


def teardown_module():
    for name in [n for n in sys.modules if n not in _saved]:
        del sys.modules[name]
    sys.modules.update(_saved)


def _load_gui_functions():
    for name in ("PyQt6", "PyQt6.QtWidgets", "PyQt6.QtCore", "PyQt6.QtGui"):
        sys.modules[name] = MagicMock()
    pkg = types.ModuleType("_plain_status_pkg")
    pkg.__path__ = [str(SRC)]
    sys.modules["_plain_status_pkg"] = pkg
    functions_pkg = types.ModuleType("_plain_status_pkg.functions")
    functions_pkg.__path__ = [str(SRC / "functions")]
    sys.modules["_plain_status_pkg.functions"] = functions_pkg
    for name, path in (
        ("_plain_status_pkg.const", SRC / "const.py"),
        ("_plain_status_pkg.functions.create_gui_functions", SRC / "functions" / "create_gui_functions.py"),
    ):
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules["_plain_status_pkg.functions.create_gui_functions"]


class _Settings:
    def __init__(self, **values):
        self.values = {
            "gui.review_hp_bar_thickness": 2,
            "gui.show_mainpkmn_in_reviewer": 1,
            "gui.hud_status_badge": True,
            "gui.hud_owned_indicator": True,
            **values,
        }

    def get(self, key, default=None):
        return self.values.get(key, default)

    def compute_special_variable(self, key):
        return 0


def test_plain_fainted_status_inherits_text_colour():
    gui = _load_gui_functions()
    html = gui.create_status_html("fainted", _Settings(), plain_text=True)
    assert "— FAINTED" in html
    assert "#FFFFFF" not in html.upper()
    assert 'id="pokestatus" style=' not in html


def test_plain_status_still_shows_captured_once():
    gui = _load_gui_functions()
    html = gui.create_status_html("brn", _Settings(), is_pokemon_owned=True, plain_text=True)
    assert "— BURNED" in html
    assert html.count("[CAPTURED]") == 1


def test_styled_fainted_badge_keeps_its_white_on_black_colours():
    gui = _load_gui_functions()
    html = gui.create_status_html("fainted", _Settings(), plain_text=False)
    assert "#FFFFFF" in html.upper()
