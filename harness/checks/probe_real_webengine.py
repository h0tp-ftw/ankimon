"""Tier-2 WebEngine probe: exercise the real Chromium-backed Settings shell.

This probe is intentionally stricter than the normal Tier-2 boot/play checks:
PyQt6-WebEngine must be importable and the harness must construct genuine
QWebEngineView instances. It then loads the real Settings HTML/JavaScript,
changes Trainer Name through the DOM, clicks Save, and verifies that the live
SQLite-backed Settings service received the change.

Run with a display wrapper on Linux:
    xvfb-run -a python3 -m harness.checks.probe_real_webengine
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time

# WebEngine must be configured and imported before QApplication is constructed.
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--no-sandbox --disable-gpu --disable-dev-shm-usage",
)

from PyQt6.QtCore import QEventLoop, QTimer
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QApplication

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from harness.real_env import start_real_session


def _wait_until(predicate, timeout_seconds=15.0):
    deadline = time.monotonic() + timeout_seconds
    app = QApplication.instance()
    while time.monotonic() < deadline:
        if predicate():
            return True
        if app is not None:
            app.processEvents()
        time.sleep(0.02)
    return bool(predicate())


def _run_javascript(page, script, timeout_ms=10_000):
    result = {}
    loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)

    def finish(value):
        result["done"] = True
        result["value"] = value
        loop.quit()

    timer.timeout.connect(loop.quit)
    timer.start(timeout_ms)
    page.runJavaScript(script, finish)
    loop.exec()
    timer.stop()

    if not result.get("done"):
        raise AssertionError("Timed out waiting for QWebEngine JavaScript callback")
    return result.get("value")


def _edit_and_save(page, key, value):
    """Edit one rendered Settings input and click the real Save button."""
    return _run_javascript(
        page,
        f"""
        (() => {{
            const row = document.querySelector(
                '.setting-row[data-key=' + {json.dumps(json.dumps(key))} + ']'
            );
            const input = row && row.querySelector('input');
            const save = document.getElementById('save-btn');
            if (!input || !save) return {{ok: false}};
            input.value = {json.dumps(value)};
            input.dispatchEvent(new Event('input', {{bubbles: true}}));
            const becameDirty = !save.disabled;
            save.click();
            return {{ok: true, becameDirty}};
        }})()
        """,
    )


def _wait_for_saved(page):
    return _wait_until(
        lambda: _run_javascript(
            page, "document.getElementById('save-status')?.textContent"
        ) == "All saved"
    )


def main() -> int:
    session = start_real_session(webengine=True, require_webengine=True)

    from Ankimon.ankimon_items_web.shop_obj import SCREEN_SETTINGS
    from Ankimon.singletons import get_items_window

    original_secret = "webengine-original-api-secret"
    session.services.settings.set("leaderboard.username", "WebEngineUser")
    session.services.settings.set("leaderboard.api_key", original_secret)

    window = get_items_window()
    view = window.webview_settings
    assert isinstance(view, QWebEngineView), type(view)
    assert type(view).__module__.startswith("PyQt6.QtWebEngine"), type(view).__module__

    window.load_screen(SCREEN_SETTINGS)
    window.show()

    assert _wait_until(lambda: SCREEN_SETTINGS in window.ready_screens), (
        "The real Settings page did not emit loadFinished"
    )

    page = view.page()
    assert _wait_until(
        lambda: bool(
            _run_javascript(
                page,
                "Boolean(document.querySelector('.setting-row[data-key=\"trainer.name\"] input'))",
            )
        )
    ), "Settings JavaScript loaded, but the settings rows were never initialized"

    page_state = _run_javascript(
        page,
        """
        (() => {
            const row = document.querySelector('.setting-row[data-key="trainer.name"]');
            const input = row && row.querySelector('input');
            return {
                title: document.title,
                rowCount: document.querySelectorAll('.setting-row').length,
                hasQtTransport: Boolean(window.qt && qt.webChannelTransport),
                hasInput: Boolean(input),
                initialValue: input ? input.value : null,
            };
        })()
        """,
    )
    assert page_state["title"] == "Ankimon Settings", page_state
    assert page_state["rowCount"] > 0, page_state
    assert page_state["hasQtTransport"] is True, page_state
    assert page_state["hasInput"] is True, page_state

    credential_state = _run_javascript(
        page,
        f"""
        (() => {{
            const row = document.querySelector(
                '.setting-row[data-key="leaderboard.api_key"]'
            );
            const input = row && row.querySelector('input');
            const html = document.documentElement.innerHTML;
            const inputValues = Array.from(document.querySelectorAll('input'))
                .map((item) => item.value);
            return {{
                hasRow: Boolean(row),
                type: input ? input.type : null,
                value: input ? input.value : null,
                placeholder: input ? input.placeholder : null,
                secretInHtml: html.includes({json.dumps(original_secret)}),
                secretInInputs: inputValues.some(
                    (value) => value.includes({json.dumps(original_secret)})
                ),
            }};
        }})()
        """,
    )
    assert credential_state == {
        "hasRow": True,
        "type": "password",
        "value": "********",
        "placeholder": "API key saved — type to replace it",
        "secretInHtml": False,
        "secretInInputs": False,
    }, credential_state

    # Saving an unrelated field leaves the masked API-key placeholder untouched.
    expected_name = "WebEngine CI Trainer"
    edit_state = _edit_and_save(page, "trainer.name", expected_name)
    assert edit_state == {"ok": True, "becameDirty": True}, edit_state

    assert _wait_until(
        lambda: session.services.settings.get("trainer.name") == expected_name
    ), "Clicking Save in the real Settings web page did not persist Trainer Name"
    assert _wait_for_saved(page), (
        "Settings persisted, but the browser never completed its saved-state refresh"
    )
    assert session.services.settings.get("leaderboard.api_key") == original_secret

    saved_state = _run_javascript(
        page,
        """
        (() => {
            const row = document.querySelector('.setting-row[data-key="trainer.name"]');
            const input = row && row.querySelector('input');
            return {
                value: input ? input.value : null,
                status: document.getElementById('save-status')?.textContent,
                saveDisabled: document.getElementById('save-btn')?.disabled,
            };
        })()
        """,
    )
    assert saved_state == {
        "value": expected_name,
        "status": "All saved",
        "saveDisabled": True,
    }, saved_state

    replacement_secret = "webengine-replacement-api-secret"
    replace_state = _edit_and_save(
        page, "leaderboard.api_key", replacement_secret
    )
    assert replace_state == {"ok": True, "becameDirty": True}, replace_state
    assert _wait_until(
        lambda: session.services.settings.get("leaderboard.api_key")
        == replacement_secret
    ), "Replacing the API key through the real browser did not persist"
    assert _wait_for_saved(page), "API-key replacement never completed its UI refresh"

    replaced_dom = _run_javascript(
        page,
        f"""
        (() => {{
            const input = document.querySelector(
                '.setting-row[data-key="leaderboard.api_key"] input'
            );
            return {{
                value: input ? input.value : null,
                secretInHtml: document.documentElement.innerHTML.includes(
                    {json.dumps(replacement_secret)}
                ),
            }};
        }})()
        """,
    )
    assert replaced_dom == {
        "value": "********",
        "secretInHtml": False,
    }, replaced_dom

    clear_state = _edit_and_save(page, "leaderboard.api_key", "")
    assert clear_state == {"ok": True, "becameDirty": True}, clear_state
    assert _wait_until(
        lambda: session.services.settings.get("leaderboard.api_key") == ""
    ), "Clearing the API key through the real browser did not persist"
    assert _wait_for_saved(page), "API-key clearing never completed its UI refresh"

    cleared_dom = _run_javascript(
        page,
        """
        (() => {
            const input = document.querySelector(
                '.setting-row[data-key="leaderboard.api_key"] input'
            );
            return {
                value: input ? input.value : null,
                placeholder: input ? input.placeholder : null,
            };
        })()
        """,
    )
    assert cleared_dom == {
        "value": "",
        "placeholder": "Enter your API key",
    }, cleared_dom

    print(
        "probe_real_webengine: OK "
        f"({page_state['rowCount']} settings rows, real QWebEngine, "
        "DOM save + API-key lifecycle persisted)"
    )
    window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
