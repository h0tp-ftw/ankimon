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

    def isEnabled(self):
        return self.enabled

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
