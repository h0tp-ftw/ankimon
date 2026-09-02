"""
harness/fake_aqt.py — a fake Anki host so the REAL Ankimon boots headless.

This is the Tier-2 shim. Unlike the Tier-1 fakes (which replace Ankimon's own
windows), this fakes only **Anki** — `mw`, `gui_hooks`, `aqt.qt`, `aqt.utils`,
etc. — and uses **real PyQt6** underneath. So `import Ankimon` runs the real
`__init__.py` -> `singletons.py` -> builds the real Qt windows (real widgets,
real memory, real PC box). Nothing is drawn (QT_QPA_PLATFORM=offscreen), but
everything else is the genuine add-on, which is what makes real-Qt glitches /
memory behaviour reproducible.

Call `install()` AFTER a QApplication exists and BEFORE `import Ankimon`.

What's faked and why:
- `aqt.gui_hooks` — a real hook registry (append + callable), so the driver can
  fire `reviewer_did_answer_card` etc.
- `aqt.mw` — a real QMainWindow (so it can parent widgets / own a real QMenu)
  with the handful of attributes the add-on touches; taskman/QueryOp run
  synchronously (no background threads racing the offscreen loop).
- `aqt.qt` — a faithful re-export of real PyQt6 (+ qconnect, sip, WebEngine stubs).
- `aqt.utils` — showInfo/tooltip/qconnect/tr/askUser + WebEngine stubs.
- `anki.hooks` (addHook/wrap) + `anki.buildinfo.version`.
- WebEngine classes are lightweight stubs so the 3 info windows import/construct
  without pulling in Chromium (PyQt6-WebEngine). Everything else is real Qt.
"""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock


# --- a real hook registry --------------------------------------------------

class _Hook(list):
    """A gui_hooks-style hook: append/remove callbacks, call to fire them."""

    def append(self, cb):
        super().append(cb)

    def remove(self, cb):
        try:
            super().remove(cb)
        except ValueError:
            pass

    def __call__(self, *args, **kwargs):
        for cb in list(self):
            cb(*args, **kwargs)


class _HookRegistry(ModuleType):
    """Lazily hands out a shared _Hook per attribute name (gui_hooks)."""

    def __init__(self, name):
        super().__init__(name)
        self._hooks = {}

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        hook = self._hooks.get(name)
        if hook is None:
            hook = self._hooks[name] = _Hook()
            object.__setattr__(self, name, hook)
        return hook


# --- WebEngine stubs (avoid pulling in Chromium) ---------------------------

def _make_webengine_stubs(QWidget):
    class _StubWebEnginePage:
        class NavigationType:
            NavigationTypeLinkClicked = 0

        def __init__(self, *a, **k):
            pass

        def acceptNavigationRequest(self, *a, **k):
            return True

        def __getattr__(self, _):
            return lambda *a, **k: None

    class _StubWebEngineProfile:
        def __init__(self, *a, **k):
            pass

        def __getattr__(self, _):
            return lambda *a, **k: None

    class _StubWebEngineSettings:
        class WebAttribute:
            JavascriptEnabled = 0
            LocalContentCanAccessRemoteUrls = 1
            LocalContentCanAccessFileUrls = 2
            AutoLoadImages = 3

        def setAttribute(self, *a, **k):
            pass

    class _StubWebEngineView(QWidget):
        def __init__(self, *a, **k):
            super().__init__()

        def settings(self):
            return _StubWebEngineSettings()

        def page(self):
            return _StubWebEnginePage()

        def setPage(self, *a, **k):
            pass

        def setHtml(self, *a, **k):
            pass

        def setUrl(self, *a, **k):
            pass

        def load(self, *a, **k):
            pass

        def __getattr__(self, name):
            # QWidget.__getattr__ isn't defined; this only fires for genuinely
            # missing attrs (web-engine-only methods we don't model).
            return lambda *a, **k: None

    return (
        _StubWebEngineView,
        _StubWebEnginePage,
        _StubWebEngineSettings,
        _StubWebEngineProfile,
    )


# --- the synchronous QueryOp ----------------------------------------------

def _make_query_op():
    class QueryOp:
        def __init__(self, *, parent=None, op=None, success=None):
            self._op = op
            self._success = success

        def without_collection(self):
            return self

        def with_progress(self, *a, **k):
            return self

        def failure(self, *a, **k):
            return self

        def run_in_background(self):
            # Synchronous: no thread, so it can't race the offscreen event loop.
            result = self._op(None) if self._op else None
            if self._success:
                self._success(result)
            return result

    return QueryOp


# --- anki shim -------------------------------------------------------------

def _install_anki():
    anki = ModuleType("anki")
    anki.__path__ = []

    hooks = ModuleType("anki.hooks")
    _legacy = {}

    def addHook(name, fn):
        _legacy.setdefault(name, []).append(fn)

    def runHook(name, *a, **k):
        for fn in _legacy.get(name, []):
            fn(*a, **k)

    def wrap(old, new, pos="after"):
        def repl(*args, **kwargs):
            if pos == "before":
                new(*args, **kwargs)
                return old(*args, **kwargs)
            if pos == "around":
                return new(*args, _old=old, **kwargs)
            # "after"
            result = old(*args, **kwargs)
            new(*args, **kwargs)
            return result
        return repl

    hooks.addHook = addHook
    hooks.runHook = runHook
    hooks.wrap = wrap
    hooks._hooks = _legacy

    buildinfo = ModuleType("anki.buildinfo")
    buildinfo.version = "0.0.0-headless"

    anki.hooks = hooks
    anki.buildinfo = buildinfo

    sys.modules["anki"] = anki
    sys.modules["anki.hooks"] = hooks
    sys.modules["anki.buildinfo"] = buildinfo
    # Unknown anki submodules: harmless mocks.
    for sub in ("utils", "lang", "collection", "cards", "notes", "sound", "scheduler"):
        m = MagicMock()
        sys.modules[f"anki.{sub}"] = m
        setattr(anki, sub, m)


# --- the fake main window --------------------------------------------------

def _make_fake_mw(app, user_path):
    from PyQt6.QtWidgets import QMainWindow

    class _Web:
        """Stand-in for mw.reviewer.web — records the last eval'd JS."""
        def __init__(self):
            self.last_js = None

        def eval(self, js):
            self.last_js = js

        def evalWithCallback(self, js, cb=None):
            self.last_js = js
            if cb:
                cb(None)

        def __getattr__(self, _):
            return lambda *a, **k: None

    class _Taskman:
        def run_in_background(self, task, on_done=None):
            # Synchronous so profile-open / monthly work is deterministic.
            try:
                result = task()
            except Exception as e:  # mirror Anki delivering the exception to on_done
                result = e
            if on_done is not None:
                fut = SimpleNamespace(result=lambda: result if not isinstance(result, Exception) else (_ for _ in ()).throw(result))
                try:
                    on_done(fut)
                except Exception:
                    pass
            return result

        def __getattr__(self, _):
            return lambda *a, **k: None

    class _AddonManager:
        def setWebExports(self, *a, **k):
            pass

        def addonFromModule(self, *a, **k):
            return "ankimon"

        def addonsFolder(self, *a, **k):
            return str(user_path)

        def getConfig(self, *a, **k):
            return {}

        def writeConfig(self, *a, **k):
            pass

        def __getattr__(self, _):
            return lambda *a, **k: None

    class _Sched:
        def answerButtons(self, card):
            return 4  # Again/Hard/Good/Easy

    class _Col:
        """Minimal stand-in for the Anki collection so the real card hooks run
        (answerCard_after reads col.sched.answerButtons; get_total_reviews reads
        col.studied_today). Faithful enough for Ankimon's own code paths."""
        def __init__(self):
            self.sched = _Sched()

        def studied_today(self):
            return "Studied 0 cards today"

        def __getattr__(self, _):
            return lambda *a, **k: None

    class FakeMW(QMainWindow):
        def __init__(self):
            super().__init__()
            self.app = app
            self.col = _Col()
            self.reviewer = SimpleNamespace(web=_Web(), card=None, mw=self)
            self.taskman = _Taskman()
            self.addonManager = _AddonManager()
            self.pm = SimpleNamespace(name="headless", profileFolder=lambda: str(user_path),
                                      night_mode=lambda: False)
            self.progress = SimpleNamespace(
                timer=lambda *a, **k: SimpleNamespace(stop=lambda: None, start=lambda *a, **k: None),
                start=lambda *a, **k: None, finish=lambda: None, update=lambda *a, **k: None,
                single_shot=lambda *a, **k: None,
            )
            self.form = MagicMock()

        def _increase_background_ops(self):
            pass

        def _decrease_background_ops(self):
            pass

    return FakeMW()


# --- top-level install -----------------------------------------------------

def install(app, user_path, real_webengine=False):
    """Install the fake aqt/anki into sys.modules. Call before `import Ankimon`.

    real_webengine=True wires the REAL QWebEngineView (so the Pokedex / HUD render
    for real) instead of the lightweight stub — requires WebEngine to be importable
    and already imported before the QApplication (real_env handles that). Falls back
    to the stub if the real one isn't available.
    """
    import PyQt6.QtCore
    import PyQt6.QtGui
    import PyQt6.QtWidgets
    import PyQt6.sip
    from PyQt6.QtWidgets import QWidget

    _install_anki()

    def qconnect(signal, func):
        signal.connect(func)

    web_view = web_page = web_settings = web_profile = None
    if real_webengine:
        try:
            from PyQt6.QtWebEngineWidgets import QWebEngineView as web_view
            from PyQt6.QtWebEngineCore import (
                QWebEnginePage as web_page,
                QWebEngineProfile as web_profile,
                QWebEngineSettings as web_settings,
            )
        except Exception:
            web_view = web_page = web_settings = web_profile = None
    if web_view is None:
        web_view, web_page, web_settings, web_profile = _make_webengine_stubs(QWidget)

    # aqt.qt — faithful re-export of real PyQt6 (+ qconnect, sip, webengine stubs).
    qt = ModuleType("aqt.qt")
    for mod in (PyQt6.QtCore, PyQt6.QtGui, PyQt6.QtWidgets):
        for nm in dir(mod):
            if not nm.startswith("_"):
                setattr(qt, nm, getattr(mod, nm))
    qt.sip = PyQt6.sip
    qt.qconnect = qconnect
    qt.QWebEngineView = web_view
    qt.QWebEnginePage = web_page
    qt.QWebEngineSettings = web_settings
    qt.QWebEngineProfile = web_profile

    # aqt.utils
    utils = ModuleType("aqt.utils")
    utils.showInfo = lambda *a, **k: None
    utils.showWarning = lambda *a, **k: None
    utils.showText = lambda *a, **k: None
    utils.tooltip = lambda *a, **k: None
    utils.askUser = lambda *a, **k: True
    utils.qconnect = qconnect
    utils.downArrow = lambda *a, **k: ""
    utils.openLink = lambda *a, **k: None
    utils.getText = lambda *a, **k: ("", 0)
    utils.QWebEngineView = web_view
    utils.QWebEnginePage = web_page
    utils.QWebEngineSettings = web_settings
    utils.QWebEngineProfile = web_profile

    class _Tr:
        def __getattr__(self, _):
            return lambda *a, **k: ""
    utils.tr = _Tr()

    # aqt.gui_hooks — real registry.
    gui_hooks = _HookRegistry("aqt.gui_hooks")

    # aqt.webview
    webview = ModuleType("aqt.webview")

    class WebContent:
        def __init__(self, *a, **k):
            self.js = []
            self.css = []
            self.head = ""
            self.body = ""
    webview.WebContent = WebContent

    # aqt.operations
    operations = ModuleType("aqt.operations")
    operations.QueryOp = _make_query_op()

    # aqt.reviewer
    reviewer = ModuleType("aqt.reviewer")

    class Reviewer:
        def _shortcutKeys(self):
            return []

        def _linkHandler(self, url):
            pass

        def _bottomHTML(self):
            return ""
    reviewer.Reviewer = Reviewer

    # aqt.theme
    theme = ModuleType("aqt.theme")
    theme.theme_manager = SimpleNamespace(night_mode=False)

    # aqt package
    aqt = ModuleType("aqt")
    aqt.__path__ = []
    fake_mw = _make_fake_mw(app, user_path)
    aqt.mw = fake_mw
    aqt.gui_hooks = gui_hooks
    aqt.utils = utils
    aqt.qt = qt
    aqt.webview = webview
    aqt.operations = operations
    aqt.reviewer = reviewer
    aqt.theme = theme
    aqt.qconnect = qconnect
    aqt.sound = MagicMock()
    aqt.main = SimpleNamespace(AnkiQt=QMainWindowProxy(fake_mw))
    # aqt re-exports some Qt classes at top level (the add-on relies on this).
    for nm in ("QDialog", "QVBoxLayout", "QHBoxLayout", "QLabel", "QWidget",
               "QPushButton", "QMainWindow"):
        if hasattr(qt, nm):
            setattr(aqt, nm, getattr(qt, nm))
    aqt.QWebEngineView = web_view
    aqt.QWebEnginePage = web_page
    aqt.QWebEngineSettings = web_settings
    aqt.QWebEngineProfile = web_profile

    sys.modules["aqt"] = aqt
    sys.modules["aqt.qt"] = qt
    sys.modules["aqt.utils"] = utils
    sys.modules["aqt.gui_hooks"] = gui_hooks
    sys.modules["aqt.webview"] = webview
    sys.modules["aqt.operations"] = operations
    sys.modules["aqt.reviewer"] = reviewer
    sys.modules["aqt.theme"] = theme
    sys.modules["aqt.sound"] = aqt.sound
    sys.modules["aqt.main"] = aqt.main

    return aqt


class QMainWindowProxy:
    """Placeholder so `aqt.main.AnkiQt` exists if referenced (rarely)."""
    def __init__(self, mw):
        self._mw = mw
