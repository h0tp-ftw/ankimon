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
import json
import sys
import time
import types
import zipfile
from pathlib import Path

import pytest

_SRC = Path(__file__).parent.parent / "src"


def _load_update_manager():
    # This runs at collection time and temporarily overwrites global sys.modules
    # entries to exec update_manager from source in isolation. Snapshot every key
    # we touch and RESTORE it in a finally, so collecting this file does not leak a
    # bare 'Ankimon' namespace / 'aqt' stub into sibling tests (e.g. it would break
    # test_addon_integrity's import walk -> tip_of_the_day NameError on QDialog).
    # The returned module object stays valid for the test functions regardless.
    # (Removing the need for this global stubbing is what the #437 service-registry
    # refactor targets.)
    _keys = (
        "aqt", "aqt.operations", "Ankimon", "Ankimon.pyobj",
        "Ankimon.resources", "Ankimon.pyobj.update_manager",
    )
    _saved = {k: sys.modules.get(k) for k in _keys}
    try:
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
    finally:
        # Restore the global module table so this collection-time load can't
        # corrupt other test modules.
        for _k, _v in _saved.items():
            if _v is None:
                sys.modules.pop(_k, None)
            else:
                sys.modules[_k] = _v


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


def test_is_git_clone_stays_true_in_dev_mode(tmp_path, monkeypatch):
    addon = tmp_path / "ankimon"
    addon.mkdir()
    (addon / ".git").mkdir()
    monkeypatch.setattr(um, "addon_dir", addon)

    # Developer naming must not disable the checkout safety guard.
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

    ok, msg, pending_mod = um.apply_update(str(dummy_zip))

    assert ok is False
    assert pending_mod is None
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


def test_git_pull_refuses_dirty_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(um, "_git_repo_root", lambda: tmp_path)
    monkeypatch.setattr(um.shutil, "which", lambda _name: "/usr/bin/git")
    monkeypatch.setattr(
        um.subprocess,
        "run",
        _fake_git({"status": _FakeProc(0, " M src/Ankimon/example.py\n")}),
    )

    ok, msg = um.git_pull_ff_only()

    assert ok is False
    assert "local changes" in msg.lower()


def test_git_pull_refuses_detached_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(um, "_git_repo_root", lambda: tmp_path)
    monkeypatch.setattr(um.shutil, "which", lambda _name: "/usr/bin/git")
    monkeypatch.setattr(
        um.subprocess,
        "run",
        _fake_git({"rev-parse": _FakeProc(0, "HEAD\n")}),
    )

    ok, msg = um.git_pull_ff_only()

    assert ok is False
    assert "detached" in msg.lower()


# --- git_checkout_source -----------------------------------------------------


def test_git_checkout_source_refuses_dirty_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(um, "_git_repo_root", lambda: tmp_path)
    monkeypatch.setattr(um.shutil, "which", lambda _name: "/usr/bin/git")
    monkeypatch.setattr(
        um.subprocess,
        "run",
        _fake_git({"status": _FakeProc(0, " M src/Ankimon/example.py\n")}),
    )

    ok, msg = um.git_checkout_source("pr", "123")

    assert ok is False
    assert "local changes" in msg.lower()


def test_git_checkout_source_fetches_pr_detached(tmp_path, monkeypatch):
    monkeypatch.setattr(um, "_git_repo_root", lambda: tmp_path)
    monkeypatch.setattr(um.shutil, "which", lambda _name: "/usr/bin/git")
    calls = []

    def _run(cmd, **kwargs):
        calls.append(cmd[3:])
        if cmd[3] == "status":
            return _FakeProc(0, "")
        if cmd[3] == "rev-parse" and "--abbrev-ref" in cmd:
            return _FakeProc(0, "main\n")
        if cmd[3] == "rev-parse" and "--short" in cmd:
            return _FakeProc(0, "abc1234\n")
        return _FakeProc(0, "")

    monkeypatch.setattr(um.subprocess, "run", _run)

    ok, msg = um.git_checkout_source("pr", "123")

    assert ok is True
    assert "PR #123" in msg
    assert [
        "fetch",
        "--force",
        um.GIT_REMOTE_URL,
        "refs/pull/123/head",
    ] in calls
    assert ["checkout", "--detach", "FETCH_HEAD"] in calls
    assert ["submodule", "update", "--init", "--recursive"] in calls


def test_git_checkout_source_reattaches_existing_branch(tmp_path, monkeypatch):
    monkeypatch.setattr(um, "_git_repo_root", lambda: tmp_path)
    monkeypatch.setattr(um.shutil, "which", lambda _name: "/usr/bin/git")
    calls = []

    def _run(cmd, **kwargs):
        calls.append(cmd[3:])
        if cmd[3] == "status":
            return _FakeProc(0, "")
        if cmd[3] == "rev-parse" and "--abbrev-ref" in cmd:
            return _FakeProc(0, "HEAD\n")
        if cmd[3] == "rev-parse" and "--short" in cmd:
            return _FakeProc(0, "def5678\n")
        if cmd[3] == "show-ref":
            return _FakeProc(0, "")
        return _FakeProc(0, "")

    monkeypatch.setattr(um.subprocess, "run", _run)

    ok, msg = um.git_checkout_source("branch", "main")

    assert ok is True
    assert "branch 'main'" in msg
    assert ["checkout", "main"] in calls
    assert ["merge", "--ff-only", "FETCH_HEAD"] in calls
    assert ["checkout", "--detach", "FETCH_HEAD"] not in calls


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


# --- dating the installed build ------------------------------------------------
#
# Anki decides an addon is out of date by comparing meta.json's "mod" against
# AnkiWeb's listing timestamp, not by comparing version numbers. The in-app
# updater copies files in place and never goes through AddonManager.install(),
# which is the only thing that normally writes "mod" -- so without an explicit
# stamp the value keeps describing whichever build Anki last installed, and
# AnkiWeb's copy looks newer than a GitHub build that is actually ahead of it.


def _meta_at(tmp_path, monkeypatch, content=None):
    """Point the updater at a throwaway meta.json (never the real addon dir)."""
    path = tmp_path / "meta.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(um, "get_meta_json_path", lambda: path)
    return path


def test_parse_iso8601_handles_github_timestamps():
    assert um._parse_iso8601("1970-01-01T00:00:00Z") == 0
    assert um._parse_iso8601("2026-08-08T11:02:30Z") == 1786186950
    assert um._parse_iso8601(None) is None
    assert um._parse_iso8601("not a date") is None


def test_fetch_releases_keeps_published_at(monkeypatch):
    monkeypatch.setattr(um, "_api_get", lambda _ep: [
        {"tag_name": "2.4", "zipball_url": "z", "body": "",
         "published_at": "2026-08-08T11:02:30Z"},
    ])
    assert um.fetch_releases()[0]["published_at"] == "2026-08-08T11:02:30Z"


def test_fetch_releases_falls_back_to_created_at(monkeypatch):
    """A draft promoted later can carry a null published_at."""
    monkeypatch.setattr(um, "_api_get", lambda _ep: [
        {"tag_name": "2.4", "zipball_url": "z", "body": "",
         "published_at": None, "created_at": "2026-07-01T09:00:00Z"},
    ])
    assert um.fetch_releases()[0]["published_at"] == "2026-07-01T09:00:00Z"


def test_published_at_for_tag_matches_the_named_release():
    releases = [
        {"name": "2.3-E", "published_at": "2026-08-08T11:02:30Z"},
        {"name": "2.4", "published_at": "2026-09-01T10:00:00Z"},
    ]
    assert um.published_at_for_tag("2.4", releases) == "2026-09-01T10:00:00Z"


def test_published_at_for_tag_returns_none_when_unknown():
    releases = [{"name": "2.3-E", "published_at": "2026-08-08T11:02:30Z"}]
    assert um.published_at_for_tag("9.9", releases) is None
    assert um.published_at_for_tag("2.3-E", []) is None
    assert um.published_at_for_tag("", releases) is None
    assert um.published_at_for_tag("2.3-E", None) is None


def test_published_at_for_tag_tolerates_malformed_entries():
    """The picker's list is whatever the API returned; don't trust its shape."""
    releases = [None, "junk", {}, {"name": "2.4"}]
    assert um.published_at_for_tag("2.4", releases) is None


def test_fetch_ref_date_resolves_a_tag_name(monkeypatch):
    """The reason this function exists: commits/<tag> works, and dates a release."""
    seen = []

    def _api(endpoint):
        seen.append(endpoint)
        return {"commit": {"committer": {"date": "2026-08-08T11:02:30Z"}}}

    monkeypatch.setattr(um, "_api_get", _api)
    assert um.fetch_ref_date("2.3-E") == "2026-08-08T11:02:30Z"
    assert seen == ["commits/2.3-E"]


def test_fetch_ref_date_falls_back_to_author_date(monkeypatch):
    monkeypatch.setattr(
        um, "_api_get",
        lambda _ep: {"commit": {"author": {"date": "2026-01-02T03:04:05Z"}}},
    )
    assert um.fetch_ref_date("abc1234def") == "2026-01-02T03:04:05Z"


def test_fetch_ref_date_refuses_unsafe_refs(monkeypatch):
    seen = []
    monkeypatch.setattr(um, "_api_get", lambda ep: seen.append(ep))
    assert um.fetch_ref_date("../../../etc/passwd") is None
    assert um.fetch_ref_date("feature/slashes") is None
    assert um.fetch_ref_date("") is None
    assert seen == []  # nothing unsafe reached the API


def test_fetch_ref_date_refuses_dot_segments(monkeypatch):
    """The character class alone admits ".." — a URL normaliser eats it, and
    ``commits/..`` then addresses the repo rather than a commit."""
    seen = []
    monkeypatch.setattr(um, "_api_get", lambda ep: seen.append(ep))
    for ref in ("..", ".", "...", "..%2f", "2.0..2.1", ".hidden"):
        assert um.fetch_ref_date(ref) is None, ref
    assert seen == []


def test_is_safe_ref_still_accepts_real_tags():
    """The tags and SHAs the pickers actually offer must keep resolving."""
    for ref in ("2.03", "2.02-E", "v1.9.1", "abc1234def", "main", "some_branch"):
        assert um._is_safe_ref(ref) is True, ref


def test_fetch_commit_date_still_rejects_non_hex(monkeypatch):
    seen = []
    monkeypatch.setattr(um, "_api_get", lambda ep: seen.append(ep))
    assert um.fetch_commit_date("not-a-sha") is None
    assert um.fetch_commit_date("") is None
    assert seen == []


def test_resolve_build_mtime_prefers_release_published_at(monkeypatch):
    called = []
    monkeypatch.setattr(um, "fetch_ref_date", lambda ref: called.append(ref))
    ts = um.resolve_build_mtime("release", "2.4", "2.4", "2026-08-08T11:02:30Z")
    assert ts == 1786186950
    assert called == []  # a release needs no extra round trip


def test_resolve_build_mtime_falls_back_to_commit_date(monkeypatch):
    monkeypatch.setattr(
        um, "fetch_ref_date",
        lambda ref: "2026-01-02T03:04:05Z" if ref == "abc1234def" else None,
    )
    assert um.resolve_build_mtime("branch", "main", "abc1234def") == 1767323045


def test_resolve_build_mtime_dates_a_tag_by_its_name(monkeypatch):
    monkeypatch.setattr(
        um, "fetch_ref_date",
        lambda ref: "2026-01-02T03:04:05Z" if ref == "2.4" else None,
    )
    assert um.resolve_build_mtime("tag", "2.4", None) == 1767323045


def test_resolve_build_mtime_returns_none_when_unresolvable(monkeypatch):
    monkeypatch.setattr(um, "fetch_ref_date", lambda _ref: None)
    assert um.resolve_build_mtime("branch", "main", "deadbeef1") is None


def test_stamp_addon_mod_writes_when_newer(tmp_path, monkeypatch):
    path = _meta_at(
        tmp_path, monkeypatch,
        '{"mod": 100, "config": {"a": 1}, "disabled": false, "name": "Ankimon"}',
    )
    assert um.stamp_addon_mod(500) is True
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["mod"] == 500
    # Anki owns every other key in this file; none of them may be lost.
    assert data["config"] == {"a": 1}
    assert data["disabled"] is False
    assert data["name"] == "Ankimon"


def test_stamp_addon_mod_stamps_when_key_absent(tmp_path, monkeypatch):
    path = _meta_at(tmp_path, monkeypatch, '{"name": "Ankimon"}')
    assert um.stamp_addon_mod(500) is True
    assert json.loads(path.read_text(encoding="utf-8"))["mod"] == 500


def test_stamp_addon_mod_never_moves_backwards(tmp_path, monkeypatch):
    """A deliberate downgrade keeps the build the user chose.

    Anki compares ``installed_at >= server_mtime``, so lowering ``mod`` is what
    surfaces an AnkiWeb build. Refusing to lower it means installing an older tag
    does not invite Anki to put the AnkiWeb copy straight back over the top, and
    it keeps the stamp monotonic so it can never leave a user worse off.
    """
    path = _meta_at(tmp_path, monkeypatch, '{"mod": 900}')
    assert um.stamp_addon_mod(500) is False
    assert json.loads(path.read_text(encoding="utf-8"))["mod"] == 900


def test_stamp_addon_mod_is_a_noop_when_unchanged(tmp_path, monkeypatch):
    _meta_at(tmp_path, monkeypatch, '{"mod": 500}')
    assert um.stamp_addon_mod(500) is False


def test_stamp_addon_mod_leaves_missing_meta_alone(tmp_path, monkeypatch):
    """No meta.json means Anki is not managing this install (clone / hand-unzip)."""
    path = _meta_at(tmp_path, monkeypatch)
    assert um.stamp_addon_mod(500) is False
    assert not path.exists()


def test_stamp_addon_mod_leaves_unparseable_meta_untouched(tmp_path, monkeypatch):
    path = _meta_at(tmp_path, monkeypatch, "{not json")
    assert um.stamp_addon_mod(500) is False
    assert path.read_text(encoding="utf-8") == "{not json"


def test_write_json_atomic_leaves_no_temp_file_behind(tmp_path):
    """The temp file must be renamed away on success and removed on failure."""
    path = tmp_path / "meta.json"
    um._write_json_atomic(path, {"mod": 1})
    assert json.loads(path.read_text(encoding="utf-8")) == {"mod": 1}
    assert list(tmp_path.iterdir()) == [path]

    class _Unserialisable:
        pass

    with pytest.raises(TypeError):
        um._write_json_atomic(path, {"mod": _Unserialisable()})
    # the original survives and the aborted temp file is gone
    assert json.loads(path.read_text(encoding="utf-8")) == {"mod": 1}
    assert list(tmp_path.iterdir()) == [path]


def test_stamp_addon_mod_rejects_nonpositive(tmp_path, monkeypatch):
    path = _meta_at(tmp_path, monkeypatch, '{"mod": 100}')
    assert um.stamp_addon_mod(0) is False
    assert um.stamp_addon_mod(-1) is False
    assert json.loads(path.read_text(encoding="utf-8"))["mod"] == 100


def test_resolve_build_mtime_dates_a_tag_by_its_release(monkeypatch):
    """Tags name published releases and install identical code, so they share a date."""
    called = []
    monkeypatch.setattr(um, "fetch_ref_date", lambda ref: called.append(ref))
    ts = um.resolve_build_mtime("tag", "2.4", "2.4", "2026-08-08T11:02:30Z")
    assert ts == 1786186950
    assert called == []


def test_resolve_build_mtime_clamps_a_future_commit_date(monkeypatch):
    """A skewed clock or a crafted committer date must not pin mod into the future.

    stamp_addon_mod never moves backwards, so an unclamped future value would
    suppress every AnkiWeb update until that date actually arrived.
    """
    monkeypatch.setattr(um, "fetch_ref_date", lambda _ref: "2099-01-01T00:00:00Z")
    before = int(time.time())
    ts = um.resolve_build_mtime("pr", "123", "abc1234def")
    assert ts is not None
    assert before <= ts <= int(time.time())


def test_resolve_build_mtime_does_not_repeat_a_failed_lookup(monkeypatch):
    """commit_sha and source_name are the same string on the tag/release paths."""
    called = []
    monkeypatch.setattr(um, "fetch_ref_date", lambda ref: called.append(ref))
    assert um.resolve_build_mtime("tag", "2.4", "2.4") is None
    assert called == ["2.4"]


# --- apply_update end to end ---------------------------------------------------


def _staged_install(tmp_path, monkeypatch):
    """Set apply_update up to run past the git-clone guard against a temp dir."""
    addon = tmp_path / "addon"
    addon.mkdir()
    (addon / "old.py").write_text("old", encoding="utf-8")
    (addon / "meta.json").write_text(
        json.dumps({"mod": 100, "config": {"k": 1}, "disabled": False}),
        encoding="utf-8",
    )
    zip_path = tmp_path / "update.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("ankimon-main/src/Ankimon/__init__.py", "new")
        zf.writestr("ankimon-main/src/Ankimon/new.py", "new")

    monkeypatch.setattr(um, "addon_dir", addon)
    monkeypatch.setattr(um, "is_git_clone", lambda: False)
    monkeypatch.setattr(um, "_get_gitignore_patterns", lambda: [])
    monkeypatch.setattr(um, "_fetch_submodule_sha", lambda _ref: None)
    monkeypatch.setattr(um, "_download_and_extract_submodule", lambda *a, **k: None)
    monkeypatch.setattr(um, "save_update_state", lambda *a, **k: None)
    monkeypatch.setattr(um, "get_meta_json_path", lambda: addon / "meta.json")
    monkeypatch.setattr(um, "fetch_ref_date", lambda _ref: None)  # no network
    return addon, zip_path


def test_apply_update_returns_the_release_date_to_stamp(tmp_path, monkeypatch):
    """The published_at threaded from the dialog comes back as pending_mod."""
    addon, zip_path = _staged_install(tmp_path, monkeypatch)

    seen = {}
    real_resolve = um.resolve_build_mtime

    def _spy(source_type, source_name, commit_sha, published_at=None):
        seen["args"] = (source_type, source_name, commit_sha, published_at)
        return real_resolve(source_type, source_name, commit_sha, published_at)

    monkeypatch.setattr(um, "resolve_build_mtime", _spy)

    # Distinct values in every slot, so a transposed argument cannot pass.
    ok, _msg, pending_mod = um.apply_update(
        str(zip_path), "release", "rel-name", "sha-value", "2026-08-08T11:02:30Z"
    )

    assert ok is True
    assert seen["args"] == ("release", "rel-name", "sha-value", "2026-08-08T11:02:30Z")
    assert pending_mod == 1786186950
    # and the install really happened
    assert (addon / "new.py").exists()
    assert not (addon / "old.py").exists()


def test_apply_update_never_writes_meta_json_itself(tmp_path, monkeypatch):
    """The structural half of the concurrency fix.

    apply_update runs in a QueryOp worker. meta.json is Anki's file and Anki
    read-modify-writes it from the main thread, so the worker must not touch it
    at all — it resolves the timestamp and hands it back. If this assertion ever
    fails, the read-modify-write has crept back onto the worker thread and the
    lost-update race is live again.
    """
    addon, zip_path = _staged_install(tmp_path, monkeypatch)
    before = (addon / "meta.json").read_text(encoding="utf-8")

    ok, _msg, pending_mod = um.apply_update(
        str(zip_path), "release", "2.4", "2.4", "2026-08-08T11:02:30Z"
    )

    assert ok is True
    assert pending_mod == 1786186950  # resolved, but deliberately not written
    assert (addon / "meta.json").read_text(encoding="utf-8") == before


def test_a_concurrent_config_write_survives_the_stamp(tmp_path, monkeypatch):
    """The regression this worker/main-thread split exists for.

    Anki keeps running while the updater worker installs. If the worker had
    snapshotted meta.json and written it back, a config Anki wrote in between —
    the add-on config dialog, an enable/disable toggle — would be reverted to
    that snapshot. Resolving in the worker and writing on the main thread means
    the write reads whatever is actually on disk at the moment it runs.
    """
    addon, zip_path = _staged_install(tmp_path, monkeypatch)
    meta_path = addon / "meta.json"

    # --- worker thread: install, resolve the date, touch nothing else ---
    ok, _msg, pending_mod = um.apply_update(
        str(zip_path), "release", "2.4", "2.4", "2026-08-08T11:02:30Z"
    )
    assert ok is True and pending_mod == 1786186950

    # --- main thread, meanwhile: Anki writes a config change ---
    meta_path.write_text(
        json.dumps({"mod": 100, "config": {"k": 2, "added": True}, "disabled": True}),
        encoding="utf-8",
    )

    # --- main thread: the stamp, reading current state rather than a snapshot ---
    assert um.stamp_addon_mod(pending_mod) is True

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["mod"] == 1786186950
    # The change that a stale snapshot would have silently discarded.
    assert meta["config"] == {"k": 2, "added": True}
    assert meta["disabled"] is True


def test_apply_update_does_not_stamp_when_the_install_rolls_back(tmp_path, monkeypatch):
    """A late failure restores the old build, so meta.json must not claim the new one.

    Returning a timestamp here would have the caller date restored old code as
    the new build -- Anki would read it as current and suppress the update that
    repairs it. The guard is that the rollback path returns None, and the
    dialog's success callback only stamps when the install actually succeeded.
    """
    addon, zip_path = _staged_install(tmp_path, monkeypatch)

    def explode(msg):
        if "Update complete" in msg:
            raise RuntimeError("taskman gone")

    ok, msg, pending_mod = um.apply_update(
        str(zip_path),
        "release",
        "2.4",
        "2.4",
        "2026-08-08T11:02:30Z",
        status_cb=explode,
    )

    assert ok is False
    assert pending_mod is None  # nothing for the caller to stamp
    # The rollback really ran: the previous build's files are back. (It restores
    # the backup over the top rather than deleting files the update added, so
    # new.py survives — pre-existing behaviour, not what this test is about.)
    assert "rolled back" in msg.lower()
    assert (addon / "old.py").read_text(encoding="utf-8") == "old"
    # ...and meta.json still describes the build that is actually on disk
    assert json.loads((addon / "meta.json").read_text(encoding="utf-8"))["mod"] == 100
