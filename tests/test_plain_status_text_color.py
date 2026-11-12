"""Plain-mode status text must not reuse the styled badge's text colour.

`status_colors_html["fainted"]` sets text_color #FFFFFF for the styled badge's
black background. Plain mode (gui.hud_styling off) draws no background and
`#hud-root` is reset with `all: initial !important`, so text colour does not
inherit from Anki's card. The fix emits an explicit, theme-aware colour for
plain-mode status text so "— FAINTED" stays readable in both light and dark
themes.
"""

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "Ankimon"


@pytest.fixture
def gui_module(monkeypatch):
    """Load create_gui_functions with only the stubs this test owns.

    Use ``monkeypatch.setitem`` so the module cache is restored per-test
    without snapshotting and rewriting the whole ``sys.modules`` table. This
    keeps the collection-time ``MockResources``/``MagicMock`` entries (set by
    ``restore_package_stubs`` and used by other module-scoped fixtures) intact.
    """
    for name in ("PyQt6", "PyQt6.QtWidgets", "PyQt6.QtCore", "PyQt6.QtGui"):
        monkeypatch.setitem(sys.modules, name, MagicMock())

    pkg = types.ModuleType("_plain_status_pkg")
    pkg.__path__ = [str(SRC)]
    monkeypatch.setitem(sys.modules, "_plain_status_pkg", pkg)

    functions_pkg = types.ModuleType("_plain_status_pkg.functions")
    functions_pkg.__path__ = [str(SRC / "functions")]
    monkeypatch.setitem(sys.modules, "_plain_status_pkg.functions", functions_pkg)

    for name, path in (
        ("_plain_status_pkg.const", SRC / "const.py"),
        (
            "_plain_status_pkg.functions.create_gui_functions",
            SRC / "functions" / "create_gui_functions.py",
        ),
    ):
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, mod)
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


def test_plain_fainted_status_does_not_reuse_badge_text_colour(gui_module):
    """Plain mode must not emit the styled badge's #FFFFFF, which is invisible
    on Anki's light theme once #hud-root has been reset by ``all: initial``."""
    html = gui_module.create_status_html("fainted", _Settings(), plain_text=True)
    assert "— FAINTED" in html
    assert "#FFFFFF" not in html.upper()


def test_plain_status_emits_explicit_theme_aware_colour(gui_module):
    """The span must carry an explicit inline colour rather than relying on
    inheritance through #hud-root, which is reset by ``all: initial``."""
    html = gui_module.create_status_html("fainted", _Settings(), plain_text=True)
    assert 'id="pokestatus" style="color: #' in html


def test_plain_status_still_shows_captured_once(gui_module):
    html = gui_module.create_status_html(
        "brn", _Settings(), is_pokemon_owned=True, plain_text=True
    )
    assert "— BURNED" in html
    assert html.count("[CAPTURED]") == 1


def test_styled_fainted_badge_keeps_its_white_on_black_colours(gui_module):
    html = gui_module.create_status_html("fainted", _Settings(), plain_text=False)
    assert "#FFFFFF" in html.upper()
