"""Real Qt regression for PR #778's update completion actions.

Run with the Tier-2 environment: python -m harness.scenarios.update_completion
Downloads and installation are controlled; no installed addon files are changed.
"""
from pathlib import Path
import tempfile
from threading import Thread
from unittest.mock import patch

from harness.real_driver import RealDriver


def main():
    driver = RealDriver(first_encounter=False)
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from Ankimon.pyobj import update_dialog as ud, update_manager as um

    app = driver.env.app
    queue = []
    screenshots = Path(tempfile.mkdtemp(prefix="ankimon-update-completion-"))

    def query(parent, op, success, failure):
        result = []

        def worker():
            try:
                result.append((True, op(None)))
            except Exception as exc:
                result.append((False, exc))

        thread = Thread(target=worker)
        thread.start()
        thread.join()
        while queue:
            queue.pop(0)()
        (success if result[0][0] else failure)(result[0][1])

    def download(*args, progress_cb=None):
        for n in (25, 50, 100):
            progress_cb(n, 100)
        return "controlled-archive.zip"

    def install(*args, status_cb=None, **kwargs):
        for n in (0, 20, 50, 95, 97, 99, 100):
            status_cb(f"__PROGRESS__{n}|100")
            status_cb("Installing files...")
        return True, "Installed", None

    with patch.object(ud, "_start_query_op", query), patch.object(
        driver.aqt.mw.taskman, "run_on_main", queue.append
    ), patch.object(um, "apply_update", install):
        for release in (None, {"name": "2.1", "zipball_url": "controlled"}):
            for outcome in ("success", "download_failure", "worker_failure"):
                with patch.object(um, "_download_branch_zip", side_effect=download) as branch, patch.object(
                    um, "_download_zip_to_temp", side_effect=download
                ) as archive:
                    if outcome != "success":
                        effect = (
                            (lambda *a, **k: None)
                            if outcome == "download_failure"
                            else RuntimeError("download failed")
                        )
                        branch.side_effect = archive.side_effect = effect
                    dialog = ud.BranchUpdateProgressDialog("main", "abcdef123", release=release)
                    values = []
                    dialog.progress_bar.valueChanged.connect(values.append)
                    assert not dialog.btn_close.isEnabled()
                    dialog.show()
                    app.processEvents()
                    assert dialog.btn_close.isEnabled()
                    if outcome == "success":
                        assert values == sorted(values), values
                        assert values[-1] == 100 and 40 in values and 99 in values
                        assert dialog.btn_close.text() == "Close Anki"
                        assert "reopen" in dialog.status_label.text()
                        assert not dialog.windowIcon().isNull()
                    else:
                        assert dialog.progress_bar.value() == 0
                        assert dialog.btn_close.text() == "Close"
                    channel = "release" if release else "branch"
                    assert dialog.grab().save(str(screenshots / f"{channel}-{outcome}.png"))
                    with patch.object(driver.aqt.mw, "close") as close_anki:
                        QTest.mouseClick(dialog.btn_close, Qt.MouseButton.LeftButton)
                        app.processEvents()
                        assert not dialog.isVisible()
                        assert close_anki.call_count == int(outcome == "success")
                    print(f"PASS {channel} {outcome}: progress={values}")
    print(f"Screenshots: {screenshots}")
    print("update_completion: OK")


if __name__ == "__main__":
    main()
