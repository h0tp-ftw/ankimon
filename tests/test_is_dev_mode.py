"""Tier-1 contract for utils.is_dev_mode() (the developer-mode gate).

The helper is the single source of truth behind the developer-only menu entries
(Switch Account / Encounter Rate Simulator) and the reviewer test hotkey. It must
determine developer mode dynamically by checking if the active Anki Profile name
or the Trainer name contains the substring "dev_" or "_dev" at runtime.
"""

import sys
import pytest
from unittest.mock import MagicMock


@pytest.fixture
def dev(monkeypatch):
    import Ankimon.utils as utils_mod
    from Ankimon.utils import is_dev_mode

    store = {}

    class _Settings:
        def get(self, key, default=None):
            return store.get(key, default)

    monkeypatch.setattr(utils_mod.services, "settings", _Settings())

    # Setup aqt.mw mock
    mock_mw = MagicMock()
    mock_mw.pm = None

    if "aqt" not in sys.modules:
        aqt_mock = MagicMock()
        aqt_mock.mw = mock_mw
        monkeypatch.setitem(sys.modules, "aqt", aqt_mock)
    else:
        sys.modules["aqt"].mw = mock_mw
    monkeypatch.setattr(sys.modules["aqt"], "mw", mock_mw, raising=False)

    return is_dev_mode, store, mock_mw


def test_dev_mode_true_when_profile_has_prefix(dev):
    is_dev_mode, store, mock_mw = dev
    mock_mw.pm = MagicMock()
    mock_mw.pm.name = "dev_user"
    assert is_dev_mode() is True


def test_dev_mode_true_when_profile_has_suffix(dev):
    is_dev_mode, store, mock_mw = dev
    mock_mw.pm = MagicMock()
    mock_mw.pm.name = "user_dev"
    assert is_dev_mode() is True


def test_dev_mode_true_when_trainer_has_prefix(dev):
    is_dev_mode, store, mock_mw = dev
    store["trainer.name"] = "dev_trainer"
    assert is_dev_mode() is True


def test_dev_mode_true_when_trainer_has_suffix(dev):
    is_dev_mode, store, mock_mw = dev
    store["trainer.name"] = "trainer_dev"
    assert is_dev_mode() is True


def test_dev_mode_false_when_normal(dev):
    is_dev_mode, store, mock_mw = dev
    mock_mw.pm = MagicMock()
    mock_mw.pm.name = "normal_user"
    store["trainer.name"] = "normal_trainer"
    assert is_dev_mode() is False


def test_dev_mode_false_and_no_raise_when_settings_broken(monkeypatch):
    import Ankimon.utils as utils_mod
    from Ankimon.utils import is_dev_mode

    class _Broken:
        def get(self, *a, **k):
            raise RuntimeError("settings not ready")

    monkeypatch.setattr(utils_mod.services, "settings", _Broken())
    
    # Ensure profile name doesn't match either
    try:
        from aqt import mw
        if mw:
            mw.pm = None
    except Exception:
        pass

    assert is_dev_mode() is False
