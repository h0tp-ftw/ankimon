"""
harness/diagnostics.py — DEV-ONLY profiling for a headless workload.

Wrap any sequence of Driver actions and get a machine-readable report:
  - DB queries: total + grouped by normalized statement (spots N+1s / rescans)
  - a profiler backend's hotspots (stdlib cProfile by default; pyinstrument optional)
  - memory: process RSS delta + optional tracemalloc top allocators (leak hunting)
  - wall time

The **query counts** and the profiler **shape** are hardware-independent — they tell
you WHERE the cost is and HOW it scales, which is what you actually fix. Wall-time and
RSS are *indicative on this box*, not a substitute for measuring on real hardware.

Core is stdlib only. The profiler backend is **pluggable** so a dev can point any
tool at the same workload — see harness/requirements-dev.txt (and harness/debug.py
for stepping through with pdb/debugpy). Dev-only: lives in harness/, never shipped.

    from harness.driver import Driver
    from harness.diagnostics import profile

    d = Driver(settings_overrides={"battle.cards_per_round": 1})
    with profile(d, label="10k battles", memory=True) as report:   # backend="cprofile"
        for _ in range(10_000):
            d.answer("good")
            if d.services.enemy_pokemon.hp <= 0:
                d.catch()
    report.print()                       # human-readable; report.as_dict() for assertions

    # swap the engine — same workload, different tool (needs `pip install pyinstrument`):
    with profile(d, backend="pyinstrument") as report: ...
"""

from __future__ import annotations

import gc
import io
import os
import re
import time
import tracemalloc
from contextlib import contextmanager

_NORM = [
    (re.compile(r"'[^']*'"), "?"),
    (re.compile(r"\b\d+\b"), "?"),
    (re.compile(r"\s+"), " "),
]


def _normalize(sql: str) -> str:
    s = sql.strip()
    for rx, rep in _NORM:
        s = rx.sub(rep, s)
    return s[:160]


def _rss_mb() -> float:
    try:
        with open("/proc/self/statm") as f:
            resident_pages = int(f.read().split()[1])
        return resident_pages * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024)
    except Exception:
        try:
            import resource
            return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
        except Exception:
            return 0.0


class Report:
    def __init__(self, label: str = "run", backend: str = "cprofile"):
        self.label = label
        self.backend = backend
        self.wall_seconds = 0.0
        self.queries: dict[str, int] = {}
        self.query_total = 0
        self.rss_start = 0.0
        self.rss_end = 0.0
        self.tracemalloc_top: list[tuple[str, float, int]] = []
        self.cprofile_top: list[tuple[str, int, float, float]] = []  # cProfile backend
        self.profile_text: str = ""  # text-tree backends (e.g. pyinstrument)

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "backend": self.backend,
            "wall_seconds": round(self.wall_seconds, 3),
            "db": {
                "total_queries": self.query_total,
                "distinct_statements": len(self.queries),
                "by_statement": sorted(self.queries.items(), key=lambda kv: -kv[1])[:15],
            },
            "memory": {
                "rss_start_mb": round(self.rss_start, 1),
                "rss_end_mb": round(self.rss_end, 1),
                "rss_delta_mb": round(self.rss_end - self.rss_start, 1),
                "tracemalloc_top": self.tracemalloc_top,
            },
            "cprofile_top": self.cprofile_top,
            "profile_text": self.profile_text,
        }

    def print(self) -> None:
        d = self.as_dict()
        print(f"\n=== diagnostics: {d['label']}  (backend={d['backend']}) ===")
        print(f"wall: {d['wall_seconds']}s  (includes profiler overhead)")
        db = d["db"]
        print(f"\nDB queries: {db['total_queries']} total, {db['distinct_statements']} distinct")
        for stmt, n in db["by_statement"]:
            print(f"  {n:>8}  {stmt}")
        m = d["memory"]
        print(f"\nRSS: {m['rss_start_mb']} -> {m['rss_end_mb']} MB  (delta {m['rss_delta_mb']:+} MB)")
        for loc, kb, cnt in m["tracemalloc_top"]:
            print(f"  +{kb:>9.1f} KB  ({cnt:+} objs)  {loc}")
        if d["profile_text"]:
            print(f"\n{d['backend']}:")
            print(d["profile_text"])
        elif d["cprofile_top"]:
            print("\ncProfile (top by cumulative time):")
            for func, ncalls, tot, cum in d["cprofile_top"]:
                print(f"  cum={cum:7.3f}s  tot={tot:7.3f}s  calls={ncalls:>9}  {func}")
        print()


# --- pluggable profiler backends --------------------------------------------
# Each backend is (start() -> handle, stop(handle, report, top)). Add one here and
# it's usable as profile(..., backend="name"). DB-query/memory/wall are captured
# around the backend, so every tool gets them for free.

def _start_cprofile():
    import cProfile
    pr = cProfile.Profile()
    pr.enable()
    return pr


def _stop_cprofile(pr, rep, top):
    import pstats
    pr.disable()
    ps = pstats.Stats(pr, stream=io.StringIO())
    rows = [
        (f"{os.path.basename(fn[0])}:{fn[1]}({fn[2]})", nc, tt, ct)
        for fn, (cc, nc, tt, ct, callers) in ps.stats.items()
    ]
    rows.sort(key=lambda r: -r[3])
    rep.cprofile_top = rows[:top]


def _start_pyinstrument():
    from pyinstrument import Profiler  # optional: pip install pyinstrument
    pr = Profiler()
    pr.start()
    return pr


def _stop_pyinstrument(pr, rep, top):
    pr.stop()
    rep.profile_text = pr.output_text(unicode=False, color=False, show_all=False)


_BACKENDS = {
    "cprofile": (_start_cprofile, _stop_cprofile),
    "pyinstrument": (_start_pyinstrument, _stop_pyinstrument),
    "none": (lambda: None, lambda h, rep, top: None),
}


@contextmanager
def profile(driver, label: str = "run", memory: bool = False, backend: str = "cprofile", top: int = 15):
    """Profile the workload run inside the ``with`` block. Yields a Report.

    backend: "cprofile" (default, stdlib), "pyinstrument" (pip), or "none".
             An unknown/uninstalled backend falls back to cProfile with a note.
    memory=True adds tracemalloc top-allocator tracking (heavier; great for leaks).
    """
    if backend not in _BACKENDS:
        print(f"diagnostics: unknown backend {backend!r}; using cprofile")
        backend = "cprofile"
    start, stop = _BACKENDS[backend]
    rep = Report(label, backend)

    # DB query counting via the sqlite trace callback on the live connection.
    conn = None
    try:
        conn = driver.services.db._get_connection()
    except Exception:
        conn = None

    def _trace(sql):
        key = _normalize(sql)
        rep.queries[key] = rep.queries.get(key, 0) + 1
        rep.query_total += 1

    if conn is not None:
        try:
            conn.set_trace_callback(_trace)
        except Exception:
            conn = None

    gc.collect()
    rep.rss_start = _rss_mb()
    snap0 = None
    if memory:
        tracemalloc.start(20)
        snap0 = tracemalloc.take_snapshot()

    # Start the profiler backend (fall back to cprofile if its package is missing).
    try:
        handle = start()
    except Exception as e:
        print(f"diagnostics: backend {backend!r} unavailable ({e}); using cprofile")
        backend, (start, stop) = "cprofile", _BACKENDS["cprofile"]
        rep.backend = "cprofile"
        handle = start()

    t0 = time.perf_counter()
    try:
        yield rep
    finally:
        rep.wall_seconds = time.perf_counter() - t0
        try:
            stop(handle, rep, top)
        except Exception as e:
            print(f"diagnostics: backend {backend!r} stop failed: {e}")

        if memory and snap0 is not None:
            snap1 = tracemalloc.take_snapshot()
            for st in snap1.compare_to(snap0, "lineno")[:top]:
                rep.tracemalloc_top.append((str(st.traceback), st.size_diff / 1024.0, st.count_diff))
            tracemalloc.stop()
        gc.collect()
        rep.rss_end = _rss_mb()

        if conn is not None:
            try:
                conn.set_trace_callback(None)
            except Exception:
                pass
