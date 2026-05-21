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


# --- git_pull_ff_only --------------------------------------------------------

class _FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _fake_git(responses):
    """Fake subprocess.run dispatching on the git subcommand (cmd[3])."""
    def _run(cmd, **kwargs):
        return responses.get(cmd[3], _FakeProc(0))
    return _run


def test_git_pull_reports_not_a_clone(monkeypatch):
    monkeypatch.setattr(um, "_git_repo_root", lambda: None)
    ok, msg = um.git_pull_ff_only()
    assert ok is False
    assert "not running from a git checkout" in msg.lower()


def test_git_pull_handles_git_missing_from_path(tmp_path, monkeypatch):
    monkeypatch.setattr(um, "_git_repo_root", lambda: tmp_path)
    monkeypatch.setattr(um.shutil, "which", lambda _name: None)
    ok, msg = um.git_pull_ff_only()
    assert ok is False
    assert "path" in msg.lower()


def test_git_pull_success_names_the_branch(tmp_path, monkeypatch):
    monkeypatch.setattr(um, "_git_repo_root", lambda: tmp_path)
    monkeypatch.setattr(um.shutil, "which", lambda _name: "/usr/bin/git")
    monkeypatch.setattr(um.subprocess, "run", _fake_git({
        "rev-parse": _FakeProc(0, "main\n"),
        "pull": _FakeProc(0, "Updating 111..222\n"),
        "submodule": _FakeProc(0, ""),
    }))
    ok, msg = um.git_pull_ff_only()
    assert ok is True
    assert "main" in msg


def test_git_pull_ff_failure_is_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(um, "_git_repo_root", lambda: tmp_path)
    monkeypatch.setattr(um.shutil, "which", lambda _name: "/usr/bin/git")
    monkeypatch.setattr(um.subprocess, "run", _fake_git({
        "rev-parse": _FakeProc(0, "feature\n"),
        "pull": _FakeProc(1, "", "fatal: Not possible to fast-forward, aborting."),
    }))
    ok, msg = um.git_pull_ff_only()
    assert ok is False
    assert "fast-forward" in msg.lower()


# --- pre-v2.0 version filtering ----------------------------------------------

def test_version_filter_keeps_2_0_and_above():
    assert um._is_supported_version("2.01-E") is True
    assert um._is_supported_version("2.0-E") is True
    assert um._is_supported_version("v2.0") is True


def test_version_filter_drops_pre_2_0_and_non_versions():
    for name in ["1.52-E", "1.931-E", "1.3962-E", "sprites", "nightly-release", "archive/h0tp/x"]:
        assert um._is_supported_version(name) is False, name


def test_fetch_releases_filters_pre_2_0(monkeypatch):
    monkeypatch.setattr(um, "_api_get", lambda _ep: [
        {"tag_name": "sprites", "zipball_url": "z", "body": ""},
        {"tag_name": "2.01-E", "zipball_url": "z", "body": ""},
        {"tag_name": "1.52-E", "zipball_url": "z", "body": ""},
    ])
    assert [r["name"] for r in um.fetch_releases()] == ["2.01-E"]


def test_fetch_tags_filters_pre_2_0(monkeypatch):
    monkeypatch.setattr(um, "_api_get", lambda _ep: [
        {"name": "nightly-release", "zipball_url": "z"},
        {"name": "2.0-E", "zipball_url": "z"},
        {"name": "1.3962-E", "zipball_url": "z"},
    ])
    assert [t["name"] for t in um.fetch_tags()] == ["2.0-E"]
