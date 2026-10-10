from harness.headless_env import start_session
env = start_session()
from harness.driver import Driver
driver = Driver()

driver.env.services.settings.set("battle.daily_average", 50)
driver.env.services.db.set_user_data("trainer.level", 50)
driver.env.services.db.set_user_data("pokedex_caught", [6]) # Only Charizard
driver.env.services.settings.set("misc.active_region", "kanto")

import Ankimon.functions.encounter_functions as ef
from harness.fixtures import build_pokemon
from Ankimon.pyobj.ankimon_tracker import AnkimonTracker

def run():
    charizard = build_pokemon({"id": 6, "level": 100})
    driver.env.services.db.save_main_pokemon(charizard.to_dict())
    ef.main_pokemon = charizard
    ef.clear_encounter_cache()

    tracker = AnkimonTracker(driver.env.services.db)
    tracker.general_card_count = 500
    tracker.get_total_reviews = lambda: 500

    megas_found = 0
    res_ids = []

    import random
    random.seed(1)

    for _ in range(1000):
        res = ef.generate_random_pokemon(charizard.level, tracker, trainer_level=50, main_level=100)
        actual_id = res[1]
        if actual_id >= 10000:
            megas_found += 1
            res_ids.append(actual_id)

    print("Megas found:", megas_found)
    print("Unique Megas names:", [ef.search_pokedex_by_id(x) for x in set(res_ids)])

run()
