#!/usr/bin/env python3
"""
harness/check.py — ONE command to verify the headless harness (Tier 1).

Auto-discovers every fast, zero-dep Tier-1 check (the `probe_*` import/boot probes,
a smoke play-through, and the headless regression test), runs each in an isolated
subprocess, and prints a single PASS/FAIL summary. **Exit code 0 = all green**, so
CI and AI agents can gate on it with no extra wiring.

    python3 harness/check.py            # run the full Tier-1 gate (~seconds)
    python3 harness/check.py --doctor   # diagnose the dev environment

No Anki, no Qt, no pip deps — only `python3` (3.10+) and the `poke_engine` submodule.
Tier-2 (real-Qt) probes (`probe_real_*`) are intentionally excluded; build that env
with `harness/setup_tier2.sh` and run them separately. New `probe_*.py` files are
picked up automatically — drop one in and it joins the gate.
"""

from __future__ import annotations

import glob
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def discover():
    """The Tier-1 gate: all non-Qt probes + a smoke run + the regression test."""
    items = []
    for p in sorted(glob.glob(os.path.join(ROOT, "harness", "checks", "probe_*.py"))):
        if os.path.basename(p).startswith("probe_real_"):
            continue  # Tier 2 — needs the offscreen-Qt env
        items.append(os.path.relpath(p, ROOT))
    for extra in ("harness/scenarios/smoke_play.py", "tests/test_headless_harness.py"):
        if os.path.exists(os.path.join(ROOT, extra)):
            items.append(extra)
    return items


def doctor():
    ok = True
    print("harness doctor:")
    v = sys.version_info
    py_ok = v >= (3, 10)
    print(f"  [{'OK' if py_ok else '!!'}] python {v.major}.{v.minor}  (need >= 3.10)")
    ok &= py_ok
    sub = os.path.join(ROOT, "src", "Ankimon", "poke_engine", "objects.py")
    sub_ok = os.path.exists(sub)
    msg = "present" if sub_ok else "MISSING -> run: git submodule update --init --recursive"
    print(f"  [{'OK' if sub_ok else '!!'}] poke_engine submodule  {msg}")
    ok &= sub_ok
    print("  =>", "ready: run `python3 harness/check.py`" if ok else "fix the items above first")
    return 0 if ok else 1


def main(argv):
    if "--doctor" in argv:
        return doctor()

    checks = discover()
    if not checks:
        print("check: no Tier-1 checks found (is the harness intact?)")
        return 1

    print(f"harness check: {len(checks)} Tier-1 checks (no Anki/Qt)\n")
    results = []
    for rel in checks:
        t0 = time.perf_counter()
        try:
            proc = subprocess.run(
                [sys.executable, rel], cwd=ROOT,
                capture_output=True, text=True, timeout=240,
            )
            rc, out, err = proc.returncode, proc.stdout or "", proc.stderr or ""
        except subprocess.TimeoutExpired:
            rc, out, err = 124, "", "TIMEOUT (>240s)"
        dt = time.perf_counter() - t0
        ok = rc == 0
        tail = next((ln for ln in reversed((out).strip().splitlines()) if ln.strip()), "")
        results.append((ok, rel, dt, tail, rc, out, err))
        print(f"  [{'PASS' if ok else 'FAIL'}] {rel:44} {dt:5.1f}s  {tail[:70]}")

    failed = [r for r in results if not r[0]]
    print()
    if failed:
        print(f"FAIL — {len(failed)}/{len(results)} checks failed:\n")
        for ok, rel, dt, tail, rc, out, err in failed:
            print(f"----- {rel}  (exit {rc}) -----")
            print((out or "")[-1800:])
            if err.strip():
                print("[stderr]", (err or "")[-1200:])
            print()
        return 1
    print(f"PASS — all {len(results)} Tier-1 checks green.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
