"""Explicit, per-test Anki state. Test tooling only; never shipped in the add-on."""

from types import ModuleType, SimpleNamespace
from unittest.mock import Mock


def make_anki_host(profile_folder):
    """Give patched attributes real storage and keep mutable state test-local.

    MagicMock synthesizes child attributes outside __dict__. pytest 9.2's
    monkeypatch cleanup deletes those children instead of restoring them,
    poisoning a shared mock for the next test. Mock callables, not the host
    object graph, so ordinary setattr/undo semantics suffice.
    """
    host = ModuleType("aqt")
    host.__path__ = []
    host.mw = SimpleNamespace(
        pm=SimpleNamespace(
            name="Test profile",
            profile={},
            profileFolder=lambda: str(profile_folder),
            media_syncing_enabled=Mock(return_value=True),
            save=Mock(),
        ),
        taskman=SimpleNamespace(run_in_background=Mock(), run_on_main=Mock()),
        progress=SimpleNamespace(single_shot=Mock(), busy=Mock(return_value=False)),
        col=None,
        media_syncer=SimpleNamespace(start=Mock()),
        restoring_backup=False,
    )
    return host


def make_file_dialog():
    return SimpleNamespace(
        getOpenFileName=Mock(return_value=("", "")),
        getSaveFileName=Mock(return_value=("", "")),
    )
