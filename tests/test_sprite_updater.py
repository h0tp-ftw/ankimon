import requests
import json
import hashlib
import os
import sys
import types
import importlib.util
from pathlib import Path
import pytest

_SRC = Path(__file__).parent.parent / "src"

def _load_sprite_updater():
    # Save original sys.modules state
    old_modules = dict(sys.modules)
    
    # Setup stubs for collections & tests to bypass aqt/Qt dependencies in non-Anki test environment
    if "aqt" not in sys.modules:
        aqt = types.ModuleType("aqt")
        aqt.mw = None
        sys.modules["aqt"] = aqt
        
    if "aqt.operations" not in sys.modules:
        ops = types.ModuleType("aqt.operations")
        ops.QueryOp = object
        sys.modules["aqt.operations"] = ops
        
    ge = types.ModuleType("Ankimon.gui_entities")
    ge.AgreementDialog = object
    sys.modules["Ankimon.gui_entities"] = ge
    
    for name, path in [
        ("Ankimon", _SRC / "Ankimon"),
        ("Ankimon.pyobj", _SRC / "Ankimon" / "pyobj"),
    ]:
        ns = types.ModuleType(name)
        ns.__path__ = [str(path)]
        sys.modules[name] = ns

    spec = importlib.util.spec_from_file_location("Ankimon.resources", _SRC / "Ankimon" / "resources.py")
    res_mod = importlib.util.module_from_spec(spec)
    sys.modules["Ankimon.resources"] = res_mod
    spec.loader.exec_module(res_mod)

    spec = importlib.util.spec_from_file_location("Ankimon.pyobj.download_sprites", _SRC / "Ankimon" / "pyobj" / "download_sprites.py")
    ds_mod = importlib.util.module_from_spec(spec)
    sys.modules["Ankimon.pyobj.download_sprites"] = ds_mod
    spec.loader.exec_module(ds_mod)

    spec = importlib.util.spec_from_file_location("Ankimon.pyobj.sprite_updater", _SRC / "Ankimon" / "pyobj" / "sprite_updater.py")
    su_mod = importlib.util.module_from_spec(spec)
    sys.modules["Ankimon.pyobj.sprite_updater"] = su_mod
    spec.loader.exec_module(su_mod)
    
    # Restore original sys.modules, keeping only the newly imported sprite_updater module
    for k in list(sys.modules.keys()):
        if k not in old_modules and k != "Ankimon.pyobj.sprite_updater":
            del sys.modules[k]
    for k, v in old_modules.items():
        sys.modules[k] = v
    sys.modules["Ankimon.pyobj.sprite_updater"] = su_mod
    
    return su_mod

su = _load_sprite_updater()


def test_git_blob_sha1(tmp_path):
    dest_dir = tmp_path / "sprites"
    dest_dir.mkdir()
    test_file = dest_dir / "test.png"
    content = b"hello"
    test_file.write_bytes(content)

    hasher = hashlib.sha1()
    hasher.update(f"blob {len(content)}\0".encode("utf-8"))
    hasher.update(content)
    expected_sha = hasher.hexdigest()

    local_files = su.get_local_sprites_manifest(dest_dir)

    assert local_files["test.png"] == expected_sha
    assert expected_sha == "b6fc4c620b67d95f953a5c1c1230aaab5db5a1b0"


class MockResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code != 200:
            raise Exception("HTTP Error")


def test_sprite_diff_logic(tmp_path, monkeypatch):
    dest_dir = tmp_path / "sprites"
    dest_dir.mkdir()
    
    (dest_dir / "a.png").write_bytes(b"content_a")
    (dest_dir / "b.png").write_bytes(b"content_b")
    (dest_dir / "c.png").write_bytes(b"content_c")

    sha_a = hashlib.sha1(b"blob 9\0content_a").hexdigest()
    sha_b_new = hashlib.sha1(b"blob 13\0new_content_b").hexdigest()
    sha_d = hashlib.sha1(b"blob 9\0content_d").hexdigest()

    mock_commits_api = {"sha": "mock_remote_commit_sha12345"}
    mock_tree_api = {
        "tree": [
            {"path": "a.png", "type": "blob", "sha": sha_a},
            {"path": "b.png", "type": "blob", "sha": sha_b_new},
            {"path": "d.png", "type": "blob", "sha": sha_d},
        ]
    }

    def mock_get(url, *args, **kwargs):
        if "commits/main" in url:
            return MockResponse(mock_commits_api)
        elif "git/trees" in url:
            return MockResponse(mock_tree_api)
        return MockResponse({}, 404)

    import requests
    monkeypatch.setattr(requests, "get", mock_get)

    res = su.calculate_sprite_diff(dest_dir)

    assert res["status"] == "update_available"
    assert res["remote_sha"] == "mock_remote_commit_sha12345"
    assert "d.png" in res["added"]
    assert "b.png" in res["modified"]
    assert "c.png" in res["deleted"]


def test_sprite_updater_snooze(tmp_path, monkeypatch):
    dest_dir = tmp_path / "sprites"
    dest_dir.mkdir()
    
    import time
    import json
    state_path = tmp_path / "sprites_update_state.json"
    state_path.write_text(json.dumps({
        "commit_sha": "new_remote_commit_sha",
        "snooze_until": time.time() + 3600
    }), encoding="utf-8")

    mock_commits_api = {"sha": "new_remote_commit_sha"}
    calls = []
    def mock_get(url, *args, **kwargs):
        calls.append(url)
        return MockResponse(mock_commits_api)

    import requests
    monkeypatch.setattr(requests, "get", mock_get)

    res_snoozed = su.calculate_sprite_diff(dest_dir, silent=True, ignore_snooze=False)
    assert res_snoozed["status"] == "snoozed"
    assert calls == []

    # 2. With snooze active and ignore_snooze=True -> should ignore snooze and proceed
    mock_tree_api = {
        "tree": [
            {"path": "a.png", "type": "blob", "sha": "some_sha"}
        ]
    }
    def mock_get_full(url, *args, **kwargs):
        if "commits/main" in url:
            return MockResponse(mock_commits_api)
        return MockResponse(mock_tree_api)
    monkeypatch.setattr(requests, "get", mock_get_full)

    res_ignored = su.calculate_sprite_diff(dest_dir, silent=True, ignore_snooze=True)
    assert res_ignored["status"] == "update_available"
    assert res_ignored["added"] == ["a.png"]
