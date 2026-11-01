"""Host patches must restore state across repeated uses and separate tests."""

from types import ModuleType

import pytest

from anki_fakes import make_anki_host, make_file_dialog


def test_repeated_host_patches_restore_original_attributes(tmp_path, monkeypatch):
    host = make_anki_host(tmp_path)
    dialog = make_file_dialog()
    assert isinstance(host, ModuleType)
    targets = (
        (host, "mw"),
        (host.mw, "taskman"),
        (host.mw, "pm"),
        (host.mw.taskman, "run_in_background"),
        (host.mw.progress, "single_shot"),
        (dialog, "getOpenFileName"),
    )
    for target, name in targets:
        original = getattr(target, name)
        for _ in range(2):
            replacement = object()
            with monkeypatch.context() as patcher:
                patcher.setattr(target, name, replacement)
                assert getattr(target, name) is replacement
            assert getattr(target, name) is original


@pytest.mark.parametrize("run", [1, 2])
def test_host_fixture_has_no_state_from_previous_test(anki_host, run):
    import aqt

    assert aqt is anki_host
    assert aqt.mw.pm.profile == {}
    aqt.mw.pm.save.assert_not_called()
    aqt.mw.pm.profile["changed"] = run
    aqt.mw.pm.save()


def test_factories_do_not_share_nested_state(tmp_path):
    first, second = make_anki_host(tmp_path), make_anki_host(tmp_path)
    first.mw.pm.profile["changed"] = True
    first.mw.taskman.run_in_background("task")
    assert second.mw.pm.profile == {}
    second.mw.taskman.run_in_background.assert_not_called()

