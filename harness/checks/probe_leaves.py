"""Import-safety probe for the aqt-free core leaves (Tasks 2-4).

Imports every module that should now load WITHOUT aqt/PyQt6. If any of them
still drags in Anki/Qt at module scope, the import below raises and this probe
fails loudly. Run:  python3 harness/checks/probe_leaves.py
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from harness.bootstrap import bootstrap

bootstrap(user_path="/tmp/ankimon_harness_probe")

# Guard: prove aqt/PyQt6 really are absent, so a passing import means the module
# is genuinely aqt-free (not that aqt happened to be installed).
for forbidden in ("aqt", "PyQt6"):
    try:
        __import__(forbidden)
        print(f"probe_leaves: WARNING {forbidden} is importable here; "
              f"the headless guarantee can't be fully proven in this env")
    except Exception:
        pass

LEAF_MODULES = [
    # pure helpers
    "Ankimon.business",
    "Ankimon.const",
    "Ankimon.events",
    "Ankimon.ui_port",
    "Ankimon.services",
    "Ankimon.resources",
    "Ankimon.move_names",
    # poke_engine submodule (battle simulation core)
    "Ankimon.poke_engine.objects",
    # data classes
    "Ankimon.pyobj.translator",
    "Ankimon.pyobj.InfoLogger",
    "Ankimon.pyobj.database_manager",
    "Ankimon.pyobj.error_handler",
    "Ankimon.pyobj.pokemon_obj",
    "Ankimon.pyobj.settings",
    "Ankimon.pyobj.ankimon_tracker",
    "Ankimon.pyobj.trainer_card",
    # function modules
    "Ankimon.functions.sprite_functions",
    "Ankimon.functions.battle_functions",
    "Ankimon.functions.friendship_evolution",
    "Ankimon.functions.badges_functions",
    "Ankimon.functions.pokemon_functions",
    "Ankimon.functions.pokedex_functions",
    "Ankimon.functions.trainer_functions",
    "Ankimon.functions.update_main_pokemon",
    "Ankimon.functions.drawing_utils",
    # the big IO helper module
    "Ankimon.utils",
]


def main() -> int:
    import importlib

    failures = []
    for name in LEAF_MODULES:
        try:
            importlib.import_module(name)
        except Exception as e:
            failures.append((name, repr(e)))

    if failures:
        print("probe_leaves: FAILED")
        for name, err in failures:
            print(f"  - {name}: {err}")
        return 1

    print(f"probe_leaves: OK ({len(LEAF_MODULES)} modules imported aqt-free)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
