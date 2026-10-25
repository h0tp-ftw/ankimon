import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from harness.real_driver import RealDriver

def main():
    d = RealDriver(settings_overrides={
        "battle.cards_per_round": 1,
        "battle.automatic_battle": 0,
        "audio.sounds": False,
        "misc.disable_evolution": True
    })

    from Ankimon.core import services
    db = services.db
    from Ankimon.utils import give_item

    give_item("master-ball")

    initial_caught = db.execute("SELECT COUNT(*) FROM captured_pokemon").fetchone()[0]
    print(f"Initial caught: {initial_caught}")

    # Try handle pokeball
    from Ankimon.pyobj.item_window import ItemWindow
    from aqt import mw
    window = ItemWindow(
        logger=services.logger,
        settings_obj=services.settings,
        main_pokemon=services.reviewer.main_pokemon,
        enemy_pokemon=services.reviewer.enemy_pokemon,
        achievements=services.achievements,
        starter_window=None,
        evo_window=None
    )
    window.pokeball_chances['master-ball'] = 100

    window.Handle_Pokeball("master-ball")

    after_caught = db.execute("SELECT COUNT(*) FROM captured_pokemon").fetchone()[0]
    print(f"After caught: {after_caught}")
    if after_caught > initial_caught:
        print("Success!")
    else:
        print("Failed.")

if __name__ == '__main__':
    main()
