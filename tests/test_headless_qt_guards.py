"""A QWidget must never be constructed without a QApplication.

Qt answers that by calling ``abort()`` — the process dies with SIGABRT and
"QWidget: Must construct a QApplication before a QWidget". That is not a Python
exception, so the ``try/except Exception`` wrappers the add-on uses as its
"headless is fine" guard cannot contain it. Those wrappers were written against
"PyQt6 is not installed"; they do not cover "PyQt6 is installed but nothing has
created an application", which is exactly the shape of a dev box running the
Tier-1 agent harness (AGENTS.md makes `python3 harness/check.py` the standard
pre-review check, and CI runs it on every PR).

`ShowInfoLogger.log_and_showinfo` is on the review path — `save_main_pokemon_
progress` calls it whenever a level-up move has to be replaced — so this took
out any headless play-through that got that far.
"""

import importlib
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import isolated_modules

_SRC = Path(__file__).parent.parent / "src"


@pytest.fixture
def logger_env(tmp_path):
    """A freshly imported ``InfoLogger`` sitting on a genuine ``PyQt6.QtWidgets``.

    Both halves matter, and both are about test isolation rather than the code
    under test: earlier suite modules replace ``PyQt6`` with a ``MagicMock``
    (so ``QApplication.instance()`` answers with a mock instead of ``None``,
    and the dialog class is a mock too) and may leave a stale ``InfoLogger``
    bound to it. Without this the tests silently pass or silently miss.

    ``isolated_modules`` puts ``sys.modules`` back exactly, including removing
    the submodules imported inside the block, so nothing here can make a later
    test order-dependent.
    """
    with isolated_modules("PyQt6", extra=("Ankimon.pyobj.InfoLogger",)):
        try:
            widgets = importlib.import_module("PyQt6.QtWidgets")
            module = importlib.import_module("Ankimon.pyobj.InfoLogger")
        except Exception as e:
            # Deliberately NOT pytest.skip — see the fixture docstring in
            # tests/test_web_bag_trade_evolutions.py. PyQt6 is a documented test
            # dependency, so an import failure here is drift to fix, not an
            # environment to tolerate.
            raise AssertionError(
                "InfoLogger / PyQt6.QtWidgets no longer importable for this "
                f"guard test — fix the fixture rather than skipping. Original "
                f"error: {e!r}"
            ) from e

        logger = module.ShowInfoLogger(
            name=f"test-{tmp_path.name}", log_filename=str(tmp_path / "app.log")
        )
        # `module.events` is the bus the logger actually emits on — importing
        # `Ankimon.events` separately can hand back a different (mocked) object.
        yield SimpleNamespace(
            module=module, logger=logger, widgets=widgets, events=module.events
        )


def test_log_and_showinfo_builds_no_dialog_without_an_application(
    monkeypatch, logger_env
):
    def _explode(*args, **kwargs):
        raise AssertionError("constructed a QMessageBox with no QApplication")

    monkeypatch.setattr(logger_env.widgets, "QMessageBox", _explode)
    monkeypatch.setattr(
        logger_env.widgets.QApplication, "instance", staticmethod(lambda: None)
    )

    # Must not raise, and must still record the message.
    logger_env.logger.log_and_showinfo("info", "hello from a headless run")


def test_log_and_showinfo_still_records_the_event_when_it_skips_the_dialog(
    monkeypatch, logger_env
):
    monkeypatch.setattr(
        logger_env.widgets.QApplication, "instance", staticmethod(lambda: None)
    )

    seen = []
    logger_env.events.enable(sink=seen.append)
    try:
        logger_env.logger.log_and_showinfo("warning", "still observable")
    finally:
        logger_env.events.disable()

    assert any(
        ev.get("type") == "log" and ev.get("message") == "still observable"
        for ev in seen
    ), "the structured event is the headless substitute for the popup"


def test_log_and_showinfo_shows_the_dialog_when_an_application_exists(
    monkeypatch, logger_env
):
    built = []
    widgets = logger_env.widgets

    class _MessageBox:
        Icon = widgets.QMessageBox.Icon

        def setWindowTitle(self, *a):
            pass

        def setText(self, *a):
            pass

        def setIcon(self, *a):
            pass

        def exec(self):
            built.append(1)

    # A stand-in for QApplication that satisfies the guard's isinstance check:
    # patch the class the module looks up AND make instance() return one of it.
    class _App:
        @staticmethod
        def instance():
            return _app

    _app = _App()

    monkeypatch.setattr(widgets, "QMessageBox", _MessageBox)
    monkeypatch.setattr(widgets, "QApplication", _App)

    logger_env.logger.log_and_showinfo("info", "gui mode")
    assert built == [1], "the guard swallowed a legitimate GUI-mode popup"


# --------------------------------------------------------------------------- #
# error_handler.show_warning_with_traceback is the funnel EVERY `except
# Exception:` in the add-on reaches. It already guards two ways a QWidget can be
# unbuildable (PyQt6 missing -> _HAVE_QT; wrong thread -> is_main_thread) but
# not the third: PyQt6 importable with no QApplication. Headless that inverted
# the module's whole purpose — a recoverable error, the thing it exists to make
# observable, became a force-close instead.
# --------------------------------------------------------------------------- #
@pytest.fixture
def error_handler():
    """`error_handler` re-imported on a genuine PyQt6 (see `logger_env`)."""
    with isolated_modules("PyQt6", extra=("Ankimon.pyobj.error_handler",)):
        try:
            module = importlib.import_module("Ankimon.pyobj.error_handler")
        except Exception as e:
            raise AssertionError(f"error_handler no longer importable: {e!r}") from e
        yield module


def test_error_dialog_is_not_built_without_a_qapplication(monkeypatch, error_handler):
    assert error_handler._HAVE_QT, "fixture must supply a real PyQt6"

    def _explode(*args, **kwargs):
        raise AssertionError("constructed a QDialog with no QApplication")

    monkeypatch.setattr(error_handler, "QDialog", _explode)
    monkeypatch.setattr(error_handler, "load_error_images", _explode)
    monkeypatch.setattr(
        error_handler.QApplication, "instance", staticmethod(lambda: None)
    )

    # Must return quietly — the log line and the `error` event are the record.
    error_handler.show_warning_with_traceback(
        exception=ValueError("boom"), message="headless error"
    )


def test_error_event_is_still_emitted_when_the_dialog_is_skipped(
    monkeypatch, error_handler
):
    from Ankimon.events import events

    monkeypatch.setattr(
        error_handler.QApplication, "instance", staticmethod(lambda: None)
    )

    seen = []
    events.enable(sink=seen.append)
    try:
        error_handler.show_warning_with_traceback(
            exception=ValueError("boom"), message="still observable"
        )
    finally:
        events.disable()

    assert any(
        ev.get("type") == "error" and ev.get("message") == "still observable"
        for ev in seen
    ), "the structured error event is the headless substitute for the dialog"


def test_error_dialog_is_still_built_when_an_application_exists(
    monkeypatch, error_handler
):
    reached = []

    class _App:
        @staticmethod
        def instance():
            return _app

    _app = _App()

    def _record(*args, **kwargs):
        reached.append(1)
        raise RuntimeError("stop here — past the guard is all we need to prove")

    monkeypatch.setattr(error_handler, "QApplication", _App)
    monkeypatch.setattr(error_handler, "load_error_images", _record)

    with pytest.raises(RuntimeError):
        error_handler.show_warning_with_traceback(
            exception=ValueError("boom"), message="gui mode"
        )
    assert reached == [1], "the guard swallowed a legitimate GUI-mode error dialog"


# --------------------------------------------------------------------------- #
# The other half of the guard: QApplication.instance() is inherited from
# QCoreApplication and returns whatever application exists, so a console-only
# QCoreApplication comes back NON-None. A guard written as `instance() is None`
# therefore passes and the QWidget built after it aborts anyway.
#
# Run in subprocesses: the failure mode is SIGABRT, which cannot be caught
# in-process, and only a fresh interpreter can create a QCoreApplication (Qt
# permits one application object per process, and the suite may already have a
# QApplication from pytest-qt). A non-zero exit IS the regression.
# --------------------------------------------------------------------------- #
_QCORE_STUBS = """
import sys, types
sys.path.insert(0, {src!r})
from unittest.mock import MagicMock

# Package stubs so relative imports resolve without running Ankimon/__init__.py.
for pkg in ("Ankimon", "Ankimon.pyobj", "Ankimon.functions"):
    m = types.ModuleType(pkg)
    m.__path__ = [{src!r} + "/Ankimon" + pkg[len("Ankimon"):].replace(".", "/")]
    m.__package__ = pkg
    sys.modules[pkg] = m

# `aqt` stub forwarding to the real PyQt6, so this matches CI (no Anki) and does
# not drag in QtWebEngineWidgets, which Qt requires to be imported before any
# application object exists.
import PyQt6.QtCore, PyQt6.QtGui, PyQt6.QtWidgets
aqt = types.ModuleType("aqt"); aqt.__path__ = []; aqt.mw = None
aqt_qt = types.ModuleType("aqt.qt")
for _src in (PyQt6.QtCore, PyQt6.QtGui, PyQt6.QtWidgets):
    for _n in dir(_src):
        if not _n.startswith("_"):
            setattr(aqt_qt, _n, getattr(_src, _n))
aqt_qt.qconnect = lambda sig, slot: sig.connect(slot)
aqt_utils = types.ModuleType("aqt.utils")
aqt_utils.showWarning = aqt_utils.showInfo = aqt_utils.tooltip = lambda *a, **k: None
sys.modules["aqt"] = aqt
sys.modules["aqt.qt"] = aqt_qt
sys.modules["aqt.utils"] = aqt_utils
"""

_QCORE_APP = """
# Import the module under test BEFORE the application exists — that is the real
# load order, and Qt forbids importing some modules after an app is created.
{imports}

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QApplication
_app = QCoreApplication([])
assert QApplication.instance() is not None, (
    "premise: a QCoreApplication reads as non-None"
)
assert not isinstance(QApplication.instance(), QApplication), (
    "premise: a QCoreApplication is not widget-capable"
)
"""


def _run_under_qcoreapplication(imports, body):
    """Run ``body`` in a fresh interpreter that owns only a QCoreApplication.

    A non-zero exit IS the regression: the failure mode is Qt calling abort(),
    which no in-process assertion could observe.
    """
    code = (
        _QCORE_STUBS.format(src=str(_SRC)) + _QCORE_APP.format(imports=imports) + body
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=300
    )
    assert proc.returncode == 0, (
        f"guard failed under a QCoreApplication (exit {proc.returncode}; "
        "-6 is SIGABRT / 'Must construct a QApplication before a QWidget')\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "OK" in proc.stdout, f"body did not complete: {proc.stdout}{proc.stderr}"
    return proc


def test_log_and_showinfo_survives_a_qcoreapplication_only_process(tmp_path):
    _run_under_qcoreapplication(
        "from Ankimon.pyobj.InfoLogger import ShowInfoLogger",
        "log = ShowInfoLogger(name='qcore', "
        f"log_filename={str(tmp_path / 'a.log')!r})\n"
        "log.log_and_showinfo('info', 'no widgets available here')\n"
        "print('OK')\n",
    )


def test_error_dialog_survives_a_qcoreapplication_only_process():
    _run_under_qcoreapplication(
        "from Ankimon.pyobj.error_handler import show_warning_with_traceback",
        "show_warning_with_traceback(exception=ValueError('boom'), message='qcore')\n"
        "print('OK')\n",
    )


def test_sprite_download_survives_a_qcoreapplication_only_process():
    _run_under_qcoreapplication(
        "from Ankimon.pyobj.download_sprites import show_agreement_and_download_dialog",
        "show_agreement_and_download_dialog()\nprint('OK')\n",
    )


# --------------------------------------------------------------------------- #
# isolated_modules itself: sys.modules is not the whole of a module's identity.
#
# These use a throwaway package written to tmp_path rather than a real Ankimon
# submodule. Not squeamishness: the suite's other modules register MagicMocks
# straight into ``sys.modules["Ankimon.pyobj.*"]``, so ``import_module`` on a
# real name can hand back a mock without ever binding the parent attribute, and
# the assertions below would then be measuring test-order rather than the helper.
# A package on disk gives a genuine import with a starting state this test owns.
# --------------------------------------------------------------------------- #
@pytest.fixture
def probe_package(tmp_path, monkeypatch):
    """A real, importable ``iso_probe_pkg.child`` that leaves nothing behind."""
    pkg_dir = tmp_path / "iso_probe_pkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "child.py").write_text("VALUE = 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    try:
        yield SimpleNamespace(parent="iso_probe_pkg", child="iso_probe_pkg.child")
    finally:
        for name in [n for n in list(sys.modules) if n.startswith("iso_probe_pkg")]:
            del sys.modules[name]


def test_isolated_modules_restores_the_parent_package_attribute(probe_package):
    """Both identities of a re-imported submodule must come back together.

    Importing ``a.b`` binds ``b`` onto package ``a`` as well as into
    ``sys.modules``. Restoring only ``sys.modules`` leaves the attribute pointing
    at the module imported inside the block while ``sys.modules[...]`` points at
    the original — and which one a later test sees depends on whether it writes
    ``from a import b`` or ``import a.b``. That is exactly the order-dependent
    breakage this helper exists to prevent.
    """
    parent = importlib.import_module(probe_package.parent)
    before = importlib.import_module(probe_package.child)
    assert parent.child is before  # what a real import binds

    with isolated_modules(extra=(probe_package.child,)):
        reimported = importlib.import_module(probe_package.child)
        assert reimported is not before  # the block really did re-execute it
        assert parent.child is reimported  # ...and rebound the parent attribute

    assert sys.modules[probe_package.child] is before
    assert parent.child is before


def test_isolated_modules_removes_an_attribute_it_created(probe_package):
    """A submodule absent beforehand must leave no attribute behind either.

    The same leak in reverse: nothing to restore, so the binding the block's
    import created has to be deleted rather than left on the parent.
    """
    parent = importlib.import_module(probe_package.parent)
    assert not hasattr(parent, "child")

    with isolated_modules(extra=(probe_package.child,)):
        importlib.import_module(probe_package.child)
        assert hasattr(parent, "child")

    assert probe_package.child not in sys.modules
    assert not hasattr(parent, "child")


def test_isolated_modules_restores_on_an_exception(probe_package):
    parent = importlib.import_module(probe_package.parent)
    before = importlib.import_module(probe_package.child)

    with pytest.raises(RuntimeError):
        with isolated_modules(extra=(probe_package.child,)):
            importlib.import_module(probe_package.child)
            raise RuntimeError("boom")

    assert sys.modules[probe_package.child] is before
    assert parent.child is before


def test_isolated_modules_cleans_up_when_the_parent_is_imported_inside(probe_package):
    """The parent itself arriving mid-block must not leave a dangling name.

    Importing the child imports the parent too; the parent is untracked, so it
    survives the block. Without an entry for it, it survives holding a ``child``
    attribute bound to a module that is no longer in ``sys.modules`` — the same
    split identity, just created rather than overwritten.
    """
    assert probe_package.parent not in sys.modules

    with isolated_modules(extra=(probe_package.child,)):
        importlib.import_module(probe_package.child)

    assert probe_package.child not in sys.modules
    parent = sys.modules.get(probe_package.parent)
    if parent is not None:
        assert not hasattr(parent, "child")
