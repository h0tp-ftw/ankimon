"""Tier-2 probe: boot the REAL Ankimon add-on headless under offscreen Qt.

Verifies that `import Ankimon` runs end to end (real __init__ -> singletons ->
real Qt windows) with only the Anki host faked. Confirms the registered objects
are the REAL window classes (not Tier-1 fakes) and the Qt presenter is wired.

Run (after sourcing the Tier-2 env file):
    python -m harness.checks.probe_real_boot
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from harness.real_env import start_real_session


def main() -> int:
    s = start_real_session()
    sv = s.services

    def describe(obj):
        return f"{type(obj).__module__}.{type(obj).__name__}"

    print("REAL BOOT OK")
    print(f"  mw           = {describe(s.aqt.mw)}")
    print(f"  main_pokemon = {sv.main_pokemon.name} (Lv {sv.main_pokemon.level})")
    print(f"  enemy_pokemon= {sv.enemy_pokemon.name}")
    print(f"  test_window  = {describe(sv.test_window)}")
    print(f"  evo_window   = {describe(sv.evo_window)}")
    print(f"  pokemon_pc   = {describe(sv.pokemon_pc)}")
    print(f"  reviewer     = {describe(sv.reviewer)}")
    print(f"  ui presenter = {describe(sv.ui)}")

    # The whole point of Tier 2: these are the REAL window classes, not harness fakes.
    assert "harness" not in describe(sv.test_window), "test_window should be the real one"
    assert type(sv.test_window).__name__ == "TestWindow", describe(sv.test_window)
    assert type(sv.ui).__name__ == "QtPresenter", describe(sv.ui)

    # gui_hooks registry is live and the battle hook is registered.
    n = len(s.gui_hooks.reviewer_did_answer_card)
    print(f"  reviewer_did_answer_card hooks registered: {n}")
    assert n >= 1, "on_review_card not registered on the real hook"

    print("probe_real_boot: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
