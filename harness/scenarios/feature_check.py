"""
harness/scenarios/feature_check.py — validate a FEATURE works as intended (Tier 2).

mega_fuzz proves a feature doesn't *crash*. This proves it does the *right thing*:
drive the real feature the way a user would, then assert the intended OUTCOME (the
DB / game state / event changed exactly as it should) — and that no error fired.

This is the TEMPLATE for "an agent, given guidance, validates a new feature." When
someone adds a menu/button/action and says "it should do Y", you write a check():

    def check_my_feature(d, app, db, pc, pool):
        before = <observe state>
        <drive the feature: open a window, find the widget, click/type, or fire the
         exact callback the menu wires>
        after = <observe state>
        return ("my_feature", after == expected, "before=%s after=%s" % (before, after))

The runner boots ONE real seeded session (real Qt windows offscreen), runs every
check, drains error events after each, and prints PASS/FAIL + a final verdict. Each
check operates on its own box Pokemon so they don't interfere. Add a check to CHECKS
and it joins the suite — so this doubles as a regression gate for existing features.

    source .tier2/env.sh
    python3 harness/scenarios/feature_check.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

SEED = {
    "main": {"species": "Gengar", "level": 50},
    "box": [{"species": s, "level": 10 + i}
            for i, s in enumerate(["Pikachu", "Bulbasaur", "Squirtle", "Eevee", "Snorlax", "Lapras"])],
    "items": {"Pokeball": 25, "Potion": 10},
}


def _find(app, cls, **match):
    """First visible widget of `cls` whose getter() text contains a substring."""
    for w in app.allWidgets():
        if not isinstance(w, cls) or not w.isVisible():
            continue
        for getter, sub in match.items():
            try:
                val = getattr(w, getter)() or ""
            except Exception:
                val = ""
            if sub.lower() in str(val).lower():
                return w
    return None


# --- feature checks (each: drive the real feature, assert the intended outcome) ---

def check_rename(d, app, db, pc, pool):
    """The 'Rename Pokémon' feature (full real GUI drive): open a Pokemon's details,
    type a nickname into the real field, click the real button → DB nickname updates."""
    from PyQt6.QtWidgets import QLineEdit, QPushButton
    from PyQt6.QtTest import QTest
    iid = pool.pop()
    pkmn = db.get_pokemon(iid)
    before = pkmn.get("nickname", "")
    pc.show_pokemon_details(pkmn)
    app.processEvents()
    edit = _find(app, QLineEdit, placeholderText="Nickname")
    btn = _find(app, QPushButton, text="Rename")
    if not edit or not btn:
        return ("rename (full GUI)", False, "could not find the rename field/button in the details window")
    edit.clear()
    QTest.keyClicks(edit, "Sparky")
    btn.click()
    app.processEvents()
    after = (db.get_pokemon(iid) or {}).get("nickname", "")
    return ("rename (full GUI)", after == "Sparky", "nickname %r -> %r (intended 'Sparky')" % (before, after))


def check_make_favorite(d, app, db, pc, pool):
    """The right-click 'Make favorite' context action (its real wired callback):
    toggling flips is_favorite in the DB, and toggling again flips it back."""
    iid = pool.pop()
    start = bool((db.get_pokemon(iid) or {}).get("is_favorite", False))
    pc.toggle_favorite(db.get_pokemon(iid))
    on = bool((db.get_pokemon(iid) or {}).get("is_favorite", False))
    pc.toggle_favorite(db.get_pokemon(iid))
    back = bool((db.get_pokemon(iid) or {}).get("is_favorite", False))
    ok = (on == (not start)) and (back == start)
    return ("make_favorite (context action)", ok,
            "is_favorite %s -> %s -> %s (intended: flip then flip back)" % (start, on, back))


def check_catch_grows_collection(d, app, db, pc, pool):
    """The catch feature (real reviewer catch path): when the wild Pokemon has
    fainted, catching it adds exactly one to the collection."""
    before = db.get_pokemon_count()
    d.services.enemy_pokemon.hp = 0          # catch is only valid on a fainted wild
    d.services.enemy_pokemon.current_hp = 0
    d.catch()
    app.processEvents()
    after = db.get_pokemon_count()
    return ("catch_grows_collection", after == before + 1,
            "collection %d -> %d (intended +1)" % (before, after))


def check_update_channel(d, app, db, pc, pool):
    """The user-selectable auto-update channel (real update dialog): the dropdown
    offers stable/experimental/main and PERSISTS the choice through
    update_manager.get/set_update_channel, and the release-channel poll raises the
    update prompt when a newer release exists on the channel. All monkeypatches are
    restored + the channel reset so the shared session stays clean for other checks."""
    from Ankimon.pyobj import update_manager as um
    import Ankimon.pyobj.update_dialog as ud
    from Ankimon import changelog

    # Only the source pickers touch the network; stub them so the dialog builds
    # offline. The channel row itself is built in __init__, independent of this.
    saved_fetch = {fn: getattr(ud, fn) for fn in ("fetch_releases", "fetch_tags", "fetch_branches", "fetch_open_prs")}
    saved_um_fetch = {fn: getattr(um, fn) for fn in ("fetch_branch_sha", "fetch_commit_date", "fetch_branch_commits")}
    for fn in saved_fetch:
        setattr(ud, fn, lambda *a, **k: [])
    for fn in saved_um_fetch:
        setattr(um, fn, lambda *a, **k: None if fn != "fetch_branch_commits" else [])
    # Preserve the original channel setting to restore after the test
    original_channel = d.services.settings.get("misc.update_channel")
    dlg = None
    try:
        dlg = ud.UpdateDialog()
        app.processEvents()
        combo = dlg.channel_combo
        channels = [combo.itemData(i) for i in range(combo.count())]
        if channels != [um.CHANNEL_STABLE, um.CHANNEL_EXPERIMENTAL, um.CHANNEL_MAIN]:
            return ("update_channel (dropdown + release poll)", False, "channels=%s" % channels)

        # Drive the real combo -> the choice must round-trip through the settings.
        combo.setCurrentIndex(combo.findData(um.CHANNEL_EXPERIMENTAL))
        app.processEvents()
        persisted = (um.get_update_channel() == um.CHANNEL_EXPERIMENTAL
                     and d.services.settings.get("misc.update_channel") == um.CHANNEL_EXPERIMENTAL)

        # A newer release on the channel must reach the update prompt.
        seen = {}
        orig_prompt = ud.show_release_update_prompt
        saved_um = {k: getattr(um, k) for k in ("is_git_clone", "read_update_state", "latest_release_for_channel")}
        ud.show_release_update_prompt = lambda ch, rel: seen.update(ch=ch, rel=rel)
        um.is_git_clone = lambda: False
        um.read_update_state = lambda: {}
        um.latest_release_for_channel = lambda ch: {"name": "99.9", "body": "", "zipball_url": "x"}
        try:
            changelog._poll_release_channel("stable")   # synchronous QueryOp -> prompt inline
            app.processEvents()
        finally:
            ud.show_release_update_prompt = orig_prompt
            for k, v in saved_um.items():
                setattr(um, k, v)
        prompted = seen.get("ch") == "stable" and (seen.get("rel") or {}).get("name") == "99.9"

        return ("update_channel (dropdown persists + release poll prompts)", persisted and prompted,
                "channels=%s persist=%s prompt=%s" % (channels, persisted, prompted))
    finally:
        for fn, orig in saved_fetch.items():
            setattr(ud, fn, orig)
        for fn, orig in saved_um_fetch.items():
            setattr(um, fn, orig)
        try:                                            # restore original channel setting
            d.services.settings.set("misc.update_channel", original_channel)
        except Exception:
            pass
        if dlg is not None:                             # don't linger into interpreter teardown
            dlg.close()
            dlg.deleteLater()
            app.processEvents()


CHECKS = [check_rename, check_make_favorite, check_catch_grows_collection, check_update_channel]


def _boot():
    from harness.real_driver import RealDriver
    from harness.fixtures import seed_db
    from PyQt6.QtWidgets import QApplication

    d = RealDriver(first_run=True, first_encounter=True)
    import Ankimon.singletons as S          # import AFTER boot (RealDriver sets up the path)
    import Ankimon.utils as u
    u.close_anki = lambda *a, **k: None
    seed_db(SEED, d.services.db)
    app = QApplication.instance()
    pc = S.pokemon_pc
    # Open the PC box (exactly what the menu action does: qconnect(..., pokemon_pc.show))
    # so its grid + details panel are live and visible, like a user opening it.
    pc.show()
    app.processEvents()
    return d, app, d.services.db, pc


def run(verbose=True):
    d, app, db, pc = _boot()
    pool = [r[0] for r in db.execute(
        "SELECT individual_id FROM captured_pokemon WHERE is_main = 0").fetchall()]
    results = []
    for chk in CHECKS:
        db_events = d.events.drain()                      # clear before
        try:
            name, ok, detail = chk(d, app, db, pc, pool)
        except Exception as e:
            name, ok, detail = chk.__name__, False, "raised %s: %s" % (type(e).__name__, e)
        errs = [e for e in d.events.drain() if isinstance(e, dict) and e.get("type") == "error"]
        if errs:
            ok = False
            detail += " | ERROR event: %s" % (errs[0].get("message") or errs[0].get("exception"))
        results.append((name, ok, detail))
        if verbose:
            print("  [%s] %-32s %s" % ("PASS" if ok else "FAIL", name, detail))
    n_ok = sum(1 for _, ok, _ in results if ok)
    if verbose:
        print("\nfeature_check: %d/%d features behave as intended" % (n_ok, len(results)))
    return results


if __name__ == "__main__":
    res = run()
    sys.exit(0 if all(ok for _, ok, _ in res) else 1)
