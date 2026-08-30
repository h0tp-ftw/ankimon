"""Tests for pypresence's Discord IPC socket discovery, specifically the
Vesktop (Flatpak sandbox) path added for Linux Rich Presence support.

test_ipc_path() normally opens a real AF_UNIX connection to verify a
candidate socket actually works; that's mocked out here (kept True whenever
the path exists) so these tests only exercise get_ipc_path's own directory
search, not real socket I/O.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

# Other test files stub sys.modules["Ankimon"] with a bare, path-less
# ModuleType and never restore it (a known pre-existing pattern in this
# suite — see test_scaffolding_smoke.py / test_review_based_damage_multiplier.py).
# Depending on collection order that stub can still be in sys.modules, which
# breaks importing the real Ankimon.addon_files subpackage through the normal
# `import Ankimon...` path. Load the module directly from disk by file path
# instead, so this test doesn't depend on the shared Ankimon package state.
_pypresence_dir = (
    Path(__file__).parent.parent / "src" / "Ankimon" / "addon_files" / "lib" / "pypresence"
)

# utils.py does `from .exceptions import ...`, a relative import, so it needs
# a real parent package registered in sys.modules (not just loaded standalone).
_pkg_name = "ankimon_pypresence_under_test"
if _pkg_name not in sys.modules:
    _pkg = importlib.util.module_from_spec(
        importlib.util.spec_from_file_location(
            _pkg_name, _pypresence_dir / "__init__.py", submodule_search_locations=[str(_pypresence_dir)]
        )
    )
    sys.modules[_pkg_name] = _pkg
    _pkg.__spec__.loader.exec_module(_pkg)

_spec = importlib.util.spec_from_file_location(
    f"{_pkg_name}.utils", _pypresence_dir / "utils.py"
)
pypresence_utils = importlib.util.module_from_spec(_spec)
pypresence_utils.__package__ = _pkg_name
sys.modules[_spec.name] = pypresence_utils
_spec.loader.exec_module(pypresence_utils)
get_ipc_path = pypresence_utils.get_ipc_path

pytestmark = pytest.mark.skipif(
    sys.platform not in ("linux", "darwin"), reason="this IPC search path is Linux/macOS only"
)


@pytest.fixture(autouse=True)
def fake_socket_test(monkeypatch):
    """Any file that exists 'works' — isolates the directory-search logic
    under test from real AF_UNIX connection behavior."""
    monkeypatch.setattr(pypresence_utils, "test_ipc_path", lambda path: True)


@pytest.fixture
def runtime_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    return tmp_path


def test_finds_native_discord_socket(runtime_dir):
    sock_path = runtime_dir / "discord-ipc-0"
    sock_path.write_text("")
    assert get_ipc_path() == str(sock_path)


def test_finds_vesktop_flatpak_sandbox_socket(runtime_dir):
    """The actual bug this fixed: Vesktop's Flatpak build doesn't write its
    IPC socket into XDG_RUNTIME_DIR directly — it lands inside the sandbox's
    own bind-mounted runtime dir at .flatpak/dev.vencord.Vesktop/xdg-run."""
    vesktop_dir = runtime_dir / ".flatpak" / "dev.vencord.Vesktop" / "xdg-run"
    vesktop_dir.mkdir(parents=True)
    sock_path = vesktop_dir / "discord-ipc-0"
    sock_path.write_text("")
    assert get_ipc_path() == str(sock_path)


def test_returns_none_when_no_socket_anywhere(runtime_dir):
    assert get_ipc_path() is None


def test_prefers_native_path_over_vesktop_when_both_present(runtime_dir):
    native = runtime_dir / "discord-ipc-0"
    native.write_text("")
    vesktop_dir = runtime_dir / ".flatpak" / "dev.vencord.Vesktop" / "xdg-run"
    vesktop_dir.mkdir(parents=True)
    (vesktop_dir / "discord-ipc-0").write_text("")

    assert get_ipc_path() == str(native)
