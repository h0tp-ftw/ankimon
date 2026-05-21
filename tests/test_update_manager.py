"""
Tests for the updater's git-clone safety guard (is_git_clone + apply_update).

Loading note: sibling test modules (test_database_manager, test_encounter_functions)
replace sys.modules['Ankimon'] / ['Ankimon.pyobj'] / ['Ankimon.resources'] with
bare or mock objects at import time. A plain `from Ankimon.pyobj import
update_manager` here therefore breaks depending on pytest collection order. We
rebuild a real namespace and exec the module from source so the test is
order-independent. (Removing the need for this kind of global stubbing is exactly
what the services-registry refactor targets.)
"""

import importlib.util
import sys
import types
from pathlib import Path

_SRC = Path(__file__).parent.parent / "src"


def _load_update_manager():
    # update_manager imports these at module load; only stub if nothing else has.
    if "aqt" not in sys.modules:
        aqt = types.ModuleType("aqt")
        aqt.mw = None
        sys.modules["aqt"] = aqt
    if "aqt.operations" not in sys.modules:
        ops = types.ModuleType("aqt.operations")
        ops.QueryOp = object
        sys.modules["aqt.operations"] = ops

    # Force real namespace packages (siblings may have replaced them with mocks).
    # Overwriting sys.modules['Ankimon'] here is safe because this test file
    # sorts last alphabetically — no other test module is collected/run after it.
    for name, path in [
        ("Ankimon", _SRC / "Ankimon"),
        ("Ankimon.pyobj", _SRC / "Ankimon" / "pyobj"),
    ]:
        ns = types.ModuleType(name)
        ns.__path__ = [str(path)]
        sys.modules[name] = ns

    # Load real resources (provides addon_dir) then update_manager, from disk.
    for modname, relpath in [
        ("Ankimon.resources", "resources.py"),
        ("Ankimon.pyobj.update_manager", "pyobj/update_manager.py"),
    ]:
        spec = importlib.util.spec_from_file_location(modname, _SRC / "Ankimon" / relpath)
        module = importlib.util.module_from_spec(spec)
        sys.modules[modname] = module
        spec.loader.exec_module(module)

    return sys.modules["Ankimon.pyobj.update_manager"]


um = _load_update_manager()


def test_is_git_clone_true_when_repo_root_has_dot_git(tmp_path, monkeypatch):
    # Mirror the real layout: <repo>/src/Ankimon, with .git at the repo root.
    addon = tmp_path / "repo" / "src" / "Ankimon"
    addon.mkdir(parents=True)
    monkeypatch.setattr(um, "addon_dir", addon)

    assert um.is_git_clone() is False  # no .git anywhere yet

    (tmp_path / "repo" / ".git").mkdir()  # repo root, two levels up
    assert um.is_git_clone() is True


def test_is_git_clone_true_when_addon_dir_is_repo(tmp_path, monkeypatch):
    addon = tmp_path / "ankimon"
    addon.mkdir()
    (addon / ".git").mkdir()
    monkeypatch.setattr(um, "addon_dir", addon)

    assert um.is_git_clone() is True


def test_is_git_clone_false_for_plain_install(tmp_path, monkeypatch):
    addon = tmp_path / "addons21" / "ankimon"
    addon.mkdir(parents=True)
    monkeypatch.setattr(um, "addon_dir", addon)

    assert um.is_git_clone() is False


def test_apply_update_refuses_on_git_clone_and_cleans_zip(tmp_path, monkeypatch):
    monkeypatch.setattr(um, "is_git_clone", lambda: True)
    dummy_zip = tmp_path / "update.zip"
    dummy_zip.write_bytes(b"PK\x03\x04")

    ok, msg = um.apply_update(str(dummy_zip))

    assert ok is False
    assert "git" in msg.lower()
    assert not dummy_zip.exists()  # guard cleaned up the downloaded archive
