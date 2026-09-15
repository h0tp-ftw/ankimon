"""Focused Tier-1 coverage for the updater dialog's busy-state controls."""

import importlib.util
import sys
import types
from pathlib import Path


_SRC = Path(__file__).parent.parent / "src"


def _load_update_dialog():
    module_names = (
        "aqt",
        "aqt.operations",
        "aqt.qt",
        "aqt.theme",
        "Ankimon",
        "Ankimon.pyobj",
        "Ankimon.pyobj.update_dialog",
        "Ankimon.pyobj.update_manager",
        "Ankimon.resources",
    )
    saved = {name: sys.modules.get(name) for name in module_names}
    try:
        aqt = types.ModuleType("aqt")
        aqt.mw = None
        sys.modules["aqt"] = aqt

        operations = types.ModuleType("aqt.operations")
        operations.QueryOp = object
        sys.modules["aqt.operations"] = operations

        qt = types.ModuleType("aqt.qt")
        qt.Qt = types.SimpleNamespace(
            ConnectionType=types.SimpleNamespace(DirectConnection=object())
        )
        for name in (
            "QDialog",
            "QVBoxLayout",
            "QHBoxLayout",
            "QLabel",
            "QPushButton",
            "QComboBox",
            "QProgressBar",
            "QTabWidget",
            "QWidget",
            "QMessageBox",
            "QGroupBox",
            "QFrame",
            "QSizePolicy",
            "QSpacerItem",
            "QTextBrowser",
            "QCheckBox",
        ):
            setattr(qt, name, type(name, (), {}))
        sys.modules["aqt.qt"] = qt

        theme = types.ModuleType("aqt.theme")
        theme.theme_manager = types.SimpleNamespace(night_mode=False)
        sys.modules["aqt.theme"] = theme

        for name, path in (
            ("Ankimon", _SRC / "Ankimon"),
            ("Ankimon.pyobj", _SRC / "Ankimon" / "pyobj"),
        ):
            package = types.ModuleType(name)
            package.__path__ = [str(path)]
            package.__package__ = name
            sys.modules[name] = package

        update_manager = types.ModuleType("Ankimon.pyobj.update_manager")
        for name in (
            "fetch_releases",
            "fetch_tags",
            "fetch_branches",
            "fetch_open_prs",
            "apply_update",
            "_download_zip_to_temp",
            "_download_branch_zip",
            "_download_pr_zip",
            "read_update_state",
            "fetch_branch_sha",
            # This tuple mirrors update_dialog's `from .update_manager import`
            # list — a name added there must be added here, or this module fails
            # to import at collection time.
            "published_at_for_tag",
            "stamp_addon_mod",
            # Git-checkout helpers. The default stub returns None, so
            # is_git_clone() is falsy here and these tests keep exercising the
            # non-Git download path.
            "is_git_clone",
            "get_git_checkout_info",
            "git_checkout_source",
            "fetch_branch_relation",
        ):
            setattr(update_manager, name, lambda *args, **kwargs: None)
        sys.modules["Ankimon.pyobj.update_manager"] = update_manager

        resources = types.ModuleType("Ankimon.resources")
        resources.addon_ver = "test"
        resources.IS_EXPERIMENTAL_BUILD = False
        sys.modules["Ankimon.resources"] = resources

        spec = importlib.util.spec_from_file_location(
            "Ankimon.pyobj.update_dialog",
            _SRC / "Ankimon" / "pyobj" / "update_dialog.py",
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


update_dialog = _load_update_dialog()


class _Control:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.visible = True
        self.text = ""

    def isEnabled(self):
        return self.enabled

    def setText(self, text):
        self.text = text

    def setEnabled(self, enabled):
        self.enabled = enabled

    def setVisible(self, visible):
        self.visible = visible


class _Progress:
    def __init__(self):
        self.visible = False
        self.value = None

    def setVisible(self, visible):
        self.visible = visible

    def setValue(self, value):
        self.value = value


class _Label:
    def __init__(self):
        self.text = ""

    def setText(self, text):
        self.text = text


class _FakeQueryOp:
    last = None
    raise_on_run = False

    def __init__(self, *, parent, op, success):
        self.parent = parent
        self.op = op
        self.success = success
        self.failure_callback = None
        _FakeQueryOp.last = self

    def failure(self, callback):
        self.failure_callback = callback
        return self

    def without_collection(self):
        return self

    def run_in_background(self):
        if self.raise_on_run:
            raise RuntimeError("query submission failed")

    def fail(self, exc):
        assert self.failure_callback is not None
        self.failure_callback(exc)


class _Signal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback, *args, **kwargs):
        self.callbacks.append(callback)

    def emit(self, *args):
        for callback in self.callbacks:
            callback(*args)


class _FakeSpriteThread:
    def __init__(self, *args):
        self.progress_signal = _Signal()
        self.status_signal = _Signal()
        self.finished_signal = _Signal()
        self.finished = _Signal()
        self.started = False
        self.cancelled = False
        self.running = False

    def start(self):
        self.started = True
        self.running = True

    def cancel(self):
        self.cancelled = True

    def isRunning(self):
        return self.running


def _make_dialog():
    dialog = types.SimpleNamespace(
        progress_bar=_Progress(),
        status_label=_Label(),
        sprites_status=_Label(),
        sprites_progress=_Progress(),
        brrr_update_btn=_Control(True),
        update_latest_btn=_Control(False),
        release_btn=_Control(True),
        dev_install_btn=_Control(True),
        sprites_check_btn=_Control(True),
        sprites_update_btn=_Control(True),
        _busy_operations=set(),
        _action_button_states={},
        _closing=False,
        _close_finalized=False,
        _sprites_busy_token=None,
        sprites_thread=None,
        # Non-Git install: _run_update branches on _git_clone to choose between
        # the download and the git-checkout path, and _git_info gates the
        # fast-forward button. These fakes exercise the download path.
        _git_clone=False,
        _git_info={},
        _git_remote_sha=None,
        _git_ff_blocked=False,
    )
    dialog._action_buttons = types.MethodType(
        update_dialog.UpdateDialog._action_buttons, dialog
    )
    dialog._set_action_enabled = types.MethodType(
        update_dialog.UpdateDialog._set_action_enabled, dialog
    )
    dialog._begin_busy = types.MethodType(update_dialog.UpdateDialog._begin_busy, dialog)
    dialog._end_busy = types.MethodType(update_dialog.UpdateDialog._end_busy, dialog)
    dialog._defer_close_for_sprite_thread = types.MethodType(
        update_dialog.UpdateDialog._defer_close_for_sprite_thread, dialog
    )
    return dialog, dialog._action_buttons()


def test_busy_state_disables_all_actions_and_restores_prior_state():
    dialog, buttons = _make_dialog()

    busy_token = update_dialog.UpdateDialog._begin_busy(dialog)

    assert dialog.progress_bar.visible is True
    assert dialog.progress_bar.value == 0
    assert all(button.enabled is False for button in buttons)

    update_dialog.UpdateDialog._end_busy(dialog, busy_token)

    assert dialog.progress_bar.visible is False
    assert [button.enabled for button in buttons] == [
        True,
        False,
        True,
        True,
        True,
        True,
    ]
    assert dialog.status_label.text == ""


def test_git_pull_button_joins_the_busy_cycle_and_restores_its_gate():
    # Git-checkout mode adds git_pull_btn. It must be disabled with the others
    # while busy and come back to the state the checkout gate chose (disabled on
    # a dirty or detached tree), because _end_busy restores from
    # _action_button_states rather than re-deriving it.
    dialog, _ = _make_dialog()
    dialog._git_clone = True
    dialog._git_info = {"branch": "main", "sha": "abc1234", "dirty": True}
    dialog._git_remote_sha = "f" * 40
    dialog._git_ff_blocked = False
    dialog.git_pull_btn = _Control(True)
    update_dialog.UpdateDialog._set_action_enabled(dialog, dialog.git_pull_btn, False)
    assert dialog.git_pull_btn in dialog._action_buttons()

    busy_token = update_dialog.UpdateDialog._begin_busy(dialog)
    assert dialog.git_pull_btn.enabled is False
    update_dialog.UpdateDialog._end_busy(dialog, busy_token)
    assert dialog.git_pull_btn.enabled is False

    # A clean, attached checkout is allowed to pull, and that survives a cycle.
    update_dialog.UpdateDialog._set_action_enabled(dialog, dialog.git_pull_btn, True)
    busy_token = update_dialog.UpdateDialog._begin_busy(dialog)
    assert dialog.git_pull_btn.enabled is False
    update_dialog.UpdateDialog._end_busy(dialog, busy_token)
    assert dialog.git_pull_btn.enabled is True


def test_overlapping_operations_keep_busy_and_apply_latest_button_states():
    dialog, buttons = _make_dialog()
    dialog.status_label.setText("Working...")

    first_token = update_dialog.UpdateDialog._begin_busy(dialog)
    dialog.progress_bar.setValue(63)
    second_token = update_dialog.UpdateDialog._begin_busy(dialog)

    assert dialog.progress_bar.value == 63
    update_dialog.UpdateDialog._set_action_enabled(
        dialog, dialog.update_latest_btn, True
    )
    update_dialog.UpdateDialog._set_action_enabled(dialog, dialog.release_btn, False)
    assert all(button.enabled is False for button in buttons)

    update_dialog.UpdateDialog._end_busy(dialog, first_token)

    assert dialog.progress_bar.visible is True
    assert dialog.status_label.text == "Working..."
    assert all(button.enabled is False for button in buttons)

    update_dialog.UpdateDialog._end_busy(dialog, second_token)

    assert dialog.progress_bar.visible is False
    assert [button.enabled for button in buttons] == [
        True,
        True,
        False,
        True,
        True,
        True,
    ]
    assert dialog.status_label.text == ""


def test_query_workflow_failures_restore_all_controls():
    original_query_op = update_dialog.QueryOp
    update_dialog.QueryOp = _FakeQueryOp
    try:
        for workflow in (
            update_dialog.UpdateDialog._load_data,
            update_dialog.UpdateDialog._load_dev_data,
        ):
            dialog, buttons = _make_dialog()
            workflow(dialog)

            assert all(button.enabled is False for button in buttons)
            _FakeQueryOp.last.fail(RuntimeError("network failed"))

            assert not dialog._busy_operations
            assert [button.enabled for button in buttons] == [
                True,
                False,
                True,
                True,
                True,
                True,
            ]
            assert "failed" in dialog.status_label.text.lower()

        _FakeQueryOp.raise_on_run = True
        for workflow in (
            update_dialog.UpdateDialog._load_data,
            update_dialog.UpdateDialog._load_dev_data,
        ):
            dialog, buttons = _make_dialog()
            workflow(dialog)
            assert not dialog._busy_operations
            assert any(button.enabled for button in buttons)
    finally:
        _FakeQueryOp.raise_on_run = False
        update_dialog.QueryOp = original_query_op


def test_success_callback_failures_release_busy_tokens():
    original_query_op = update_dialog.QueryOp
    update_dialog.QueryOp = _FakeQueryOp
    dialog, buttons = _make_dialog()
    dialog._populate_brrr_ui = lambda *args: (_ for _ in ()).throw(
        RuntimeError("UI failed")
    )
    dialog._populate_ui = lambda: None
    try:
        update_dialog.UpdateDialog._load_data(dialog)
        try:
            _FakeQueryOp.last.success(([], {}, None, None, []))
        except RuntimeError as exc:
            assert str(exc) == "UI failed"
        else:
            raise AssertionError("success callback should propagate the UI failure")

        assert not dialog._busy_operations
        assert any(button.enabled for button in buttons)

        dev_dialog, dev_buttons = _make_dialog()
        dev_dialog.source_combo = types.SimpleNamespace(
            currentData=lambda: (_ for _ in ()).throw(RuntimeError("UI failed"))
        )
        dev_dialog._populate_target = lambda source: None
        update_dialog.UpdateDialog._load_dev_data(dev_dialog)
        try:
            _FakeQueryOp.last.success(([], [], []))
        except RuntimeError as exc:
            assert str(exc) == "UI failed"
        else:
            raise AssertionError("success callback should propagate the UI failure")

        assert not dev_dialog._busy_operations
        assert any(button.enabled for button in dev_buttons)
    finally:
        update_dialog.QueryOp = original_query_op


def test_update_worker_failure_restores_controls():
    class MessageBox:
        class StandardButton:
            Yes = 1
            No = 2

        warnings = []

        @staticmethod
        def question(*args, **kwargs):
            return MessageBox.StandardButton.Yes

        @staticmethod
        def warning(*args):
            MessageBox.warnings.append(args)

        @staticmethod
        def information(*args):
            pass

    original_query_op = update_dialog.QueryOp
    original_message_box = update_dialog.QMessageBox
    update_dialog.QueryOp = _FakeQueryOp
    update_dialog.QMessageBox = MessageBox
    dialog, buttons = _make_dialog()
    try:
        update_dialog.UpdateDialog._run_update(dialog, lambda **kwargs: None, "test")
        assert all(button.enabled is False for button in buttons)

        _FakeQueryOp.last.fail(RuntimeError("download crashed"))

        assert not dialog._busy_operations
        assert any(button.enabled for button in buttons)
        assert dialog.progress_bar.value == 0
        assert MessageBox.warnings

        malformed_dialog, malformed_buttons = _make_dialog()
        update_dialog.UpdateDialog._run_update(
            malformed_dialog, lambda **kwargs: None, "test"
        )
        _FakeQueryOp.last.success(None)
        assert not malformed_dialog._busy_operations
        assert any(button.enabled for button in malformed_buttons)

        _FakeQueryOp.raise_on_run = True
        submission_dialog, submission_buttons = _make_dialog()
        update_dialog.UpdateDialog._run_update(
            submission_dialog, lambda **kwargs: None, "test"
        )
        assert not submission_dialog._busy_operations
        assert any(button.enabled for button in submission_buttons)
    finally:
        _FakeQueryOp.raise_on_run = False
        update_dialog.QueryOp = original_query_op
        update_dialog.QMessageBox = original_message_box


def test_sprite_workflows_share_busy_lifecycle(tmp_path):
    original_query_op = update_dialog.QueryOp
    original_mw = update_dialog.mw
    saved_modules = {
        name: sys.modules.get(name)
        for name in (
            "Ankimon",
            "Ankimon.pyobj",
            "Ankimon.pyobj.sprite_updater",
            "Ankimon.resources",
        )
    }
    update_dialog.QueryOp = _FakeQueryOp
    update_dialog.mw = types.SimpleNamespace(
        taskman=types.SimpleNamespace(run_on_main=lambda callback: callback())
    )
    try:
        for name, path in (
            ("Ankimon", _SRC / "Ankimon"),
            ("Ankimon.pyobj", _SRC / "Ankimon" / "pyobj"),
        ):
            package = types.ModuleType(name)
            package.__path__ = [str(path)]
            package.__package__ = name
            sys.modules[name] = package

        sprite_updater = types.ModuleType("Ankimon.pyobj.sprite_updater")
        sprite_updater.calculate_sprite_diff = lambda *args, **kwargs: None
        sprite_updater.SpriteUpdateDiffThread = _FakeSpriteThread
        sys.modules[sprite_updater.__name__] = sprite_updater

        resources = types.ModuleType("Ankimon.resources")
        resources.user_path_sprites = str(tmp_path / "ankimon-test-sprites")
        sys.modules[resources.__name__] = resources

        check_dialog, check_buttons = _make_dialog()
        update_dialog.UpdateDialog._check_sprites(check_dialog)
        assert all(button.enabled is False for button in check_buttons)

        _FakeQueryOp.last.fail(RuntimeError("sprite check crashed"))
        assert not check_dialog._busy_operations
        assert check_dialog.sprites_check_btn.enabled is True

        disabled_check_dialog, _buttons = _make_dialog()
        disabled_check_dialog._set_action_enabled(
            disabled_check_dialog.sprites_check_btn, False
        )
        update_dialog.UpdateDialog._check_sprites(disabled_check_dialog)
        _FakeQueryOp.last.fail(RuntimeError("sprite check crashed"))
        assert disabled_check_dialog.sprites_check_btn.enabled is False

        _FakeQueryOp.raise_on_run = True
        submission_dialog, submission_buttons = _make_dialog()
        update_dialog.UpdateDialog._check_sprites(submission_dialog)
        assert not submission_dialog._busy_operations
        assert any(button.enabled for button in submission_buttons)
        _FakeQueryOp.raise_on_run = False

        download_dialog, download_buttons = _make_dialog()
        download_dialog.sprites_added = []
        download_dialog.sprites_modified = []
        download_dialog.sprites_deleted = []
        download_dialog.sprites_remote_sha = "abc123"
        update_dialog.UpdateDialog._start_sprites_download(download_dialog)
        assert all(button.enabled is False for button in download_buttons)
        assert download_dialog.sprites_thread.started is True

        download_dialog.sprites_thread.finished_signal.emit(False, "simulated failure")
        assert download_dialog._busy_operations
        assert all(button.enabled is False for button in download_buttons)

        download_dialog.sprites_thread.running = False
        download_dialog.sprites_thread.finished.emit()
        assert not download_dialog._busy_operations
        assert download_dialog.sprites_check_btn.enabled is True
        assert download_dialog.sprites_update_btn.enabled is True
        assert download_dialog.sprites_status.text == "Update failed: simulated failure"

        crashed_dialog, crashed_buttons = _make_dialog()
        crashed_dialog.sprites_added = []
        crashed_dialog.sprites_modified = []
        crashed_dialog.sprites_deleted = []
        crashed_dialog.sprites_remote_sha = "abc123"
        update_dialog.UpdateDialog._start_sprites_download(crashed_dialog)
        assert all(button.enabled is False for button in crashed_buttons)
        crashed_thread = crashed_dialog.sprites_thread
        crashed_thread.running = False
        crashed_thread.finished.emit()
        assert not crashed_dialog._busy_operations
        assert "unexpectedly" in crashed_dialog.sprites_status.text
        crashed_thread.finished_signal.emit(False, "late result")
        assert "unexpectedly" in crashed_dialog.sprites_status.text

        replacement_dialog, replacement_buttons = _make_dialog()
        replacement_dialog.sprites_added = []
        replacement_dialog.sprites_modified = []
        replacement_dialog.sprites_deleted = []
        replacement_dialog.sprites_remote_sha = "abc123"
        update_dialog.UpdateDialog._start_sprites_download(replacement_dialog)
        old_thread = replacement_dialog.sprites_thread

        update_dialog.UpdateDialog._start_sprites_download(replacement_dialog)
        assert replacement_dialog.sprites_thread is old_thread
        assert len(replacement_dialog._busy_operations) == 1

        # The worker has stopped, but its queued QThread.finished callback has
        # not run yet, so a new operation can replace the stored reference.
        old_thread.running = False
        update_dialog.UpdateDialog._start_sprites_download(replacement_dialog)
        replacement_thread = replacement_dialog.sprites_thread

        old_thread.progress_signal.emit(88)
        old_thread.status_signal.emit("stale status")
        old_thread.finished_signal.emit(False, "stale result")
        old_thread.finished.emit()

        assert replacement_dialog.sprites_thread is replacement_thread
        assert len(replacement_dialog._busy_operations) == 1
        assert all(button.enabled is False for button in replacement_buttons)
        assert replacement_dialog.sprites_progress.value == 0
        assert "stale status" not in replacement_dialog.sprites_status.text
        assert "stale result" not in replacement_dialog.sprites_status.text

        replacement_thread.finished_signal.emit(False, "current result")
        replacement_thread.running = False
        replacement_thread.finished.emit()
        assert replacement_dialog.sprites_thread is None
        assert not replacement_dialog._busy_operations
        assert replacement_dialog.sprites_status.text == "Update failed: current result"

        closing_dialog, closing_buttons = _make_dialog()
        closing_dialog.sprites_added = []
        closing_dialog.sprites_modified = []
        closing_dialog.sprites_deleted = []
        closing_dialog.sprites_remote_sha = "abc123"
        close_requests = []
        closing_dialog.reject = lambda: close_requests.append(True)
        update_dialog.UpdateDialog._start_sprites_download(closing_dialog)
        closing_thread = closing_dialog.sprites_thread

        update_dialog.UpdateDialog.reject(closing_dialog)

        assert closing_thread.cancelled is True
        assert closing_dialog._busy_operations
        assert all(button.enabled is False for button in closing_buttons)

        closing_thread.status_signal.emit("Downloading after cancellation")
        closing_thread.progress_signal.emit(99)
        assert closing_dialog.sprites_status.text == "Cancelling sprite update..."
        assert closing_dialog.sprites_progress.value == 0

        closing_thread.finished_signal.emit(False, "Update cancelled.")
        assert closing_dialog._busy_operations
        closing_thread.running = False
        closing_thread.finished.emit()

        assert not closing_dialog._busy_operations
        assert closing_dialog.sprites_thread is None
        assert close_requests == [True]
        assert closing_dialog.sprites_status.text == "Cancelling sprite update..."

        late_dialog, _buttons = _make_dialog()
        late_dialog.sprites_added = []
        late_dialog.sprites_modified = []
        late_dialog.sprites_deleted = []
        late_dialog.sprites_remote_sha = "abc123"
        late_rejects = []
        late_dialog.reject = lambda: late_rejects.append(True)
        update_dialog.UpdateDialog._start_sprites_download(late_dialog)
        late_thread = late_dialog.sprites_thread
        late_dialog._closing = True
        late_dialog._close_finalized = True
        late_thread.running = False
        late_thread.finished.emit()
        assert late_rejects == []
    finally:
        _FakeQueryOp.raise_on_run = False
        update_dialog.QueryOp = original_query_op
        update_dialog.mw = original_mw
        for name, module in saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def test_branch_progress_malformed_result_uses_failure_path():
    class MessageBox:
        warnings = []

        @staticmethod
        def warning(*args):
            MessageBox.warnings.append(args)

        @staticmethod
        def information(*args):
            pass

    original_query_op = update_dialog.QueryOp
    original_message_box = update_dialog.QMessageBox
    saved_modules = {
        name: sys.modules.get(name)
        for name in ("Ankimon", "Ankimon.pyobj", "Ankimon.pyobj.update_manager")
    }
    update_dialog.QueryOp = _FakeQueryOp
    update_dialog.QMessageBox = MessageBox
    try:
        for name, path in (
            ("Ankimon", _SRC / "Ankimon"),
            ("Ankimon.pyobj", _SRC / "Ankimon" / "pyobj"),
        ):
            package = types.ModuleType(name)
            package.__path__ = [str(path)]
            package.__package__ = name
            sys.modules[name] = package

        manager = types.ModuleType("Ankimon.pyobj.update_manager")
        manager._download_branch_zip = lambda *args, **kwargs: None
        manager._download_zip_to_temp = lambda *args, **kwargs: None
        manager.apply_update = lambda *args, **kwargs: None
        manager.stamp_addon_mod = lambda *args, **kwargs: None
        sys.modules[manager.__name__] = manager

        dialog = types.SimpleNamespace(
            release=None,
            branch_name="main",
            remote_sha="abc123",
            on_progress=lambda *args: None,
            btn_close=_Control(False),
            status_label=_Label(),
            progress_bar=_Progress(),
        )
        update_dialog.BranchUpdateProgressDialog.start_update(dialog)
        _FakeQueryOp.last.success(None)

        assert dialog.btn_close.enabled is True
        assert dialog.progress_bar.value == 0
        assert "unexpectedly" in dialog.status_label.text
        assert MessageBox.warnings
    finally:
        update_dialog.QueryOp = original_query_op
        update_dialog.QMessageBox = original_message_box
        for name, module in saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


# --- meta.json is stamped on the main thread, never in the worker --------------
#
# apply_update runs in a QueryOp worker and returns the date to stamp rather than
# writing it, because meta.json is Anki's file and Anki read-modify-writes it from
# the main thread. QueryOp guarantees the success callback runs there, so that is
# where the write belongs. Both tests below round-trip bg -> on_done: a tuple-arity
# mismatch between the two would otherwise surface only at runtime, as a silent
# "Update failed unexpectedly" (on_done's unpack is inside a broad except).


class _StampSpy:
    """Stands in for stamp_addon_mod, recording the timestamps it is handed.

    An empty ``calls`` list is the assertion that matters in most of these
    tests: it means no meta.json write was attempted from that phase.
    """

    def __init__(self):
        self.calls = []

    def __call__(self, timestamp):
        self.calls.append(timestamp)
        return True


def test_release_update_stamps_meta_json_from_the_success_callback():
    class MessageBox:
        class StandardButton:
            Yes = 1
            No = 2

        @staticmethod
        def question(*args, **kwargs):
            return MessageBox.StandardButton.Yes

        @staticmethod
        def warning(*args):
            pass

        @staticmethod
        def information(*args):
            pass

    original_query_op = update_dialog.QueryOp
    original_message_box = update_dialog.QMessageBox
    original_apply = update_dialog.apply_update
    original_stamp = update_dialog.stamp_addon_mod
    spy = _StampSpy()
    update_dialog.QueryOp = _FakeQueryOp
    update_dialog.QMessageBox = MessageBox
    update_dialog.stamp_addon_mod = spy
    update_dialog.apply_update = lambda *a, **k: (
        True,
        "Update applied successfully.",
        1786186950,
    )
    dialog, _buttons = _make_dialog()
    dialog._on_progress = lambda *args: None
    try:
        update_dialog.UpdateDialog._run_update(
            dialog,
            lambda **kwargs: "/tmp/update.zip",
            "2.4",
            source_type="release",
            source_name="2.4",
            commit_sha="2.4",
            published_at="2026-08-08T11:02:30Z",
        )
        # Worker phase: it must not have stamped anything yet.
        result = _FakeQueryOp.last.op(None)
        assert spy.calls == []

        # Main-thread phase.
        _FakeQueryOp.last.success(result)
        assert spy.calls == [1786186950]
        assert dialog.progress_bar.value == 100
    finally:
        update_dialog.QueryOp = original_query_op
        update_dialog.QMessageBox = original_message_box
        update_dialog.apply_update = original_apply
        update_dialog.stamp_addon_mod = original_stamp


def test_release_update_does_not_stamp_when_the_install_failed():
    """The rollback guard. apply_update returns None on the rollback path, but
    the callback must refuse a stamp on any failure regardless — dating a
    restored old build as the new one would make Anki suppress the update that
    repairs it."""

    class MessageBox:
        class StandardButton:
            Yes = 1
            No = 2

        @staticmethod
        def question(*args, **kwargs):
            return MessageBox.StandardButton.Yes

        @staticmethod
        def warning(*args):
            pass

        @staticmethod
        def information(*args):
            pass

    original_query_op = update_dialog.QueryOp
    original_message_box = update_dialog.QMessageBox
    original_apply = update_dialog.apply_update
    original_stamp = update_dialog.stamp_addon_mod
    spy = _StampSpy()
    update_dialog.QueryOp = _FakeQueryOp
    update_dialog.QMessageBox = MessageBox
    update_dialog.stamp_addon_mod = spy
    # A timestamp alongside a failure: only the success flag may authorise a write.
    update_dialog.apply_update = lambda *a, **k: (
        False,
        "Update failed and was rolled back: boom",
        1786186950,
    )
    dialog, _buttons = _make_dialog()
    dialog._on_progress = lambda *args: None
    try:
        update_dialog.UpdateDialog._run_update(
            dialog, lambda **kwargs: "/tmp/update.zip", "2.4", source_type="release"
        )
        _FakeQueryOp.last.success(_FakeQueryOp.last.op(None))
        assert spy.calls == []
        assert dialog.progress_bar.value == 0
    finally:
        update_dialog.QueryOp = original_query_op
        update_dialog.QMessageBox = original_message_box
        update_dialog.apply_update = original_apply
        update_dialog.stamp_addon_mod = original_stamp


def test_branch_progress_dialog_also_stamps_on_the_main_thread():
    """The second call site. It imports from update_manager locally and unpacks
    its own tuple, so it can drift out of step with the release path."""

    class MessageBox:
        @staticmethod
        def warning(*args):
            pass

        @staticmethod
        def information(*args):
            pass

    original_query_op = update_dialog.QueryOp
    original_message_box = update_dialog.QMessageBox
    saved_modules = {
        name: sys.modules.get(name)
        for name in ("Ankimon", "Ankimon.pyobj", "Ankimon.pyobj.update_manager")
    }
    spy = _StampSpy()
    update_dialog.QueryOp = _FakeQueryOp
    update_dialog.QMessageBox = MessageBox
    try:
        for name, path in (
            ("Ankimon", _SRC / "Ankimon"),
            ("Ankimon.pyobj", _SRC / "Ankimon" / "pyobj"),
        ):
            package = types.ModuleType(name)
            package.__path__ = [str(path)]
            package.__package__ = name
            sys.modules[name] = package

        manager = types.ModuleType("Ankimon.pyobj.update_manager")
        manager._download_branch_zip = lambda *args, **kwargs: "/tmp/update.zip"
        manager._download_zip_to_temp = lambda *args, **kwargs: "/tmp/update.zip"
        manager.apply_update = lambda *a, **k: (True, "Update applied.", 1786186950)
        manager.stamp_addon_mod = spy
        sys.modules[manager.__name__] = manager

        dialog = types.SimpleNamespace(
            release=None,
            branch_name="main",
            remote_sha="abc123",
            on_progress=lambda *args: None,
            btn_close=_Control(False),
            status_label=_Label(),
            progress_bar=_Progress(),
        )
        update_dialog.BranchUpdateProgressDialog.start_update(dialog)

        result = _FakeQueryOp.last.op(None)
        assert spy.calls == []  # worker phase writes nothing

        _FakeQueryOp.last.success(result)
        assert spy.calls == [1786186950]
        assert dialog.progress_bar.value == 100
    finally:
        update_dialog.QueryOp = original_query_op
        update_dialog.QMessageBox = original_message_box
        for name, module in saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def test_branch_progress_dialog_does_not_stamp_when_the_install_failed():
    """The branch path's rollback guard, which is a separate copy from the
    release path's and can drift out of step with it."""

    class MessageBox:
        @staticmethod
        def warning(*args):
            pass

        @staticmethod
        def information(*args):
            pass

    original_query_op = update_dialog.QueryOp
    original_message_box = update_dialog.QMessageBox
    saved_modules = {
        name: sys.modules.get(name)
        for name in ("Ankimon", "Ankimon.pyobj", "Ankimon.pyobj.update_manager")
    }
    spy = _StampSpy()
    update_dialog.QueryOp = _FakeQueryOp
    update_dialog.QMessageBox = MessageBox
    try:
        for name, path in (
            ("Ankimon", _SRC / "Ankimon"),
            ("Ankimon.pyobj", _SRC / "Ankimon" / "pyobj"),
        ):
            package = types.ModuleType(name)
            package.__path__ = [str(path)]
            package.__package__ = name
            sys.modules[name] = package

        manager = types.ModuleType("Ankimon.pyobj.update_manager")
        manager._download_branch_zip = lambda *args, **kwargs: "/tmp/update.zip"
        manager._download_zip_to_temp = lambda *args, **kwargs: "/tmp/update.zip"
        # A timestamp alongside a failure: only the success flag may authorise a
        # write. Dating a rolled-back build as the new one would make Anki read
        # the restored old code as current and suppress the update that fixes it.
        manager.apply_update = lambda *a, **k: (
            False,
            "Update failed and was rolled back: boom",
            1786186950,
        )
        manager.stamp_addon_mod = spy
        sys.modules[manager.__name__] = manager

        dialog = types.SimpleNamespace(
            release=None,
            branch_name="main",
            remote_sha="abc123",
            on_progress=lambda *args: None,
            btn_close=_Control(False),
            status_label=_Label(),
            progress_bar=_Progress(),
        )
        update_dialog.BranchUpdateProgressDialog.start_update(dialog)
        _FakeQueryOp.last.success(_FakeQueryOp.last.op(None))

        assert spy.calls == []
        assert dialog.progress_bar.value == 0
    finally:
        update_dialog.QueryOp = original_query_op
        update_dialog.QMessageBox = original_message_box
        for name, module in saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
