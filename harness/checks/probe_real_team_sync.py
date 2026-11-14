"""Tier-2 WebEngine probe: the Team screen and the Pokémon PC edit the same party.

Reviewer scenario on PR #950: with both open, a party edit in the PC must not be
undone by a later Save on the Team screen. Drives the real Chromium-backed Team
page and the real team seam (the same calls the PC's menu/drag actions make):

1. clean view: a PC edit is pushed into the page, which reloads itself;
2. unsaved draft: the draft is kept and the page says the party moved on;
3. stale save: Save on that draft is refused and the stored party keeps the
   PC's edit;
4. reload: re-pushing the screen data clears the stale state.

Run with a display wrapper on Linux:
    xvfb-run -a python3 -m harness.checks.probe_real_team_sync
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time

os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--no-sandbox --disable-gpu --disable-dev-shm-usage",
)

from PyQt6.QtCore import QEventLoop, QTimer
from PyQt6.QtWidgets import QApplication

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from harness.real_env import start_real_session

SEED = {
    "main": {"species": "Gengar", "level": 50},
    "box": [
        {"species": s, "level": 10 + i}
        for i, s in enumerate(["Pikachu", "Bulbasaur", "Squirtle", "Eevee"])
    ],
    "items": {"Pokeball": 5},
}


def _wait_until(predicate, timeout_seconds=15.0):
    """Pump the event loop until ``predicate()`` is true or the timeout passes."""
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
    """Run ``script`` in the page and return its value synchronously."""
    result = {}
    loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)

    def finish(value):
        """Store the callback value and stop the local loop."""
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


def _team_state(page):
    """The page's own snapshot of its team state (see team.js getTeamState)."""
    return _run_javascript(page, "window.getTeamState ? window.getTeamState() : null")


def main() -> int:
    """Run the four-step scenario; raise on the first mismatch."""
    session = start_real_session(webengine=True, require_webengine=True)
    from harness.fixtures import seed_db
    from Ankimon.ankimon_items_web.shop_obj import SCREEN_TEAM
    from Ankimon.functions import team_functions
    from Ankimon.singletons import get_items_window

    db = session.services.db
    seed_db(SEED, db)
    boxed = [r[0] for r in db.execute(
        "SELECT individual_id FROM captured_pokemon WHERE is_main = 0 ORDER BY rowid"
    ).fetchall()]
    first, second, third = boxed[:3]
    assert team_functions.add_to_party(db, first).ok

    def team():
        """Ordered ids in the team table."""
        return [r["individual_id"] for r in db.get_team()]

    base = team()  # the seed may already hold the main Pokémon in slot 1
    assert first in base, base

    window = get_items_window()
    assert session.services.items_web is window, "services.items_web must point at the shell"
    window.load_screen(SCREEN_TEAM)
    window.show()
    assert _wait_until(lambda: SCREEN_TEAM in window.ready_screens), "Team page never loaded"
    page = window.webview_team.page()
    assert _wait_until(lambda: (_team_state(page) or {}).get("team") == base), _team_state(page)

    # 1. Clean view: a PC edit is pushed and the page reloads itself.
    assert team_functions.add_to_party(db, second).ok
    assert _wait_until(lambda: (_team_state(page) or {}).get("team") == base + [second]), _team_state(page)
    state = _team_state(page)
    assert state["dirty"] is False and state["changedElsewhere"] is False, state
    assert state["loadedTeam"] == base + [second], state

    # 2. Unsaved draft: remove slot 1 on the page (dirty), then the PC adds a third.
    assert _run_javascript(page, """
        (() => { const b = document.querySelector('.slot-remove'); if (!b) return false; b.click(); return true; })()
    """), "no remove button on the Team page"
    assert _wait_until(lambda: (_team_state(page) or {}).get("dirty") is True), _team_state(page)
    assert team_functions.add_to_party(db, third).ok
    assert _wait_until(lambda: (_team_state(page) or {}).get("changedElsewhere") is True), _team_state(page)
    state = _team_state(page)
    assert state["dirty"] is True and third not in state["team"], state  # draft kept, not overwritten
    assert "changed elsewhere" in state["status"], state

    # 3. Stale save: Save on the draft is refused, the stored party keeps the PC's edit.
    _run_javascript(page, "document.getElementById('save-btn').click(); true")
    assert _wait_until(lambda: "changed elsewhere" in (_run_javascript(
        page, "document.getElementById('toast').textContent") or "")), "no stale-save toast"
    assert team() == base + [second, third], team()
    state = _team_state(page)
    assert state["dirty"] is True and state["changedElsewhere"] is True, state

    # 3b. Double-click Save on a fresh draft: one save, no false "changed elsewhere".
    window.push_screen_data()
    assert _wait_until(lambda: (_team_state(page) or {}).get("dirty") is False), _team_state(page)
    _run_javascript(page, "document.querySelector('.slot-remove').click(); true")
    assert _wait_until(lambda: (_team_state(page) or {}).get("dirty") is True), _team_state(page)
    _run_javascript(page, "(() => { const b = document.getElementById('save-btn'); b.click(); b.click(); })(); true")
    assert _wait_until(lambda: (_team_state(page) or {}).get("dirty") is False), _team_state(page)
    state = _team_state(page)
    assert state["changedElsewhere"] is False and state["savePending"] is False, state
    assert state["loadedTeam"] == team(), (state, team())  # baseline = what the backend stored

    # 3c. Open picker stays current: the PC adds a Pokémon while it is open.
    _run_javascript(page, "(() => { const s = document.querySelector('.slot.empty'); if (s) s.click(); })(); true")
    assert _wait_until(lambda: (_team_state(page) or {}).get("pickerOpen") is True), _team_state(page)
    roster_cards = "document.querySelectorAll('#roster-list .roster-card').length"
    assert _wait_until(lambda: (_run_javascript(page, roster_cards) or 0) > 0), "roster never loaded"
    newcomer = next(i for i in boxed if i not in team())
    assert team_functions.add_to_party(db, newcomer).ok
    newcomer_name = (db.get_pokemon(newcomer) or {}).get("name", "").lower()
    assert _wait_until(lambda: any(newcomer_name in n.lower() for n in (_team_state(page) or {}).get("pickerOnTeam", []))), (
        "picker did not refresh: %s" % _team_state(page))
    _run_javascript(page, "document.getElementById('picker').classList.add('hidden'); true")

    # 4. Reload the page data: the stale state clears and the stored party shows.
    window.push_screen_data()
    assert _wait_until(lambda: (_team_state(page) or {}).get("team") == team()), _team_state(page)
    state = _team_state(page)
    assert state["dirty"] is False and state["changedElsewhere"] is False, state
    assert state["status"] == "All saved", state

    print("probe_real_team_sync: OK —", json.dumps({
        "clean_refresh": True, "draft_kept": True, "stale_save_refused": True,
        "double_save_single": True, "picker_refreshes": True, "reload_clears": True,
    }), flush=True)
    # Same exit path as probe_real_webengine: close the shell so its pages are
    # released before the QWebEngine profile goes away at interpreter exit.
    window.close()
    QApplication.instance().processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
