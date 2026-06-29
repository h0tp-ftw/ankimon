"""
harness/scenarios/hud_render.py — RENDER the reviewer HUD overlay in real WebEngine
and measure its memory over many refreshes.

The in-card HUD is HTML/CSS/JS the add-on injects into Anki's reviewer webview.
The harness captures the exact JS it would inject (self-contained — sprite inlined
as base64), so this loads it into a REAL QWebEngineView, renders it, and re-injects
it N times measuring RSS — the leak-hunt for the per-card overlay (it repaints every
single review, so a leak there compounds fastest of all).

Needs WebEngine. On CI (x86) it's built in. On this aarch64 Pi, the native deps
were fetched sudo-free into .tier2/we-libs; run with:

    source .tier2/env.sh
    export LD_LIBRARY_PATH=$PWD/.tier2/we-libs/extract/usr/lib/aarch64-linux-gnu:$LD_LIBRARY_PATH
    python3 harness/scenarios/hud_render.py 200
"""

import os
import sys

# Headless Chromium flags BEFORE importing WebEngine.
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--no-sandbox --disable-gpu --disable-dev-shm-usage --in-process-gpu "
    "--disable-software-rasterizer --single-process",
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# WebEngine must be imported before any QApplication exists.
from PyQt6.QtWebEngineWidgets import QWebEngineView          # noqa: E402
from PyQt6.QtCore import QTimer, QEventLoop                  # noqa: E402


def _capture_hud(h):
    """One HUD refresh -> (portal_setup_js, hud_content_js)."""
    calls = []
    web = h.aqt.mw.reviewer.web
    web.eval = lambda js, *a, **k: calls.append(js)
    web.evalWithCallback = lambda js, cb=None, *a, **k: (calls.append(js), cb(None) if cb else None)
    h.services.reviewer.refresh_hud()
    if not calls:
        return "", ""
    content = max(calls, key=len)
    setup = "\n".join(c for c in calls if c is not content)
    return setup, content


def _rss():
    for line in open("/proc/self/status"):
        if line.startswith("VmRSS"):
            return int(line.split()[1]) // 1024
    return 0


def _pump(app, ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def run(n=200):
    from harness.real_env import start_real_session
    from PyQt6.QtWidgets import QApplication

    h = start_real_session(first_run=True)
    app = QApplication.instance()

    setup, content = _capture_hud(h)
    print("captured HUD: setup %d chars, content %d chars | self-contained base64: %s"
          % (len(setup), len(content), "base64," in content))
    if not content:
        print("no HUD content captured — aborting"); return

    # Real WebEngine view; load a blank page, then inject the real portal + HUD.
    view = QWebEngineView()
    view.resize(520, 340)
    q = QEventLoop()
    view.loadFinished.connect(lambda ok: q.quit())
    view.setHtml("<html><body style='margin:0;background:#111'></body></html>")
    QTimer.singleShot(30000, q.quit)
    q.exec()

    page = view.page()
    page.runJavaScript(setup)
    page.runJavaScript(content)
    _pump(app, 1500)

    shot = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".tier2", "hud.png"))
    try:
        view.grab().save(shot)
        print("rendered HUD -> screenshot:", shot, "(WebEngine grabs may be blank offscreen)")
    except Exception as e:
        print("screenshot skipped:", e)

    # Leak hunt: re-inject a fresh HUD N times, watch RSS.
    import gc
    gc.collect()
    base = _rss()
    print("baseline RSS after first render: %d MB" % base)
    for i in range(1, n + 1):
        _, content = _capture_hud(h)            # fresh HUD markup each "review"
        page.runJavaScript(content)
        _pump(app, 15)
        if i in (50, 100, 200, n):
            gc.collect()
            print("  after %3d HUD renders: RSS %d MB  (Δ +%d)" % (i, _rss(), _rss() - base))


if __name__ == "__main__":
    run(int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 200)
