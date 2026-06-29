"""
Tests for migration.migrate_starter_individual_id — migrated off mw onto the
services registry. The logger comes from `services` (no Anki). The only Anki
touch left is `showWarning`, lazy-imported inside the fix path; the skip-path
tests below never trigger it, and the fix-path test installs a tiny 2-module
aqt.utils stub (vs. the ~20-module sys.modules surgery the legacy tests need).
"""

import json
import sys
import types

import pytest

from Ankimon.services import services
from Ankimon.functions import migration as mig


class FakeLogger:
    def __init__(self):
        self.logs = []

    def log(self, level, message):
        self.logs.append((level, message))


@pytest.fixture(autouse=True)
def fake_logger():
    services.reset()
    logger = FakeLogger()
    services.logger = logger
    yield logger
    services.reset()


@pytest.fixture
def recording_showwarning():
    """Stub aqt.utils.showWarning for the lazy import on the fix path."""
    calls = []
    aqt = types.ModuleType("aqt")
    aqt_utils = types.ModuleType("aqt.utils")
    aqt_utils.showWarning = lambda *a, **k: calls.append(a[0] if a else "")
    aqt.utils = aqt_utils
    saved = {k: sys.modules.get(k) for k in ("aqt", "aqt.utils")}
    sys.modules["aqt"] = aqt
    sys.modules["aqt.utils"] = aqt_utils
    try:
        yield calls
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def test_get_starter_evolution_ids_is_pure():
    assert mig.get_starter_evolution_ids(1) == [1, 2, 3]


def test_skips_when_data_files_missing(fake_logger, monkeypatch, tmp_path):
    monkeypatch.setattr(mig, "mainpokemon_path", tmp_path / "absent_main.json")
    monkeypatch.setattr(mig, "mypokemon_path", tmp_path / "absent_my.json")

    mig.migrate_starter_individual_id()

    assert any("data files missing" in m for _, m in fake_logger.logs)


def test_skips_when_already_synchronized(fake_logger, monkeypatch, tmp_path):
    main = tmp_path / "main.json"
    my = tmp_path / "my.json"
    main.write_text(json.dumps([{"id": 1, "individual_id": "abc"}]))
    my.write_text(json.dumps([{"id": 1, "individual_id": "abc"}]))
    monkeypatch.setattr(mig, "mainpokemon_path", main)
    monkeypatch.setattr(mig, "mypokemon_path", my)

    mig.migrate_starter_individual_id()

    assert any("already synchronized" in m for _, m in fake_logger.logs)


def test_fixes_starter_id_and_warns_user(fake_logger, recording_showwarning, monkeypatch, tmp_path):
    # Same starter (bulbasaur, id 1) in both files but with mismatched individual_id.
    shared = {"id": 1, "iv": {"hp": 10}, "gender": "M", "ability": "Overgrow", "shiny": False}
    main = tmp_path / "main.json"
    my = tmp_path / "my.json"
    main.write_text(json.dumps([{**shared, "individual_id": "NEW-ID"}]))
    my.write_text(json.dumps([{**shared, "individual_id": "OLD-DIFFERENT"}]))
    monkeypatch.setattr(mig, "mainpokemon_path", main)
    monkeypatch.setattr(mig, "mypokemon_path", my)

    mig.migrate_starter_individual_id()

    # the my-pokemon's id was synced, the file rewritten, and the user warned once
    assert json.loads(my.read_text())[0]["individual_id"] == "NEW-ID"
    assert len(recording_showwarning) == 1
    assert any("successful" in m for _, m in fake_logger.logs)
