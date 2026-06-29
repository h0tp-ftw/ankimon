"""
harness/scenarios/pokedex_render.py — open the REAL Pokedex (WebEngine) repeatedly
and measure memory. Reproduces user issue #4 ("lagspikes / memory when opening the
Pokedex and going past pages").

The Pokedex loads pokedex.html (all ~1300 Pokemon) into a real QWebEngineView. This
opens it once, then re-opens it (load_html + reload) N times, sampling RSS — the
per-open memory cost. Needs real WebEngine (CI, or this aarch64 Pi via
harness/setup_webengine.sh + the env below).

    source .tier2/env.sh
    export LD_LIBRARY_PATH=$PWD/.tier2/we-libs/extract/usr/lib/aarch64-linux-gnu:$LD_LIBRARY_PATH
    export ANKIMON_SPRITE_CACHE=$PWD/.tier2/sprites-cache
    python3 harness/scenarios/pokedex_render.py 80

Finding (this Pi, single-process Chromium): ~2.6 MB leaked per re-open, linear, no
plateau over 60 opens (326 -> 481 MB). Root not yet pinned (Ankimon page not freed
vs Chromium image cache vs single-process) — but it reproduces the reported symptom.
"""

import os
import sys

os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--no-sandbox --disable-gpu --disable-dev-shm-usage --in-process-gpu --single-process",
)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from PyQt6.QtWebEngineWidgets import QWebEngineView          # before QApplication
from PyQt6.QtCore import QTimer, QEventLoop


def _rss():
    for line in open("/proc/self/status"):
        if line.startswith("VmRSS"):
            return int(line.split()[1]) // 1024
    return 0


def _pump(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def run(n=80):
    from harness.real_env import start_real_session
    from harness.fixtures import seed_db
    from PyQt6.QtWidgets import QApplication

    h = start_real_session(first_run=True, webengine=True)
    seed_db({"main": {"species": "Gengar", "level": 50},
             "box": [{"species": s, "level": 10}
                     for s in ["Pikachu", "Bulbasaur", "Squirtle", "Eevee", "Snorlax",
                               "Mew", "Onix", "Magikarp", "Ditto", "Lapras"]]}, h.services.db)
    app = QApplication.instance()
    import Ankimon.singletons as S
    pd = S.pokedex_window

    if not pd.webview.__class__.__module__.startswith("PyQt6"):
        print("Pokedex is on the WebEngine STUB — run setup_webengine.sh + set the env "
              "(LD_LIBRARY_PATH, Chromium flags) so it uses real WebEngine. Aborting.")
        return

    done = {}
    pd.webview.loadFinished.connect(lambda ok: done.update(ok=ok))
    pd.load_html()
    _pump(5000)
    base = _rss()
    print("first Pokedex render: loadFinished=%s | baseline RSS %d MB" % (done.get("ok"), base))

    for i in range(1, n + 1):
        pd.load_html()
        pd.webview.reload()
        _pump(180)
        if i % max(1, n // 4) == 0 or i == n:
            print("  after %3d re-opens: RSS %d MB  (Δ +%d)" % (i, _rss(), _rss() - base))

    slope = (_rss() - base) / n
    print("\npokedex re-open memory: %d -> %d MB over %d opens (~%.2f MB/open)"
          % (base, _rss(), n, slope))
    print("VERDICT:", "LEAK — climbs without plateau" if slope > 0.5 else "bounded")


if __name__ == "__main__":
    run(int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 80)
