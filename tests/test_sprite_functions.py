"""
Tests for sprite_functions — migrated off `mw.logger` onto the `services`
registry. Like test_badges_functions, the entire setup is a plain import plus
`services.logger = FakeLogger()`; no `aqt`, no `sys.modules` surgery. The import
below would fail outright if the module still pulled in Anki.
"""

import pytest

from Ankimon.services import services
from Ankimon.functions import sprite_functions as sf


class FakeLogger:
    """Records log calls so tests can assert on them, with no Qt/Anki."""

    def __init__(self):
        self.logs = []

    def log(self, level, message):
        self.logs.append((level, message))


@pytest.fixture(autouse=True)
def fake_logger():
    services.reset()
    logger = FakeLogger()
    services.logger = logger
    yield logger
    services.reset()


def test_found_sprite_returns_path_and_logs_debug(fake_logger, monkeypatch):
    expected = sf._path_format(back=False, id=25, gif=False, shiny=False, female=False)
    monkeypatch.setattr("os.path.exists", lambda p: p == expected)

    result = sf.get_sprite_path("front", "png", 25, shiny=False, gender="M")

    assert result == expected
    assert ("debug", f"Sprite found: {expected}") in fake_logger.logs


def test_missing_sprite_returns_substitute_and_logs_warning(fake_logger, monkeypatch):
    monkeypatch.setattr("os.path.exists", lambda p: False)

    result = sf.get_sprite_path("front", "png", 999999, shiny=False, gender="M")

    assert result == sf.SUBSTITUTE_PATH
    assert any(level == "warning" for level, _ in fake_logger.logs)


def test_gender_fallback_to_nongendered(fake_logger, monkeypatch):
    # Female sprite absent, non-gendered present -> should fall back to it.
    nongendered = sf._path_format(back=False, id=25, gif=False, shiny=False, female=False)
    monkeypatch.setattr("os.path.exists", lambda p: p == nongendered)

    result = sf.get_sprite_path("front", "png", 25, shiny=False, gender="F")

    assert result == nongendered
