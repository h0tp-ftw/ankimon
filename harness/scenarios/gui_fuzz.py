"""
harness/scenarios/gui_fuzz.py — isolated, REPRODUCIBLE GUI monkey-fuzzer (Tier 2).

Random GUI actions on the real windows: click a random visible button, type a
weird string into a random field, or trigger a random menu item — wandering
wherever the GUI leads. The catch (learned the hard way): a bad GUI action can
HARD-crash the process (a C++ Qt abort, uncatchable in Python). So this is built
for reproducibility:

  * Every action is journaled to disk and FLUSHED *before* it runs — so if the
    process dies, the journal's last line is exactly the action that killed it.
  * Each seed runs in its own CHILD process — a hard crash kills the child, not
    the sweep; the parent reads the journal and reports the culprit + how to replay.

    source .tier2/env.sh
    python3 harness/scenarios/gui_fuzz.py                  # parent: sweep seeds, report crashers
    python3 harness/scenarios/gui_fuzz.py --replay 7 40    # re-run one seed, verbose, to watch it
    # (--run SEED STEPS JOURNAL is the internal child entrypoint)
"""

import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

WEIRD = ["", "💀", "Ž★🔥", "x" * 60, "'; DROP TABLE x;--", "999", "../../etc/passwd"]
SEED_TEAM = {"main": {"species": "Gengar", "level": 50},
             "box": [{"species": s, "level": 10}
                     for s in ["Pikachu", "Bulbasaur", "Squirtle", "Eevee", "Snorlax"]]}


def _describe(w):
    try:
        win = w.window().windowTitle() or type(w.window()).__name__
    except Exception:
        win = "?"
    txt = ""
    for getter in ("text", "placeholderText", "objectName"):
        try:
            txt = getattr(w, getter)() or txt
        except Exception:
            pass
        if txt:
            break
    return "%s '%s' in [%s]" % (type(w).__name__, str(txt)[:30], win)


def _candidates(app):
    from PyQt6.QtWidgets import QPushButton, QLineEdit
    btns = [w for w in app.allWidgets() if isinstance(w, QPushButton) and w.isVisible() and w.isEnabled()]
    edits = [w for w in app.allWidgets() if isinstance(w, QLineEdit) and w.isVisible() and w.isEnabled()]
    # sort for best-effort determinism (the journal is the real repro)
    btns.sort(key=lambda w: (w.text(), w.objectName()))
    edits.sort(key=lambda w: (w.placeholderText(), w.objectName()))
    return btns, edits


def _menu_actions(mw):
    out = []

    def walk(m):
        for a in m.actions():
            if a.menu():
                walk(a.menu())
            elif a.text():
                out.append(a)
    walk(mw.pokemenu)
    out.sort(key=lambda a: a.text())
    return out


def _run(seed, steps, journal_path, verbose=False):
    """CHILD: run one seeded monkey, journaling each action (flushed) before it runs."""
    import random
    from harness.real_env import start_real_session
    from harness.fixtures import seed_db
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtTest import QTest

    h = start_real_session(first_run=True)           # FAITHFUL state: sprites present, full menu
    import Ankimon.utils as u
    u.close_anki = lambda *a, **k: None              # so the monkey can't quit the process
    seed_db(SEED_TEAM, h.services.db)
    app = QApplication.instance()
    rng = random.Random(seed)

    j = open(journal_path, "w", buffering=1)

    def log(line):
        j.write(line + "\n")
        j.flush()
        os.fsync(j.fileno())                         # survive a hard abort
        if verbose:
            print(line)

    log("SEED %d STEPS %d" % (seed, steps))
    for i in range(steps):
        btns, edits = _candidates(app)
        acts = _menu_actions(h.aqt.mw)
        kind = rng.choice(["menu", "menu", "button", "button", "type", "close"])
        try:
            if kind == "menu" and acts:
                a = rng.choice(acts)
                log("step %d: TRIGGER menu '%s'" % (i, a.text()))     # journaled BEFORE doing it
                a.trigger()
            elif kind == "button" and btns:
                b = rng.choice(btns)
                log("step %d: CLICK %s" % (i, _describe(b)))
                b.click()
            elif kind == "type" and edits:
                e = rng.choice(edits)
                txt = rng.choice(WEIRD)
                log("step %d: TYPE %r into %s" % (i, txt, _describe(e)))
                e.clear()
                if txt.isascii():
                    QTest.keyClicks(e, txt)         # realistic keystrokes for ASCII
                else:
                    e.setText(txt)                  # QTest.keyClicks is ASCII-only; setText for unicode
            elif kind == "close":
                wins = [w for w in app.topLevelWidgets()
                        if w.isVisible() and w is not h.aqt.mw and w.windowTitle()]
                if wins:
                    w = rng.choice(wins)
                    log("step %d: CLOSE window '%s'" % (i, w.windowTitle()))
                    w.close()
                else:
                    log("step %d: noop (nothing open to close)" % i)
                    continue
            else:
                log("step %d: noop" % i)
                continue
            app.processEvents()
            for ev in h.events.drain():
                if ev.get("type") == "error":
                    log("  CAUGHT error event: %s" % (ev.get("message") or ev.get("exception")))
        except Exception as ex:
            log("  CAUGHT exception: %s: %s" % (type(ex).__name__, str(ex)[:120]))
    log("SURVIVED all %d steps" % steps)
    j.close()


def sweep(n_seeds=12, steps=40):
    """PARENT: run each seed in a child process; report any that hard-crash."""
    crashers = []
    for seed in range(n_seeds):
        fd, jp = tempfile.mkstemp(prefix="guifuzz_%d_" % seed, suffix=".log")
        os.close(fd)
        try:
            proc = subprocess.run([sys.executable, __file__, "--run", str(seed), str(steps), jp],
                                  capture_output=True, text=True, timeout=180)
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            rc = -99
        lines = []
        try:
            lines = [l for l in open(jp).read().splitlines() if l.strip()]
        except Exception:
            pass
        survived = bool(lines) and lines[-1].startswith("SURVIVED")
        if survived:
            print("  seed %2d: survived %d steps" % (seed, steps))
        else:
            culprit = lines[-1] if lines else "(no journal written)"
            crashers.append((seed, rc, culprit))
            print("  seed %2d: HARD CRASH (exit %d) — last action before death:\n           %s"
                  % (seed, rc, culprit))

    print("\ngui_fuzz: %d seeds x %d steps | %d hard-crashed" % (n_seeds, steps, len(crashers)))
    for seed, rc, culprit in crashers:
        print("  [seed %d] %s   →  replay: python3 harness/scenarios/gui_fuzz.py --replay %d %d"
              % (seed, culprit, seed, steps))
    return crashers


if __name__ == "__main__":
    if "--run" in sys.argv:
        i = sys.argv.index("--run")
        _run(int(sys.argv[i + 1]), int(sys.argv[i + 2]), sys.argv[i + 3])
    elif "--replay" in sys.argv:
        i = sys.argv.index("--replay")
        fd, jp = tempfile.mkstemp(suffix=".log")
        os.close(fd)
        _run(int(sys.argv[i + 1]), int(sys.argv[i + 2]), jp, verbose=True)
    else:
        sys.exit(1 if sweep() else 0)
