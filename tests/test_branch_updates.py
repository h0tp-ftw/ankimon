"""Branch self-updater tests (F26): update_state.json persistence, the GitHub
branch/commit fetch helpers, and the changelog.check_branch_update poll flow.

Ported from BRRRR_Experimental. Tier-1 note: this file must stay collectable
in the Qt-free venv — aqt/anki are stubbed below, and the product code keeps
its heavy imports (markdown, Qt dialogs) lazy so importing ``changelog`` and
``update_manager`` here needs neither Qt nor markdown.
"""

import sys
import time
from unittest.mock import MagicMock, patch

# Mock aqt/anki namespaces if not already mocked by conftest
for name in [
    "aqt",
    "aqt.qt",
    "aqt.utils",
    "aqt.gui_hooks",
    "aqt.operations",
    "aqt.reviewer",
    "aqt.webview",
    "aqt.main",
    "anki",
    "anki.hooks",
    "anki.collection",
    "anki.models",
    "anki.notes",
    "anki.template",
    "anki.buildinfo",
]:
    if name not in sys.modules:
        sys.modules[name] = MagicMock()

# Ensure QueryOp mock is set up for operations
if "aqt.operations" in sys.modules:
    sys.modules["aqt.operations"].QueryOp = MagicMock()

# Import the actual modules under test
from Ankimon.pyobj import update_manager
from Ankimon import changelog


def test_update_state_read_write(tmp_path):
    """Test saving and reading the update state to update_state.json."""
    state_file = tmp_path / "update_state.json"

    with patch.object(update_manager, "get_update_state_path", return_value=state_file):
        # When file doesn't exist, read_update_state should return None
        assert update_manager.read_update_state() is None

        # Save state with a custom branch to prevent auto-migration to main
        update_manager.save_update_state(
            "branch", "custom_branch", "c0ffee12345", skip_until=999999.9
        )

        # Verify it exists and matches
        assert state_file.exists()
        state = update_manager.read_update_state()
        assert state is not None
        assert state["source_type"] == "branch"
        assert state["source_name"] == "custom_branch"
        assert state["commit_sha"] == "c0ffee12345"
        assert state["skip_until"] == 999999.9

        # Test set_update_skip_until
        update_manager.set_update_skip_until(888888.8)
        state = update_manager.read_update_state()
        assert state["skip_until"] == 888888.8


def test_update_state_migration_from_experimental(tmp_path):
    """Test that legacy BRRRR_Experimental branch is NOT auto-migrated to main."""
    state_file = tmp_path / "update_state.json"

    with patch.object(update_manager, "get_update_state_path", return_value=state_file):
        import json
        legacy_state = {
            "source_type": "branch",
            "source_name": "BRRRR_Experimental",
            "commit_sha": "c0ffee12345",
        }
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(legacy_state), encoding="utf-8")

        state = update_manager.read_update_state()
        assert state is not None
        assert state["source_name"] == "BRRRR_Experimental"


@patch("Ankimon.pyobj.update_manager._api_get")
def test_fetch_branch_sha(mock_api_get):
    """Test fetching branch commit SHA from GitHub API."""
    mock_api_get.return_value = {
        "name": "BRRRR_Experimental",
        "commit": {"sha": "a1b2c3d4e5f6"},
    }
    sha = update_manager.fetch_branch_sha("BRRRR_Experimental")
    assert sha == "a1b2c3d4e5f6"
    mock_api_get.assert_called_with("branches/BRRRR_Experimental")


@patch("Ankimon.pyobj.update_manager._api_get")
def test_fetch_commit_date(mock_api_get):
    """Test fetching commit date from GitHub API."""
    mock_api_get.return_value = {
        "sha": "a1b2c3d4e5f6",
        "commit": {"committer": {"date": "2026-05-24T12:00:00Z"}},
    }
    date = update_manager.fetch_commit_date("a1b2c3d4e5f6")
    assert date == "2026-05-24T12:00:00Z"
    mock_api_get.assert_called_with("commits/a1b2c3d4e5f6")

    # Test invalid SHA
    assert update_manager.fetch_commit_date("") is None
    assert update_manager.fetch_commit_date("not-a-sha") is None


@patch("Ankimon.changelog.QueryOp")
@patch("Ankimon.pyobj.update_manager.read_update_state")
@patch("Ankimon.pyobj.update_manager.is_git_clone", return_value=False)
def test_check_branch_update_no_state(mock_is_git_clone, mock_read_state, mock_query_op):
    """Test check_branch_update initializes tracking silently if no update state exists."""
    mock_read_state.return_value = None
    changelog.check_branch_update(True, True)
    mock_query_op.assert_called_once()


@patch("Ankimon.changelog.QueryOp")
@patch("Ankimon.pyobj.update_manager.read_update_state")
def test_check_branch_update_not_branch_type(mock_read_state, mock_query_op):
    """Test check_branch_update does nothing if source_type is not branch."""
    mock_read_state.return_value = {
        "source_type": "release",
        "source_name": "main",
        "commit_sha": "abc1234",
        "addon_version": changelog.addon_ver,
    }
    changelog.check_branch_update(True, True)
    mock_query_op.assert_not_called()


@patch("Ankimon.changelog.QueryOp")
@patch("Ankimon.pyobj.update_manager.read_update_state")
def test_check_branch_update_on_experimental_branch(mock_read_state, mock_query_op):
    """Test check_branch_update starts QueryOp if tracking a branch."""
    mock_read_state.return_value = {
        "source_type": "branch",
        "source_name": "BRRRR_Experimental",
        "commit_sha": "abc1234",
        "addon_version": changelog.addon_ver,
    }
    changelog.check_branch_update(True, True)
    mock_query_op.assert_called_once()


@patch("Ankimon.pyobj.update_manager._api_get")
def test_fetch_branch_commits_compare(mock_api_get):
    """Test fetching branch commits using the compare API."""
    mock_api_get.return_value = {
        "commits": [
            {
                "sha": "abcdef123456",
                "commit": {"message": "First commit message\nSome detail"},
            },
            {
                "sha": "789012345678",
                "commit": {"message": "Second commit message"},
            },
        ]
    }
    commits = update_manager.fetch_branch_commits("BRRRR_Experimental", "abc1234")
    assert len(commits) == 2
    # Commits are in reversed order (newest first)
    assert commits[0]["sha"] == "7890123"
    assert commits[0]["message"] == "Second commit message"
    assert commits[1]["sha"] == "abcdef1"
    assert commits[1]["message"] == "First commit message"
    mock_api_get.assert_called_with("compare/abc1234...BRRRR_Experimental")


@patch("Ankimon.pyobj.update_manager._api_get")
def test_fetch_branch_commits_fallback(mock_api_get):
    """Test fetching branch commits using the fallback commits list API."""
    mock_api_get.return_value = [
        {
            "sha": "111111122222",
            "commit": {"message": "Fallback commit message 1"},
        },
        {
            "sha": "333333344444",
            "commit": {"message": "Fallback commit message 2\nWith some details"},
        },
    ]
    # No local_sha passed, should fallback to commits endpoint
    commits = update_manager.fetch_branch_commits("BRRRR_Experimental")
    assert len(commits) == 2
    assert commits[0]["sha"] == "1111111"
    assert commits[0]["message"] == "Fallback commit message 1"
    assert commits[1]["sha"] == "3333333"
    assert commits[1]["message"] == "Fallback commit message 2"
    mock_api_get.assert_called_with("commits?sha=BRRRR_Experimental&per_page=5")


@patch("Ankimon.changelog.QueryOp")
@patch("Ankimon.pyobj.update_manager.read_update_state")
@patch("Ankimon.pyobj.update_manager.fetch_branch_sha")
@patch("Ankimon.pyobj.update_manager.fetch_branch_commits")
def test_check_branch_update_bg_op(
    mock_fetch_commits, mock_fetch_sha, mock_read_state, mock_query_op
):
    """Test that the background operation in check_branch_update fetches SHA and commits."""
    mock_read_state.return_value = {
        "source_type": "branch",
        "source_name": "BRRRR_Experimental",
        "commit_sha": "local_sha_123",
        "addon_version": changelog.addon_ver,
    }
    mock_fetch_sha.return_value = "remote_sha_456"
    mock_fetch_commits.return_value = [{"sha": "7890123", "message": "Commit message"}]

    changelog.check_branch_update(True, True)

    # Get the background operation function passed to QueryOp
    mock_query_op.assert_called_once()
    kwargs = mock_query_op.call_args[1]
    bg_func = kwargs.get("op") or mock_query_op.call_args[0][1]

    # Run the background function
    res_sha, res_commits = bg_func(None)

    assert res_sha == "remote_sha_456"
    assert res_commits == [{"sha": "7890123", "message": "Commit message"}]
    mock_fetch_sha.assert_called_once_with("BRRRR_Experimental")
    mock_fetch_commits.assert_called_once_with("BRRRR_Experimental", "local_sha_123")


@patch("Ankimon.changelog.QueryOp")
@patch("Ankimon.pyobj.update_manager.read_update_state")
def test_check_branch_update_skipped(mock_read_state, mock_query_op):
    """Test check_branch_update does nothing if skip_until is in the future."""
    mock_read_state.return_value = {
        "source_type": "branch",
        "source_name": "BRRRR_Experimental",
        "commit_sha": "abc1234",
        "skip_until": time.time() + 3600,  # 1 hour in the future
        "addon_version": changelog.addon_ver,
    }
    changelog.check_branch_update(True, True)
    mock_query_op.assert_not_called()


def test_read_update_state_rejects_non_dict_json(tmp_path):
    """update_state.json is user-editable: valid JSON that isn't an object
    (list/string/number/null) must read back as None, not leak through to
    callers that immediately call .get() on the result."""
    state_file = tmp_path / "update_state.json"

    with patch.object(update_manager, "get_update_state_path", return_value=state_file):
        for content in ('["not", "a", "dict"]', '"just a string"', "42", "null"):
            state_file.write_text(content, encoding="utf-8")
            assert update_manager.read_update_state() is None

        # Corrupt (non-JSON) content degrades to None too.
        state_file.write_text("{not json", encoding="utf-8")
        assert update_manager.read_update_state() is None


@patch("Ankimon.changelog.QueryOp")
@patch("Ankimon.pyobj.update_manager.read_update_state")
def test_check_branch_update_tolerates_bad_skip_until(mock_read_state, mock_query_op):
    """A null or non-numeric skip_until in the user-editable state file must not
    raise a TypeError against time.time(); the poll simply proceeds."""
    for bad_skip in (None, "not-a-number"):
        mock_query_op.reset_mock()
        mock_read_state.return_value = {
            "source_type": "branch",
            "source_name": "BRRRR_Experimental",
            "commit_sha": "abc1234",
            "skip_until": bad_skip,
            "addon_version": changelog.addon_ver,
        }
        changelog.check_branch_update(True, True)
        mock_query_op.assert_called_once()


@patch("Ankimon.changelog.QueryOp")
@patch("Ankimon.pyobj.update_manager.read_update_state")
@patch("Ankimon.pyobj.update_manager.fetch_branch_sha")
@patch("Ankimon.pyobj.update_manager.fetch_branch_commits")
def test_check_branch_update_null_commit_sha(
    mock_fetch_commits, mock_fetch_sha, mock_read_state, mock_query_op
):
    """A null commit_sha in the state file still polls; the background op passes
    None through to fetch_branch_commits (which then uses the fallback API)."""
    mock_read_state.return_value = {
        "source_type": "branch",
        "source_name": "BRRRR_Experimental",
        "commit_sha": None,
        "addon_version": changelog.addon_ver,
    }
    mock_fetch_sha.return_value = "remote_sha_456"
    mock_fetch_commits.return_value = []

    changelog.check_branch_update(True, True)

    mock_query_op.assert_called_once()
    bg_func = mock_query_op.call_args[1].get("op") or mock_query_op.call_args[0][1]
    res_sha, res_commits = bg_func(None)

    assert res_sha == "remote_sha_456"
    assert res_commits == []
    mock_fetch_commits.assert_called_once_with("BRRRR_Experimental", None)


@patch("Ankimon.pyobj.update_manager._api_get")
def test_fetch_branch_commits_non_string_local_sha_uses_fallback(mock_api_get):
    """A non-string local_sha (hostile state file) must not raise; the helper
    skips the compare API and uses the plain commits endpoint."""
    mock_api_get.return_value = [
        {"sha": "111111122222", "commit": {"message": "A commit"}},
    ]
    commits = update_manager.fetch_branch_commits("BRRRR_Experimental", 1234567890)
    assert commits == [{"sha": "1111111", "message": "A commit"}]
    mock_api_get.assert_called_once_with("commits?sha=BRRRR_Experimental&per_page=5")


@patch("Ankimon.pyobj.update_manager._api_get")
def test_fetch_branch_commits_empty_message(mock_api_get):
    """An empty commit message must not IndexError the whole feed; the other
    commits are still returned."""
    mock_api_get.return_value = [
        {"sha": "111111122222", "commit": {"message": ""}},
        {"sha": "333333344444", "commit": {"message": "Real message\nDetails"}},
    ]
    commits = update_manager.fetch_branch_commits("BRRRR_Experimental")
    assert commits == [
        {"sha": "1111111", "message": ""},
        {"sha": "3333333", "message": "Real message"},
    ]


@patch("Ankimon.pyobj.update_manager._api_get")
def test_fetch_helpers_tolerate_malformed_api_shapes(mock_api_get):
    """fetch_branch_sha / fetch_commit_date return None (not raise) when the
    API helper yields None or an unexpected JSON shape."""
    for bad in (None, [], "err", {"commit": None}, {"commit": "not-a-dict"}):
        mock_api_get.return_value = bad
        assert update_manager.fetch_branch_sha("BRRRR_Experimental") is None
        assert update_manager.fetch_commit_date("a1b2c3d4e5f6") is None


@patch("Ankimon.changelog.check_for_update")
def test_schedule_branch_update_check_uses_profile_open_hook(mock_check):
    """The boot wiring registers on gui_hooks.profile_did_open and only polls
    when a connection was available at boot (main re-fit of exp's
    profile-open call site)."""
    registered = []
    with patch.object(
        changelog.gui_hooks.profile_did_open, "append", side_effect=registered.append
    ):
        changelog.schedule_branch_update_check(True, True)
    assert len(registered) == 1

    # Hook fires after the profile opens -> the poll runs.
    registered[0]()
    mock_check.assert_called_once_with(True, True)

    # Offline boot: the hook is registered but never polls.
    mock_check.reset_mock()
    registered.clear()
    with patch.object(
        changelog.gui_hooks.profile_did_open, "append", side_effect=registered.append
    ):
        changelog.schedule_branch_update_check(False, True)
    registered[0]()
    mock_check.assert_not_called()


# --- check_for_update: channel dispatch (F: user-selectable update channel) ---


@patch("Ankimon.changelog._poll_release_channel")
@patch("Ankimon.changelog.check_branch_update")
@patch("Ankimon.pyobj.update_manager.get_update_channel", return_value="main")
def test_check_for_update_main_channel_uses_branch_poll(
    mock_channel, mock_branch, mock_release
):
    """The 'main' channel routes to the existing branch/commit poll, untouched."""
    changelog.check_for_update(True, True)
    mock_branch.assert_called_once_with(True, True)
    mock_release.assert_not_called()


@patch("Ankimon.changelog._poll_release_channel")
@patch("Ankimon.changelog.check_branch_update")
@patch("Ankimon.pyobj.update_manager.get_update_channel", return_value="stable")
def test_check_for_update_stable_channel_uses_release_poll(
    mock_channel, mock_branch, mock_release
):
    """A release channel routes to the release poll, not the branch poll."""
    changelog.check_for_update(True, True)
    mock_release.assert_called_once_with("stable")
    mock_branch.assert_not_called()


@patch("Ankimon.changelog._poll_release_channel")
@patch("Ankimon.changelog.check_branch_update")
def test_check_for_update_exits_when_no_ssh(mock_branch, mock_release):
    """No connectivity -> neither poll runs (and the channel is never read)."""
    changelog.check_for_update(True, False)
    mock_branch.assert_not_called()
    mock_release.assert_not_called()


@patch("Ankimon.changelog.QueryOp")
@patch("Ankimon.pyobj.update_manager.read_update_state", return_value={})
@patch("Ankimon.pyobj.update_manager.is_git_clone", return_value=False)
def test_poll_release_channel_starts_query_op(mock_clone, mock_state, mock_query_op):
    """With no clone and no snooze, the release poll kicks off its GitHub fetch."""
    changelog._poll_release_channel("stable")
    mock_query_op.assert_called_once()


@patch("Ankimon.changelog.QueryOp")
@patch("Ankimon.pyobj.update_manager.is_git_clone", return_value=True)
def test_poll_release_channel_skips_git_clone(mock_clone, mock_query_op):
    """Dev clones update via git pull, so the release poll never prompts them."""
    changelog._poll_release_channel("stable")
    mock_query_op.assert_not_called()


@patch("Ankimon.changelog.QueryOp")
@patch("Ankimon.pyobj.update_manager.read_update_state")
@patch("Ankimon.pyobj.update_manager.is_git_clone", return_value=False)
def test_poll_release_channel_respects_snooze(mock_clone, mock_state, mock_query_op):
    """A future skip_until suppresses the release poll (weekly snooze)."""
    mock_state.return_value = {"skip_until": time.time() + 604800}
    changelog._poll_release_channel("stable")
    mock_query_op.assert_not_called()
