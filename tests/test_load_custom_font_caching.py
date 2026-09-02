"""load_custom_font() used to call QFontDatabase.addApplicationFont() on
every single call — with no caching, a brand-new duplicate font entry got
registered every time, even for a file already registered (confirmed live:
20 uncached calls returned 20 distinct font ids — Qt's own
QFontDatabase.families() never shows the duplication, so that can't be used
to detect it; only the call count / returned ids can). Since this function
runs on every battle-scene redraw, a real play session could pile up
thousands of duplicate registrations, eventually bloating Qt's font database
until fontconfig's internal font-set sort crashed with a stack overflow
(observed as a SIGSEGV inside FcFontSetSort). Fixed by registering each font
file exactly once.
"""

import pytest


@pytest.fixture(scope="session")
def qapp():
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


def test_repeated_calls_register_the_font_file_only_once(qapp, monkeypatch):
    import Ankimon.utils as ankimon_utils
    from PyQt6.QtGui import QFontDatabase

    calls = []
    real_add = QFontDatabase.addApplicationFont

    def spy_add(path):
        calls.append(path)
        return real_add(path)

    monkeypatch.setattr(QFontDatabase, "addApplicationFont", staticmethod(spy_add))
    # The cache is a module-level set, shared across the whole process —
    # reset it so an earlier test's registration doesn't hide a regression.
    monkeypatch.setattr(ankimon_utils, "_registered_fonts", set())

    for _ in range(20):
        font = ankimon_utils.load_custom_font(20, 0)
        assert font is not None

    assert len(calls) == 1, (
        f"expected exactly 1 addApplicationFont() call across 20 load_custom_font() "
        f"calls for the same font file, got {len(calls)}"
    )


def test_different_language_fonts_each_register_once(qapp, monkeypatch):
    """language=1 (Western) and language=0 (default) use different font
    files — both should still register at most once each, not per-call."""
    import Ankimon.utils as ankimon_utils
    from PyQt6.QtGui import QFontDatabase

    calls = []
    real_add = QFontDatabase.addApplicationFont

    def spy_add(path):
        calls.append(path)
        return real_add(path)

    monkeypatch.setattr(QFontDatabase, "addApplicationFont", staticmethod(spy_add))
    monkeypatch.setattr(ankimon_utils, "_registered_fonts", set())

    for _ in range(10):
        ankimon_utils.load_custom_font(20, 0)
        ankimon_utils.load_custom_font(20, 1)

    # At most one registration per distinct underlying font file.
    assert len(calls) == len(set(calls))
    # And both language paths were actually exercised, not just one of them
    # happening to dedupe against itself — pin the exact distinct paths seen.
    from Ankimon.resources import font_path

    pkmn_w_bundled = (font_path / "pkmn_w.ttf").exists()
    assert any("Early GameBoy.ttf" in c for c in calls)
    if pkmn_w_bundled:
        # This repo ships pkmn_w.ttf, so language=1 must actually register
        # it — not silently reuse the Early GameBoy path, which would pass
        # the weaker "at most 2 distinct paths" check just as well.
        assert any("pkmn_w.ttf" in c for c in calls)
        assert len(set(calls)) == 2
    else:
        # No pkmn_w.ttf bundled -> language=1 falls back to Early GameBoy too.
        assert len(set(calls)) == 1


def test_a_failed_registration_is_retried_on_the_next_call(qapp, monkeypatch):
    """addApplicationFont() returning -1 (failure) must not be cached as a
    success — otherwise every later call for that file silently gives up
    retrying forever. First call fails, second call (for the same file)
    should genuinely retry the real registration rather than skip it."""
    import Ankimon.utils as ankimon_utils
    from PyQt6.QtGui import QFontDatabase

    real_add = QFontDatabase.addApplicationFont
    calls = []

    def flaky_add(path):
        calls.append(path)
        if len(calls) == 1:
            return -1  # simulate a failed registration
        return real_add(path)

    monkeypatch.setattr(QFontDatabase, "addApplicationFont", staticmethod(flaky_add))
    monkeypatch.setattr(ankimon_utils, "_registered_fonts", set())

    font1 = ankimon_utils.load_custom_font(20, 0)
    font2 = ankimon_utils.load_custom_font(20, 0)

    assert font1 is not None and font2 is not None
    # The failure must have triggered a genuine retry, not a skipped no-op.
    assert len(calls) == 2
    # And the retry succeeded, so a third call must NOT register again.
    ankimon_utils.load_custom_font(20, 0)
    assert len(calls) == 2
