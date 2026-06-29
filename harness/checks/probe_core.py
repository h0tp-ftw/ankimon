"""End-to-end probe for the aqt-free core composition (Task 7).

Boots build_core() against a fresh temp profile under plain python3 (no Anki/Qt)
and checks that every core object is constructed and registered in services.
Run:  python3 harness/checks/probe_core.py
"""

import sys
import pathlib
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from harness.bootstrap import bootstrap

# Fresh, isolated profile dir so the DB/migrations start clean.
_tmp = tempfile.mkdtemp(prefix="ankimon_core_probe_")
bootstrap(user_path=_tmp)

from Ankimon.services import services
from Ankimon.core import build_core


def main() -> int:
    core = build_core()

    # Core objects exist.
    assert core.logger is not None
    assert core.ankimon_db is not None
    assert core.settings_obj is not None
    assert core.translator is not None
    assert core.main_pokemon is not None
    assert core.enemy_pokemon is not None
    assert core.trainer_card is not None
    assert core.ankimon_tracker_obj is not None
    assert isinstance(core.achievements, dict) and core.achievements

    # Registry wired to the same instances.
    assert services.db is core.ankimon_db
    assert services.logger is core.logger
    assert services.settings is core.settings_obj
    assert services.translator is core.translator
    assert services.tracker is core.ankimon_tracker_obj
    assert services.main_pokemon is core.main_pokemon
    assert services.enemy_pokemon is core.enemy_pokemon
    assert services.trainer_card is core.trainer_card

    # Sanity on the objects.
    assert core.enemy_pokemon.name == "Rattata"
    assert core.main_pokemon.max_hp > 0
    settings_default = core.settings_obj.get("battle.cards_per_round")
    assert settings_default is not None

    print("probe_core: OK")
    print(f"  main_pokemon = {core.main_pokemon.name} (Lv {core.main_pokemon.level}, "
          f"HP {core.main_pokemon.hp}/{core.main_pokemon.max_hp})")
    print(f"  enemy_pokemon = {core.enemy_pokemon.name} (Lv {core.enemy_pokemon.level})")
    print(f"  trainer = {core.trainer_card.trainer_name} (Lv {core.trainer_card.level})")
    print(f"  cards_per_round setting = {settings_default}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
