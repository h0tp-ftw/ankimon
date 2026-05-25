"""
Regression tests for trainer-data (cash) recovery after a wipe.

Pins the guardrails of pyobj/profile_recovery.recover_wiped_trainer_data so a
future change can't silently re-introduce the "everyone's cash got wiped"
behaviour or, worse, start clobbering healthy profiles.

The module is loaded directly from its file: it imports only stdlib at module
level (the aqt import is lazy, inside warn_if_synced_folder), so no Anki stubs
are needed for the recovery path tested here.
"""

import base64
import importlib.util
import json
from pathlib import Path

import pytest

_SRC = Path(__file__).parent.parent / "src"
_KEY = "H0tP-!s-N0t-4-C@tG!rL_v2".encode("utf-8")


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "ankimon_profile_recovery", _SRC / "Ankimon" / "pyobj" / "profile_recovery.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pr = _load_module()


def _obfuscate(d: dict) -> str:
    js = json.dumps(d).encode()
    return base64.b64encode(bytes(b ^ _KEY[i % len(_KEY)] for i, b in enumerate(js))).decode()


class FakeDB:
    DB_FILENAME = "ankimon.db"

    def __init__(self, config):
        self.meta = {}
        self.config = dict(config)

    def get_metadata(self, key, default=None):
        return self.meta.get(key, default)

    def set_metadata(self, key, value):
        self.meta[key] = str(value)
        return True

    def get_config_value(self, key, default=None):
        return self.config.get(key, default)

    def set_config_value(self, key, value):
        self.config[key] = value


class FakeSettings:
    def __init__(self, config):
        self.config = dict(config)

    def compute_gui_config(self):
        pass


WIPED = {"trainer.cash": 0, "trainer.level": 0, "trainer.xp": 0}
SNAPSHOT = {
    "trainer.name": "PandaPrincess333", "trainer.sprite": "red", "trainer.id": 7,
    "trainer.cash": 45900, "trainer.level": 19, "trainer.xp": 120,
}


def _profile(tmp_path, snapshots):
    """Create a user_files dir with a dummy db and the given .obf snapshots."""
    tmp_path = Path(tmp_path)
    (tmp_path / "json").mkdir(parents=True, exist_ok=True)
    (tmp_path / "ankimon.db").write_bytes(b"dummy-sqlite")
    for name, data in snapshots:
        (tmp_path / name).write_text("WARNING: do not edit\n---" + _obfuscate(data))
    return tmp_path


def test_restores_wiped_profile_from_newest_snapshot(tmp_path):
    uf = _profile(tmp_path, [
        ("config.sync-conflict-20260512-205742-ZZUGTKE.obf", SNAPSHOT),
        ("json/config.obf", {**SNAPSHOT, "trainer.cash": 8000}),  # written last -> newest
    ])
    db, st = FakeDB(WIPED), FakeSettings(WIPED)

    assert pr.recover_wiped_trainer_data(db, st, uf, logger=None) is True
    assert db.config["trainer.cash"] == 8000          # newest snapshot wins
    assert st.config["trainer.cash"] == 8000          # in-memory settings updated too
    assert db.config["trainer.name"] == "PandaPrincess333"
    assert db.meta.get("trainer_cash_repair_v1") == "true"
    assert len(list(uf.glob("ankimon.db.bak-*"))) == 1  # DB backed up before write


def test_runs_only_once(tmp_path):
    uf = _profile(tmp_path, [("json/config.obf", SNAPSHOT)])
    db, st = FakeDB(WIPED), FakeSettings(WIPED)
    assert pr.recover_wiped_trainer_data(db, st, uf) is True
    # second invocation must be a no-op (flag gate) and not create a 2nd backup
    assert pr.recover_wiped_trainer_data(db, st, uf) is False
    assert len(list(uf.glob("ankimon.db.bak-*"))) == 1


def test_never_touches_healthy_profile(tmp_path):
    uf = _profile(tmp_path, [("json/config.obf", SNAPSHOT)])
    db = FakeDB({"trainer.cash": 5000, "trainer.level": 10, "trainer.xp": 50})
    assert pr.recover_wiped_trainer_data(db, FakeSettings({}), uf) is False
    assert db.config["trainer.cash"] == 5000
    assert not list(uf.glob("ankimon.db.bak-*"))


def test_does_not_restore_legitimately_spent_down_profile(tmp_path):
    # cash == 0 but level/xp > 0 -> a real player who spent their money, NOT a wipe
    uf = _profile(tmp_path, [("json/config.obf", SNAPSHOT)])
    db = FakeDB({"trainer.cash": 0, "trainer.level": 7, "trainer.xp": 300})
    assert pr.recover_wiped_trainer_data(db, FakeSettings({}), uf) is False
    assert db.config["trainer.cash"] == 0


def test_noop_when_no_positive_snapshot(tmp_path):
    uf = _profile(tmp_path, [("json/config.obf", WIPED)])
    db = FakeDB(WIPED)
    assert pr.recover_wiped_trainer_data(db, FakeSettings(WIPED), uf) is False
    assert not list(uf.glob("ankimon.db.bak-*"))


def test_sync_conflict_detection(tmp_path):
    uf = _profile(tmp_path, [
        ("config.sync-conflict-20260512-205742-ZZUGTKE.obf", SNAPSHOT),
        ("json/config.obf", SNAPSHOT),
    ])
    assert pr._has_sync_conflicts(uf) is True

    clean = _profile(tmp_path / "clean", [("json/config.obf", SNAPSHOT)])
    assert pr._has_sync_conflicts(clean) is False
