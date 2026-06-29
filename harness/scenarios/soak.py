"""
Memory soak: run N reviews and watch RSS for leaks / blow-ups.

Defaults to Tier 2 (the REAL Qt widgets) so it reflects genuine widget memory —
this is the "is there a crash/leak after 10k encounters?" test. It pumps the Qt
event loop periodically so deferred widget deletions (deleteLater) actually
process; otherwise RSS would grow simply because nothing gets collected, which
would be a false positive.

    source .tier2/env.sh
    python -m harness.scenarios.soak 10000           # Tier 2 (real widgets)
    python -m harness.scenarios.soak 20000 tier1     # Tier 1 (fast, fake windows)
"""

import sys
import gc
import time
import pathlib
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))


def _rss_mb():
    """Current resident set size in MB (Linux)."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except Exception:
        pass
    return -1.0


def run(n=10000, tier="real", sample=1000, verbose=True):
    overrides = {
        "battle.cards_per_round": 1,
        "battle.automatic_battle": 2,   # auto-resolve on faint -> encounters cycle
        "audio.sounds": False,
        "audio.sound_effects": False,
    }
    app = None
    if tier == "real":
        from harness.real_driver import RealDriver
        d = RealDriver(settings_overrides=overrides)
        app = d.env.app
    else:
        from harness.driver import Driver
        d = Driver(settings_overrides=overrides)

    kinds = Counter()
    gc.collect()
    if app:
        app.processEvents()
    rss0 = _rss_mb()
    t0 = time.time()
    rows = [(0, rss0)]

    for i in range(1, n + 1):
        for e in d.answer("good"):
            kinds[e["type"]] += 1
        # Pump the loop so Qt actually frees deleteLater'd widgets (real tier).
        if app and i % 50 == 0:
            app.processEvents()
        if i % sample == 0:
            if app:
                app.processEvents()
            gc.collect()
            rss = _rss_mb()
            rows.append((i, rss))
            if verbose:
                print(f"  {i:6d} reviews | RSS {rss:7.1f} MB (+{rss - rss0:6.1f}) "
                      f"| {i / (time.time() - t0):5.0f}/s | enc {kinds['encounter']} "
                      f"err {kinds['error']}", flush=True)

    dt = time.time() - t0
    rss_end = rows[-1][1]
    growth = rss_end - rss0
    result = {
        "tier": tier, "reviews": n, "seconds": round(dt, 1),
        "reviews_per_sec": round(n / dt),
        "rss_start_mb": round(rss0, 1), "rss_end_mb": round(rss_end, 1),
        "growth_mb": round(growth, 1), "mb_per_1k_reviews": round(growth / n * 1000, 3),
        "encounters": kinds["encounter"], "errors": kinds["error"],
    }
    if verbose:
        print(f"\nsoak ({tier}): {n} reviews in {dt:.1f}s ({n / dt:.0f}/s)")
        print(f"  RSS {rss0:.1f} -> {rss_end:.1f} MB  (growth {growth:+.1f} MB, "
              f"{growth / n * 1000:.3f} MB per 1000 reviews)")
        print(f"  encounters {kinds['encounter']}, errors {kinds['error']}")
    assert kinds["error"] == 0, f"{kinds['error']} error events during soak"
    return result


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10000
    tier = "fast" if (len(sys.argv) > 2 and sys.argv[2].startswith("tier1")) else "real"
    print("soak: OK", run(n, tier=tier))
