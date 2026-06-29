"""
harness/checks/probe_contract.py — the harness's SEAM CONTRACT with Ankimon.

The harness drives the real game code, so it depends on a set of symbols staying
put: build_core, the service-registry fields, the DB methods, the env functions
the driver calls, and the pokedex helpers the fixtures build Pokemon from. Those
are the "fragile edge" — the things an Ankimon refactor (or an Anki API change)
can move out from under us.

This probe asserts every one of them exists, so drift fails HERE with a clear
"SEAM MOVED: X" line instead of a deep traceback in the middle of some scenario.
It's the early-warning the harness needs to stay sturdy across updates.

Run:  python3 harness/checks/probe_contract.py
"""

import sys
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)                       # for `harness`
sys.path.insert(0, os.path.join(_ROOT, "src"))  # for `Ankimon` (before any import)

from harness.driver import Driver

problems = []


def need(cond, msg):
    if not cond:
        problems.append(msg)


def need_methods(obj, names, label):
    for n in names:
        need(callable(getattr(obj, n, None)), "SEAM MOVED: %s.%s missing/not callable" % (label, n))


def need_attrs(obj, names, label):
    for n in names:
        need(hasattr(obj, n), "SEAM MOVED: %s.%s missing" % (label, n))


def main():
    # Boot FIRST: Driver() runs bootstrap() (which installs the Tier-1 import stub)
    # and build_core(), loading Ankimon's modules into sys.modules — only then do the
    # introspection imports below resolve (Tier-1 has no real aqt).
    d = Driver()
    s, env = d.services, d.env

    # --- core composition (shared with production singletons.py) ---------------
    from Ankimon import core
    need_methods(core, ["build_core", "bind_runtime_globals"], "Ankimon.core")

    # --- service registry: the fields core logic + the driver read -------------
    need_attrs(s, ["db", "settings", "logger", "translator", "main_pokemon",
                   "enemy_pokemon", "tracker", "trainer_card", "achievements",
                   "ui", "test_window", "evo_window", "pokemon_pc",
                   "reviewer"], "services")
    # the event bus is its own module (not a services field)
    from Ankimon.events import events as _bus
    need_methods(_bus, ["emit", "drain", "enable"], "Ankimon.events.events")

    # --- env functions the driver calls (answer/catch/defeat/encounter) --------
    need_attrs(env, ["catch_pokemon", "kill_pokemon", "new_pokemon",
                     "on_review_card", "collected_ids"], "env")

    # --- DB methods the harness + fixtures depend on ---------------------------
    need_methods(s.db, ["get_pokemon", "get_all_pokemon", "get_pokemon_count",
                        "get_main_pokemon", "save_pokemon", "save_main_pokemon",
                        "save_team", "get_team", "get_item", "add_item",
                        "get_all_items", "is_migrated", "migrate_from_json",
                        "set_config_value", "get_all_config", "execute"], "db")

    # --- settings + tracker surface -------------------------------------------
    need_methods(s.settings, ["get", "set"], "settings")
    need_methods(s.tracker, ["review"], "tracker")

    # --- the Driver's own public surface (scenarios/probes call these) ---------
    need_methods(d, ["answer", "catch", "defeat", "encounter", "set_enemy",
                     "set_setting", "get_state", "advance_time", "time_of_day"], "Driver")

    # --- pokedex/poke_engine helpers the fixtures build Pokemon from -----------
    from Ankimon.functions import pokedex_functions as pf
    need_methods(pf, ["search_pokedex", "search_pokedex_by_id", "get_base_experience",
                      "get_growth_rate", "get_effort_values", "_load_pokedex_id_index"],
                 "pokedex_functions")
    from Ankimon.functions import learnset_retrieval as lr
    need_methods(lr, ["get_all_pokemon_moves"], "learnset_retrieval")
    from Ankimon.functions import pokemon_functions as pkf
    need_methods(pkf, ["pick_random_gender"], "pokemon_functions")
    from Ankimon import utils as u
    need_methods(u, ["get_tier_by_id"], "utils")
    from Ankimon.poke_engine import helpers as h
    need_methods(h, ["normalize_name"], "poke_engine.helpers")
    from Ankimon.pyobj.pokemon_obj import PokemonObject
    need(callable(getattr(PokemonObject, "from_dict", None)), "SEAM MOVED: PokemonObject.from_dict missing")
    need(callable(getattr(PokemonObject, "to_dict", None)), "SEAM MOVED: PokemonObject.to_dict missing")

    n_checked = 9 + 15 + 5 + 16 + 3 + 9 + 6 + 2  # rough count for the summary line
    if problems:
        print("SEAM CONTRACT VIOLATIONS (%d) — the harness's dependencies moved:" % len(problems))
        for p in problems:
            print("  - " + p)
        raise SystemExit(1)
    print("probe_contract: OK (~%d seams intact)" % n_checked)


if __name__ == "__main__":
    main()
