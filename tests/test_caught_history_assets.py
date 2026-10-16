"""PR #892's WebEngine proof must never expose arbitrary local files."""

import pytest

from harness.checks.probe_real_caught_history import _resolve_bundled_asset

ICON_URL = "/_addons/ankimon/web/images/pokeball.png"


@pytest.fixture
def addon(tmp_path):
    """Create a disposable add-on tree with one allowed and one private file."""
    root = tmp_path / "addon"
    icon = root / "web/images/pokeball.png"
    icon.parent.mkdir(parents=True)
    icon.write_bytes(b"fixture icon")
    (root / "config.json").write_text("private settings")
    return root


def test_bundled_icon_is_allowed(addon):
    """The exact media URL resolves to the bundled icon."""
    assert _resolve_bundled_asset(addon, ICON_URL) == addon / "web/images/pokeball.png"


@pytest.mark.parametrize(
    "path",
    [
        "/_addons/ankimon//etc/passwd",
        "/_addons/ankimon/../../../etc/passwd",
        "/_addons/ankimon/web/images/../../config.json",
        "/_addons/ankimon/%2e%2e/%2e%2e/etc/passwd",
        "/_addons/ankimon/%2fetc/passwd",
        "/_addons/ankimon/C:/Windows/win.ini",
        "/_addons/ankimon/\\\\server\\share\\private",
        "/_addons/ankimon/config.json",
        "/_addons/ankimon/user_files/ankimon.db",
        "/_addons/ankimon/web/images/missing.png",
        "/_addons/other/web/images/pokeball.png",
        ICON_URL + "/../pokeball.png",
    ],
)
def test_unapproved_paths_are_rejected(addon, path):
    """Traversal, absolute paths, and files outside the allowlist are denied."""
    assert _resolve_bundled_asset(addon, path) is None


@pytest.mark.parametrize("target", ["file", "directory", "missing", "loop"])
def test_allowed_name_cannot_escape_through_symlinks(addon, tmp_path, target):
    """Canonical containment rejects escaped, missing, and looping targets."""
    icon = addon / "web/images/pokeball.png"
    icon.unlink()
    outside = tmp_path / "outside"
    if target == "file":
        outside.write_bytes(b"private")
    elif target == "directory":
        outside.mkdir()
    icon.symlink_to(icon if target == "loop" else outside)
    assert _resolve_bundled_asset(addon, ICON_URL) is None


@pytest.mark.parametrize("directory", [False, True])
def test_icon_must_exist_as_a_regular_file(addon, directory):
    """An allowed name alone cannot redirect to a missing file or directory."""
    icon = addon / "web/images/pokeball.png"
    icon.unlink()
    if directory:
        icon.mkdir()
    assert _resolve_bundled_asset(addon, ICON_URL) is None
