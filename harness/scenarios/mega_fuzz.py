"""
harness/scenarios/mega_fuzz.py — the unified "do EVERYTHING" fuzzer (Tier 2).

One fuzzer that exercises the WHOLE add-on the way a chaotic human would: it boots
the genuine Ankimon (real Qt windows, offscreen) into a random *starting world*,
then loops random *actions* drawn from the complete surface — gameplay AND GUI —
over auto-discovered targets, until something breaks or N steps pass.

  initial WORLD  (rng per seed)
    first_run  — sprites present, empty box, full menu       (fresh post-download)
    blank      — NO sprites (download denied), gated menu     ("I said no to sprites")
    seeded     — sprites + a full team/box                    (established player)
    corrupt    — seeded, then ONE saved Pokemon mangled       (corrupt save file)

  ACTION space  (rng per step, weighted) — every verb a user has:
    gameplay : answer(ease) · catch · defeat · encounter · set_setting
    GUI      : open-menu · click-button · type-weird · close-window
    RIGHT-CLICK → context-menu   (PC box: favorite/give-item/release/trade/rename/evolve)
  Targets auto-discover from live widgets — a window that opens mid-run is fuzzed for
  free; a PokemonSlotButton that appears becomes right-clickable.

Built for reproducibility, because a bad GUI action can HARD-crash the process (a
C++ Qt abort uncatchable from Python):
  * every action is journaled + flushed + fsync'd BEFORE it runs — the journal's
    last line is exactly the action (and world) that killed the process;
  * each seed runs in its own CHILD process — a hard crash kills the child, the
    parent reads the journal and reports the culprit + an exact replay command.

This FINDS bugs; it does not fix them. Expect a lot — that's the point.

    source .tier2/env.sh
    python3 harness/scenarios/mega_fuzz.py                    # sweep seeds (random worlds), report crashers
    python3 harness/scenarios/mega_fuzz.py --world corrupt    # sweep, forcing the corrupt-save world
    python3 harness/scenarios/mega_fuzz.py --replay 7 80      # re-run one seed, verbose, to watch it
    python3 harness/scenarios/mega_fuzz.py --replay 7 80 seeded   # ...forcing a world
    # (--run SEED STEPS JOURNAL [WORLD] is the internal child entrypoint)
"""

import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

WORLDS = ["first_run", "blank", "seeded", "corrupt"]

WEIRD = ["", "💀", "Ž★🔥", "x" * 80, "'; DROP TABLE captured_pokemon;--", "999", "-1", "../../etc/passwd"]
EASES = ["again", "hard", "good", "easy"]

# A populated save for the seeded/corrupt worlds — a main + a teamful + a boxful, so
# the PC box has plenty of slots to right-click and plenty of data to render.
BIG_TEAM = {
    "main": {"species": "Gengar", "level": 50, "nickname": "Spooky"},
    "team": [{"species": s, "level": 40} for s in ["Charizard", "Blastoise", "Venusaur"]],
    "box": [{"species": s, "level": 10 + i}
            for i, s in enumerate(["Pikachu", "Bulbasaur", "Squirtle", "Eevee", "Snorlax",
                                   "Mew", "Dragonite", "Gyarados", "Lapras", "Ditto"])],
    "items": {"Pokeball": 25, "Potion": 10, "Rare Candy": 5},
}


# --- footprint ---------------------------------------------------------------

def _rss_mb():
    """Current resident set size in MB (Linux /proc) — for organic leak flagging."""
    try:
        with open("/proc/self/statm") as f:
            pages = int(f.read().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE") / 1e6
    except Exception:
        return 0.0


# --- target auto-discovery ---------------------------------------------------

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
    btns.sort(key=lambda w: (w.text(), w.objectName()))       # best-effort determinism; journal is the real repro
    edits.sort(key=lambda w: (w.placeholderText(), w.objectName()))
    return btns, edits


def _rightclickables(app):
    """Any live widget exposing a ``rightClicked`` signal — PC box slots, etc."""
    out = [w for w in app.allWidgets()
           if w.isVisible() and w.isEnabled() and hasattr(type(w), "rightClicked")]
    out.sort(key=lambda w: (w.objectName(), _describe(w)))
    return out


def _menu_actions(mw):
    out = []

    def walk(m):
        for a in m.actions():
            if a.menu():
                walk(a.menu())
            elif a.text():
                out.append(a)
    menu = getattr(mw, "pokemenu", None)
    if menu is not None:
        walk(menu)
    out.sort(key=lambda a: a.text())
    return out


# --- the corrupt world -------------------------------------------------------

def _corrupt_one(db, rng, log):
    """Mangle ONE saved Pokemon to valid-JSON-but-broken-values — the realistic
    'part of my save is corrupt' a normal user can hit (a half-finished write, a
    bad migration). Stays valid JSON so it passes the generated virtual columns;
    the damage surfaces when the game LOADS/renders it (PC box, battle, HUD)."""
    rows = db.execute("SELECT individual_id, data FROM captured_pokemon").fetchall()
    if not rows:
        log("  (corrupt: no rows to mangle)")
        return
    row = rng.choice(rows)
    iid, data = row[0], json.loads(row[1])
    mangles = [
        ("level=garbage", lambda x: x.__setitem__("level", rng.choice([-5, 0, 99999, "fifty", None]))),
        ("hp=garbage",    lambda x: x.__setitem__("hp", rng.choice(["lots", -1, None]))),
        ("stats={}",      lambda x: x.__setitem__("stats", {})),
        ("no attacks",    lambda x: x.__setitem__("attacks", [])),
        ("type=broken",   lambda x: x.__setitem__("type", rng.choice([None, [], ["???"]]))),
        ("drop name",     lambda x: x.pop("name", None)),
        ("ability=null",  lambda x: x.__setitem__("ability", None)),
        ("ev=broken",     lambda x: x.__setitem__("ev", "broken")),
        ("id=garbage",    lambda x: x.__setitem__("id", rng.choice([-1, 0, 999999]))),
    ]
    label, fn = rng.choice(mangles)
    fn(data)
    db.execute("UPDATE captured_pokemon SET data = ? WHERE individual_id = ?", (json.dumps(data), iid))
    db._get_connection().commit()
    log("  CORRUPT row %s -> %s" % (str(iid)[:8], label))


# --- the child ---------------------------------------------------------------

def _run(seed, steps, journal_path, world=None, verbose=False):
    """CHILD: one seeded run in a random world; journals each action before it runs."""
    import random
    from harness.real_driver import RealDriver
    from harness.fixtures import seed_db
    from PyQt6.QtWidgets import QApplication, QMenu
    from PyQt6.QtTest import QTest

    rng = random.Random(seed)
    world = world or rng.choice(WORLDS)

    j = open(journal_path, "w", buffering=1)

    def log(line):
        j.write(line + "\n")
        j.flush()
        os.fsync(j.fileno())                         # survive a hard abort
        if verbose:
            print(line)

    log("SEED %d STEPS %d WORLD %s" % (seed, steps, world))

    # Boot the genuine add-on into the chosen world.
    has_assets = world != "blank"                    # blank = the user who denied the sprite download
    d = RealDriver(first_run=has_assets, first_encounter=has_assets)
    import Ankimon.utils as u
    u.close_anki = lambda *a, **k: None              # the monkey must not quit the process
    app = QApplication.instance()
    mw = d.aqt.mw

    if world in ("seeded", "corrupt"):
        seed_db(BIG_TEAM, d.services.db)             # PC box reads the DB live, so this populates it
    if world == "corrupt":
        _corrupt_one(d.services.db, rng, log)

    # Right-click pops a QMenu via menu.exec() (blocks). Neuter it to AUTO-pick and
    # trigger a random context action (its actions are pre-wired via .triggered) and
    # journal the choice — so favorite/give-item/release/trade/rename/evolve all fire.
    def _fuzz_menu_exec(self, *a, **k):
        acts = [x for x in self.actions() if x.isEnabled() and x.text() and not x.isSeparator()]
        if acts:
            c = rng.choice(acts)
            log("    CONTEXT-ACTION '%s'" % c.text())
            c.trigger()
        return None
    QMenu.exec = _fuzz_menu_exec
    QMenu.exec_ = _fuzz_menu_exec

    config = dict(d.services.settings.config)        # all ~63 real setting keys + defaults

    # Weighted action menu: gameplay-heavy, with the full GUI surface mixed in.
    KINDS = (["answer"] * 5 + ["catch"] * 2 + ["defeat"] * 2 + ["encounter"] * 1 + ["setting"] * 1
             + ["menu"] * 4 + ["click"] * 3 + ["type"] * 2 + ["close"] * 1 + ["rightclick"] * 3)

    def check(ret):
        app.processEvents()
        for ev in list(ret or []) + d.events.drain():
            if isinstance(ev, dict) and ev.get("type") == "error":
                message = ev.get("message") or "error"
                exception = ev.get("exception")
                if exception and exception not in message:
                    message = "%s %s" % (message, exception)
                log("  CAUGHT error event: %s" % message)

    # Worlds with a populated box: open the PC box once up front so its slots are
    # live for right-click AND so a corrupt row is actually LOADED/rendered — else
    # the mangled row sits dormant in the DB and the corrupt world tests nothing.
    # (If rendering a corrupt row hard-crashes, this warm-up line is the culprit.)
    if world in ("seeded", "corrupt"):
        pc = [a for a in _menu_actions(mw) if a.text() == "Pokémon PC"]
        if pc:
            log("warmup: open 'Pokémon PC' (load the seeded/corrupt box)")
            try:
                pc[0].trigger()
                check(None)
            except Exception as ex:
                log("  CAUGHT exception in warmup: %s: %s" % (type(ex).__name__, str(ex)[:160]))

    rss0 = _rss_mb()
    log("RSS start: %.1f MB" % rss0)
    for i in range(steps):
        if i and i % 15 == 0:
            log("RSS step %d: %.1f MB" % (i, _rss_mb()))
        kind = rng.choice(KINDS)
        ret = None
        try:
            if kind == "answer":
                e = rng.choice(EASES)
                log("step %d: ANSWER %s" % (i, e)); ret = d.answer(e)
            elif kind == "catch":
                log("step %d: CATCH" % i); ret = d.catch()
            elif kind == "defeat":
                log("step %d: DEFEAT" % i); ret = d.defeat()
            elif kind == "encounter":
                log("step %d: ENCOUNTER" % i); ret = d.encounter()
            elif kind == "setting":
                k = rng.choice(list(config))
                v = _fuzz_value(rng, config[k])
                log("step %d: SET %s=%r" % (i, k, v)); d.set_setting(k, v)
            elif kind == "menu":
                acts = _menu_actions(mw)
                if not acts:
                    log("step %d: noop (menu gated/empty)" % i); continue
                a = rng.choice(acts)
                log("step %d: MENU '%s'" % (i, a.text())); a.trigger()
            elif kind == "click":
                btns, _ = _candidates(app)
                if not btns:
                    log("step %d: noop (no buttons)" % i); continue
                b = rng.choice(btns)
                log("step %d: CLICK %s" % (i, _describe(b))); b.click()
            elif kind == "type":
                _, edits = _candidates(app)
                if not edits:
                    log("step %d: noop (no fields)" % i); continue
                ed = rng.choice(edits); txt = rng.choice(WEIRD)
                log("step %d: TYPE %r into %s" % (i, txt, _describe(ed)))
                ed.clear()
                if txt.isascii():
                    QTest.keyClicks(ed, txt)
                else:
                    ed.setText(txt)                  # QTest.keyClicks is ASCII-only
            elif kind == "close":
                wins = [w for w in app.topLevelWidgets()
                        if w.isVisible() and w is not mw and w.windowTitle()]
                if not wins:
                    log("step %d: noop (nothing to close)" % i); continue
                w = rng.choice(wins)
                log("step %d: CLOSE window '%s'" % (i, w.windowTitle())); w.close()
            elif kind == "rightclick":
                rc = _rightclickables(app)
                if not rc:
                    log("step %d: noop (nothing right-clickable — PC box not open yet)" % i); continue
                w = rng.choice(rc)
                log("step %d: RIGHT-CLICK %s" % (i, _describe(w)))
                w.rightClicked.emit()                # -> context menu -> _fuzz_menu_exec triggers an action
            check(ret)
        except Exception as ex:
            log("  CAUGHT exception: %s: %s" % (type(ex).__name__, str(ex)[:160]))
    rssN = _rss_mb()
    log("RSS final: %.1f MB (delta %+.1f over %d steps)" % (rssN, rssN - rss0, steps))
    log("SURVIVED all %d steps" % steps)
    j.close()


def _fuzz_value(rng, default):
    """A random value for a setting, typed from its default + boundary picks."""
    if isinstance(default, bool):                    # bool BEFORE int (bool is an int subclass)
        return rng.random() < 0.5
    if isinstance(default, int):
        return rng.choice([0, 1, 2, -1, -5, rng.randint(0, 9999), 999999])
    if isinstance(default, float):
        return rng.choice([0.0, -1.0, rng.random(), rng.uniform(0, 1000), 1e9])
    return rng.choice(["", "x" * 100, "Ž★🔥", "999", "-1", str(default)])


# --- the parent --------------------------------------------------------------

def _signature(caught_line):
    """Collapse a 'CAUGHT ...' journal line to a dedup key (drop the variable tail)."""
    s = caught_line.strip()
    for cut in (" at ", " in <", "': ", ": '"):
        if cut in s:
            s = s.split(cut)[0]
    return s[:90]


def _run_one(seed, steps, world):
    """Run ONE child subprocess and return (seed, rc, journal-lines)."""
    fd, jp = tempfile.mkstemp(prefix="megafuzz_%d_" % seed, suffix=".log")
    os.close(fd)
    cmd = [sys.executable, __file__, "--run", str(seed), str(steps), jp]
    if world:
        cmd.append(world)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        rc = -99
    try:
        lines = [l for l in open(jp).read().splitlines() if l.strip()]
    except Exception:
        lines = []
    return seed, rc, lines


def sweep(n_seeds=12, steps=80, world=None, parallel=1):
    """PARENT: run each seed in a child process (up to `parallel` at once) and
    aggregate hard crashes, soft error events, AND footprint into one ranked
    findings report. `world` may be None (random/child), a str (forced), or a
    LIST that is cycled across seeds (e.g. ['corrupt','blank'] to mine the edge
    worlds hard)."""
    crashers = []                 # (seed, world, rc, culprit-action)
    soft = {}                     # signature -> {"count", "seeds": set, "example", "world"}
    footprints = []               # (seed, world, delta_mb) for survived runs

    def world_for(seed):
        if isinstance(world, (list, tuple)):
            return world[seed % len(world)]
        return world

    def handle(seed, rc, lines):
        w = lines[0].split("WORLD")[-1].strip() if lines else "?"
        caught = [l for l in lines if "CAUGHT" in l]
        for c in caught:
            sig = _signature(c)
            rec = soft.setdefault(sig, {"count": 0, "seeds": set(), "example": c.strip(), "world": w})
            rec["count"] += 1
            rec["seeds"].add(seed)
        delta = None
        for l in lines:
            if l.startswith("RSS final") and "delta" in l:
                try:
                    delta = float(l.split("delta")[1].split("over")[0])
                except Exception:
                    pass
        survived = bool(lines) and lines[-1].startswith("SURVIVED")
        if survived:
            rc_count = sum(1 for l in lines if "RIGHT-CLICK" in l)
            ctx_count = sum(1 for l in lines if "CONTEXT-ACTION" in l)
            if delta is not None:
                footprints.append((seed, w, delta))
            mem = (" | RSS %+.0f MB" % delta) if delta is not None else ""
            print("  seed %3d [%-9s]: survived %d steps  (%d right-clicks, %d context-actions, %d soft errors)%s"
                  % (seed, w, steps, rc_count, ctx_count, len(caught), mem))
        else:
            culprit = next((l for l in reversed(lines) if not l.startswith("RSS")), lines[-1] if lines else "(no journal written)")
            crashers.append((seed, w, rc, culprit))
            print("  seed %3d [%-9s]: HARD CRASH (exit %d) — last action before death:\n           %s"
                  % (seed, w, rc, culprit))

    if parallel > 1:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=parallel) as ex:
            futs = [ex.submit(_run_one, s, steps, world_for(s)) for s in range(n_seeds)]
            for f in as_completed(futs):
                handle(*f.result())
    else:
        for s in range(n_seeds):
            handle(*_run_one(s, steps, world_for(s)))

    wlabel = ("|".join(world) if isinstance(world, (list, tuple)) else world) or "random"
    print("\nmega_fuzz: %d seeds x %d steps (worlds=%s, parallel=%d) | %d hard-crashed | %d distinct soft errors"
          % (n_seeds, steps, wlabel, parallel, len(crashers), len(soft)))
    if crashers:
        print("\nHARD CRASHES (process aborted — uncatchable in Python):")
        for seed, w, rc, culprit in crashers:
            wpart = (" %s" % w) if w and w != "?" else ""
            print("  [seed %d %s] %s\n      replay: python3 harness/scenarios/mega_fuzz.py --replay %d %d%s"
                  % (seed, w, culprit, seed, steps, wpart))
    if soft:
        print("\nSOFT ERRORS (caught mid-run — error events / handled exceptions):")
        for sig, rec in sorted(soft.items(), key=lambda kv: -kv[1]["count"]):
            ex = next(iter(sorted(rec["seeds"])))
            print("  x%-3d [%s] %s\n      e.g. replay: python3 harness/scenarios/mega_fuzz.py --replay %d %d %s"
                  % (rec["count"], rec["world"], rec["example"][:110], ex, steps, rec["world"]))
    if footprints:
        by_world = {}
        for seed, w, d in footprints:
            by_world.setdefault(w, []).append(d)
        print("\nFOOTPRINT (RSS growth over the run — leak signal; high+linear = investigate):")
        for w in sorted(by_world):
            ds = by_world[w]
            print("  %-9s avg %+.0f MB / run  (max %+.0f MB, n=%d)"
                  % (w, sum(ds) / len(ds), max(ds), len(ds)))
        worst = max(footprints, key=lambda t: t[2])
        if worst[2] >= 100:
            print("  ! biggest grower: seed %d [%s] %+.0f MB — replay: "
                  "python3 harness/scenarios/mega_fuzz.py --replay %d %d %s"
                  % (worst[0], worst[1], worst[2], worst[0], steps, worst[1]))
    return crashers


if __name__ == "__main__":
    if "--run" in sys.argv:
        i = sys.argv.index("--run")
        extra = sys.argv[i + 4] if len(sys.argv) > i + 4 and not sys.argv[i + 4].startswith("-") else None
        _run(int(sys.argv[i + 1]), int(sys.argv[i + 2]), sys.argv[i + 3], world=extra)
    elif "--replay" in sys.argv:
        i = sys.argv.index("--replay")
        w = sys.argv[i + 3] if len(sys.argv) > i + 3 and not sys.argv[i + 3].startswith("-") else None
        fd, jp = tempfile.mkstemp(suffix=".log")
        os.close(fd)
        _run(int(sys.argv[i + 1]), int(sys.argv[i + 2]), jp, world=w, verbose=True)
    else:
        def _opt(flag, default, cast=int):
            return cast(sys.argv[sys.argv.index(flag) + 1]) if flag in sys.argv else default
        world = None
        if "--world" in sys.argv:
            raw = sys.argv[sys.argv.index("--world") + 1]
            world = raw.split(",") if "," in raw else raw     # comma list -> cycle worlds
        n_seeds = _opt("--seeds", 12)
        steps = _opt("--steps", 80)
        parallel = _opt("--parallel", 1)
        sys.exit(1 if sweep(n_seeds=n_seeds, steps=steps, world=world, parallel=parallel) else 0)
