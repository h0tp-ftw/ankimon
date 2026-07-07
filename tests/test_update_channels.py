"""Update-channel logic (stable / experimental / main) in update_manager.

Covers the pure helpers that back the user-selectable auto-update channel:
version ordering under the project's decimal-minor tag scheme, tag→channel
classification, newest-release-per-channel selection, newer-than comparison,
and the channel get/set (default derived from the installed build). Kept
Qt-free via conftest's Ankimon stubbing (aqt/anki mocked below too, so the
module imports even when run standalone).
"""

import sys
from unittest.mock import MagicMock, patch

for _name in [
    "aqt", "aqt.qt", "aqt.utils", "aqt.gui_hooks", "aqt.operations",
    "aqt.reviewer", "aqt.webview", "aqt.main",
    "anki", "anki.hooks", "anki.collection",
]:
    sys.modules.setdefault(_name, MagicMock())
sys.modules["aqt.operations"].QueryOp = MagicMock()

from Ankimon.pyobj import update_manager as um  # noqa: E402


# --- tag → channel classification ---

def test_channel_of_tag():
    assert um.channel_of_tag("2.03") == um.CHANNEL_STABLE
    assert um.channel_of_tag("2.02-E") == um.CHANNEL_EXPERIMENTAL
    assert um.channel_of_tag("  1.52-E  ") == um.CHANNEL_EXPERIMENTAL


# --- decimal-minor version ordering ---

def test_version_key_decimal_minor_ordering():
    k = um._version_key
    # .43 < .50, so 1.43 is OLDER than 1.5 (not integer-compared)
    assert k("1.43") < k("1.5")
    assert k("2.02") < k("2.03")
    assert k("2.03") < k("2.1")
    assert k("1.52") < k("2.0")
    assert k("1.3962") < k("1.4")


def test_version_key_ignores_channel_suffix():
    assert um._version_key("2.02-E") == um._version_key("2.02")
    assert um._version_key("1.3962-E") < um._version_key("1.4")


def test_version_key_non_version_sorts_lowest():
    assert um._version_key("sprites") == (-1, 0.0)
    assert um._version_key("nightly-release") == (-1, 0.0)
    # multi-part semver is intentionally unsupported (documented) -> lowest
    assert um._version_key("2.0.1") == (-1, 0.0)


# --- newer-than ---

def test_is_newer_version():
    assert um.is_newer_version("2.03", "2.02") is True
    assert um.is_newer_version("2.02", "2.03") is False
    assert um.is_newer_version("2.03", "2.03") is False  # same is not newer
    assert um.is_newer_version("1.5", "1.43") is True
    # the -E channel suffix alone is not a version bump
    assert um.is_newer_version("2.03-E", "2.03") is False


# --- newest release per channel ---

_FAKE_RELEASES = [
    {"name": "2.03", "body": "", "zipball_url": "u1"},    # newest stable
    {"name": "2.02-E", "body": "", "zipball_url": "u2"},  # newest experimental
    {"name": "2.0-E", "body": "", "zipball_url": "u3"},   # older experimental
    {"name": "2.01", "body": "", "zipball_url": "u4"},    # older stable
]


def test_latest_release_for_channel_stable():
    with patch.object(um, "fetch_releases", return_value=list(_FAKE_RELEASES)):
        assert um.latest_release_for_channel(um.CHANNEL_STABLE)["name"] == "2.03"


def test_latest_release_for_channel_experimental():
    with patch.object(um, "fetch_releases", return_value=list(_FAKE_RELEASES)):
        assert um.latest_release_for_channel(um.CHANNEL_EXPERIMENTAL)["name"] == "2.02-E"


def test_latest_release_for_channel_main_is_none():
    # main is a branch, not a release — the branch-SHA poll handles it
    assert um.latest_release_for_channel(um.CHANNEL_MAIN) is None


def test_latest_release_for_channel_none_when_empty():
    with patch.object(um, "fetch_releases", return_value=[]):
        assert um.latest_release_for_channel(um.CHANNEL_STABLE) is None


def test_latest_selection_independent_of_api_order():
    shuffled = [_FAKE_RELEASES[3], _FAKE_RELEASES[1], _FAKE_RELEASES[0], _FAKE_RELEASES[2]]
    with patch.object(um, "fetch_releases", return_value=shuffled):
        assert um.latest_release_for_channel(um.CHANNEL_STABLE)["name"] == "2.03"
        assert um.latest_release_for_channel(um.CHANNEL_EXPERIMENTAL)["name"] == "2.02-E"


# --- channel get/set (default derived from the installed build) ---

def _fake_services(stored):
    mod = MagicMock()
    mod.services.settings.get.return_value = stored
    return mod


def test_get_update_channel_returns_stored_valid_value():
    with patch("Ankimon.pyobj.update_manager.read_update_state", return_value=None):
        with patch.dict(sys.modules, {"Ankimon.services": _fake_services("main")}):
            assert um.get_update_channel() == um.CHANNEL_MAIN


def test_get_update_channel_defaults_stable_for_plain_build():
    with patch("Ankimon.pyobj.update_manager.read_update_state", return_value=None):
        with patch.dict(sys.modules, {"Ankimon.services": _fake_services(None)}):
            with patch("Ankimon.resources.IS_EXPERIMENTAL_BUILD", False):
                assert um.get_update_channel() == um.CHANNEL_STABLE


def test_get_update_channel_defaults_experimental_for_e_build():
    # unrecognized stored value also falls back to the build-derived default
    with patch("Ankimon.pyobj.update_manager.read_update_state", return_value=None):
        with patch.dict(sys.modules, {"Ankimon.services": _fake_services("garbage")}):
            with patch("Ankimon.resources.IS_EXPERIMENTAL_BUILD", True):
                assert um.get_update_channel() == um.CHANNEL_EXPERIMENTAL


def test_set_update_channel_persists_known_channel():
    fake = _fake_services(None)
    with patch.dict(sys.modules, {"Ankimon.services": fake}):
        um.set_update_channel(um.CHANNEL_EXPERIMENTAL)
        fake.services.settings.set.assert_called_once_with(
            "misc.update_channel", um.CHANNEL_EXPERIMENTAL
        )


def test_set_update_channel_ignores_unknown():
    fake = _fake_services(None)
    with patch.dict(sys.modules, {"Ankimon.services": fake}):
        um.set_update_channel("not-a-channel")
        fake.services.settings.set.assert_not_called()
