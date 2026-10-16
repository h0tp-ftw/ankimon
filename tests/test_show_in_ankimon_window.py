"""Tier-1 tests for ``drawing_utils.show_in_ankimon_window``.

The helper's return value is load-bearing: every caller
(``reviewer_ui.cycle_team_pokemon`` and friends) uses it to decide whether to
fall back to a floating Anki tooltip, so a wrong ``True`` does not degrade to a
duplicate message — it makes the message disappear entirely. These pin the
preconditions it must all see before it claims the message was shown.

Runs Qt-free: the helper touches no Qt API itself, only ``services``.
"""

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"

_RUNTIME_STUBS = (
    "aqt", "aqt.qt", "aqt.utils", "aqt.gui_hooks", "aqt.operations",
    "aqt.theme", "aqt.sound", "aqt.webview", "aqt.main",
    "anki", "anki.hooks", "anki.collection", "anki.utils",
    "PyQt6", "PyQt6.QtGui", "PyQt6.QtWidgets", "PyQt6.QtCore",
    "PyQt6.QtWebChannel", "PyQt6.QtWebEngineWidgets",
)
_ADDON_PKGS = ("Ankimon", "Ankimon.functions", "Ankimon.pyobj")

_MODULE_NAME = "Ankimon.functions.drawing_utils"
_MODULE_PATH = _SRC / "Ankimon" / "functions" / "drawing_utils.py"


@pytest.fixture
def _tier1_runtime(monkeypatch):
    """Fake Anki/Qt runtime + real addon packages, torn down after each test.

    This bootstrap used to run at import time, which made it collection-order
    pollution rather than test setup: the ``MagicMock`` it puts at
    ``sys.modules["PyQt6"]`` outlived this file and was still installed when a
    later module ran, so ``test_test_window_gui.py``'s
    ``pytest.importorskip("PyQt6")`` could bind the mock instead of the real
    binding and run its Qt characterization tests against a MagicMock. Doing it
    per-test through ``monkeypatch`` puts every entry back afterwards.

    ``setdefault`` semantics are deliberately preserved: in a Qt-capable run the
    real module wins and is left untouched — a stub is only installed where
    nothing is imported yet.
    """
    for name in _RUNTIME_STUBS:
        if name not in sys.modules:
            monkeypatch.setitem(sys.modules, name, MagicMock())

    for pkg in _ADDON_PKGS:
        existing = sys.modules.get(pkg)
        if existing is None or not hasattr(existing, "__path__"):
            mod = types.ModuleType(pkg)
            mod.__path__ = [str(_SRC / pkg.replace(".", "/"))]
            mod.__package__ = pkg
            monkeypatch.setitem(sys.modules, pkg, mod)

    monkeypatch.syspath_prepend(str(_SRC))


@pytest.fixture
def drawing_utils(monkeypatch, _tier1_runtime):
    """The REAL module, whatever the rest of the suite left in ``sys.modules``.

    Several other test files pre-stub ``Ankimon.functions.drawing_utils`` (and
    ``Ankimon.services``) with a MagicMock at import time, and collection order
    decides who wins: a plain module-level import here would bind their mock and
    every assertion below would pass against a Mock instead of the function
    under test. So load it from source per test, put the previous ``sys.modules``
    entry back afterwards, and give it its own ``services`` stub rather than
    reaching for whichever singleton happens to be live.

    ``_HAVE_QT`` is pinned True because it reports the environment, not the
    branch under test — the headless short-circuit gets its own test below.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, _MODULE_NAME, module)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_HAVE_QT", True, raising=False)
    monkeypatch.setattr(module, "services", _FakeServices(), raising=False)
    return module


class _FakeServices:
    test_window = None


class _FakeWindow:
    """Only what ``is_alive()`` and the helper itself touch."""

    def __init__(self, *, visible=True, current_view="battle"):
        self._visible = visible
        self.current_view = current_view
        self.shown = []

    def objectName(self):  # what utils.is_alive() probes
        return "AnkimonWindow"

    def isVisible(self):
        return self._visible

    def force_display_battle(self, message_text=None, paint_now=False):
        self.shown.append(message_text)


def test_message_goes_to_an_open_battle_window(drawing_utils):
    win = _FakeWindow()
    drawing_utils.services.test_window = win

    assert drawing_utils.show_in_ankimon_window("Switched to Pikachu!") is True
    assert win.shown == ["Switched to Pikachu!"]


def test_closed_window_is_not_treated_as_shown(drawing_utils):
    """The regression: ``TestWindow.closeEvent`` leaves ``current_view`` alone
    and ``QWidget.close()`` only hides the widget (no ``WA_DeleteOnClose``), so
    a closed window still answers True to both ``is_alive()`` and
    ``current_view == "battle"``. Reporting success there paints into a window
    nobody can see AND suppresses the caller's tooltip fallback.
    """
    win = _FakeWindow(visible=False)
    drawing_utils.services.test_window = win

    assert drawing_utils.show_in_ankimon_window("Switched to Pikachu!") is False
    assert win.shown == []


def test_other_views_and_missing_window_fall_back(drawing_utils):
    win = _FakeWindow(current_view="death")
    drawing_utils.services.test_window = win
    assert drawing_utils.show_in_ankimon_window("caught it!") is False
    assert win.shown == []

    drawing_utils.services.test_window = None
    assert drawing_utils.show_in_ankimon_window("caught it!") is False


def test_headless_always_falls_back(drawing_utils, monkeypatch):
    monkeypatch.setattr(drawing_utils, "_HAVE_QT", False)
    win = _FakeWindow()
    drawing_utils.services.test_window = win

    assert drawing_utils.show_in_ankimon_window("caught it!") is False
    assert win.shown == []
