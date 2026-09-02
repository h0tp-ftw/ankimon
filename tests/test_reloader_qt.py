"""Real-Qt tests for the hot-reload's deferred-delete flush.

``_flush_deferred_widget_deletes`` is the whole of PR #686, and what it claims
only holds if ``QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)``
really destroys objects that ``deleteLater()`` merely *scheduled* — including
at the same event-loop level the reload runs at, which is the case a plain
``processEvents()`` deliberately skips. A fake ``QApplication`` cannot show
that, so these run against real Qt (the Tier-2 / integrity-test env) and skip
cleanly in the aqt-free Tier-1 env.

Kept out of ``test_reloader.py`` on purpose: that file installs fake ``PyQt6``
modules in ``sys.modules``, which is exactly what would invalidate these. It
restores them via ``monkeypatch``, so real Qt is back by the time this module
runs — but that only holds because ``test_reloader_qt`` sorts *after*
``test_reloader``. If collection order ever changes (a rename, xdist, a
shuffling plugin), ``_env_guard`` below turns these into skips rather than
failures, so re-run this file standalone before trusting a green suite.

Run standalone with::

    pytest tests/test_reloader_qt.py
"""

import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")  # Qt env only; skipped in the aqt-free Tier-1 env.


_SRC = Path(__file__).parent.parent / "src"


@pytest.fixture(autouse=True)
def _env_guard():
    """Skip gracefully when another test file has mocked PyQt6 in this run.

    Un-mocking a compiled Qt extension mid-process is not reliable, so skip
    rather than fail. Matches ``test_move_picker.py``.
    """
    from PyQt6.QtWidgets import QWidget

    if not isinstance(QWidget, type):  # PyQt6 was mocked by another test
        pytest.skip(
            "real PyQt6 not active (mocked by another test); "
            "run tests/test_reloader_qt.py standalone"
        )
    yield


@pytest.fixture
def reloader(monkeypatch):
    """Import the real reloader.py against real Qt and a stubbed ``aqt``."""

    def _stub(name, **attrs):
        mod = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(mod, key, value)
        return mod

    aqt = _stub("aqt", gui_hooks=SimpleNamespace(), mw=SimpleNamespace())
    aqt.__path__ = []
    monkeypatch.setitem(sys.modules, "aqt", aqt)
    monkeypatch.setitem(
        sys.modules, "aqt.utils", _stub("aqt.utils", tooltip=lambda message: None)
    )
    monkeypatch.setitem(
        sys.modules,
        "Ankimon.services",
        _stub("Ankimon.services", services=SimpleNamespace()),
    )

    spec = importlib.util.spec_from_file_location(
        "Ankimon.reloader", _SRC / "Ankimon" / "reloader.py"
    )
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "Ankimon.reloader", mod)
    spec.loader.exec_module(mod)
    return mod


def test_plain_process_events_leaves_deferred_deletes_alive(qapp, reloader):
    """The control. Without this, the flush would be redundant with the
    ``processEvents()`` calls teardown already makes."""
    from PyQt6 import sip
    from PyQt6.QtWidgets import QWidget

    widget = QWidget()
    widget.deleteLater()

    qapp.processEvents()

    assert not sip.isdeleted(widget)
    widget.deleteLater()  # leave nothing for the next test to collect
    reloader._flush_deferred_widget_deletes()


def test_flush_destroys_deferred_deleted_widgets(qapp, reloader):
    """The claim PR #686 rests on: after the flush the C++ objects are gone,
    so the module purge can no longer strand a live QWebEngine page."""
    from PyQt6 import sip
    from PyQt6.QtWidgets import QWidget

    parent = QWidget()
    child = QWidget(parent)
    destroyed = []
    parent.destroyed.connect(lambda *_: destroyed.append("parent"))

    parent.close()
    parent.deleteLater()
    assert not sip.isdeleted(parent)  # deleteLater() alone destroys nothing

    reloader._flush_deferred_widget_deletes()

    assert destroyed == ["parent"]
    assert sip.isdeleted(parent)
    # Children go with the parent — the QWebEngine page/profile ownership case.
    assert sip.isdeleted(child)


def test_flush_collects_deletes_scheduled_during_destruction(qapp, reloader):
    """Why the flush loops instead of sending posted events once.

    Destroying one object can schedule the next through *queued* work — which
    is the shape WebEngine teardown takes. A queued connection lands as a
    metacall event, so the first ``sendPostedEvents(..., DeferredDelete)`` pass
    cannot see it; only the interleaved ``processEvents()`` dispatches it, and
    the ``deleteLater()`` it then posts needs a second DeferredDelete pass.
    Drop the flush to a single iteration and this test fails.
    """
    from PyQt6 import sip
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QWidget

    first = QWidget()
    second = QWidget()
    first.destroyed.connect(second.deleteLater, Qt.ConnectionType.QueuedConnection)

    first.deleteLater()
    reloader._flush_deferred_widget_deletes()

    assert sip.isdeleted(first)
    assert sip.isdeleted(second)


def test_flush_is_a_noop_without_a_qapplication(reloader, monkeypatch):
    """Headless callers (and Anki mid-shutdown) must not trip over the flush."""
    monkeypatch.setattr(
        reloader.QApplication, "instance", staticmethod(lambda: None)
    )

    reloader._flush_deferred_widget_deletes()  # must not raise
