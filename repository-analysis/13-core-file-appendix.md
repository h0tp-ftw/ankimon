# Core File Appendix

This appendix contains excerpts and context for the most critical files in the Ankimon repository, preventing future agent turns from needing to rediscover them via repeated reads.

## 1. `src/Ankimon/__init__.py`
- **Why it was selected:** It is the primary orchestrator and entrypoint. It defines the Anki hooks and the core battle loop logic.
- **Included:** Focused excerpt of the main review hook logic.
- **Omitted:** The massive blocks of import statements, UI window initializations, and update checks are omitted to focus on behavior.
- **Preface:** Future agents should pay close attention to `on_review_card`. This is where the translation between Anki, Ankimon UI, and `poke_engine` occurs.

```python
# [EXCERPT: Core Review Loop in __init__.py]
def on_review_card(*args):
    try:
        multiplier = ankimon_tracker_obj.multiplier
        # ... [setup omitted] ...

        # Increment the counter when a card is reviewed
        ankimon_tracker_obj.cards_battle_round += 1
        ankimon_tracker_obj.general_card_count_for_battle += 1

        if ankimon_tracker_obj.cards_battle_round >= int(settings_obj.get("battle.cards_per_round")):
            ankimon_tracker_obj.cards_battle_round = 0
            ankimon_tracker_obj.pokemon_encouter += 1

            # ... [move selection logic omitted] ...

            '''
            To the devs,
            below is the MOST IMPORTANT function for the new engine.
            This runs our current Pokemon stats through the SirSkaro Poke-Engine.
            The "results" can then be used to access battle outcomes.
            '''
            results = simulate_battle_with_poke_engine(
                main_pokemon,
                enemy_pokemon,
                user_attack,
                enemy_attack,
                mutator_full_reset,
                new_state,
            )

            # Unpack results from the simulation
            battle_info = results[0]
            new_state = copy.deepcopy(results[1])
            # ... [damage variable assignments omitted] ...

            # IMMEDIATE STATE SYNCHRONIZATION (THE FIX)
            # Update Pokémon objects with the new state from the engine BEFORE any other processing.
            main_pokemon.hp = new_state.user.active.hp
            main_pokemon.current_hp = new_state.user.active.hp
            enemy_pokemon.hp = new_state.opponent.active.hp
            enemy_pokemon.current_hp = new_state.opponent.active.hp

            # Update statuses based on instructions, now that HP is correct.
            enemy_status_changed, main_status_changed = update_pokemon_battle_status(
                battle_info, enemy_pokemon, main_pokemon
            )

            # ... [UI tooltip generation and faint handling omitted] ...
```

## 2. `src/Ankimon/singletons.py`
- **Why it was selected:** It defines the global state of the application.
- **Included:** Full file.
- **Preface:** Observe how instances are created and passed to UI windows. This is the root of the dependency tree.

```python
"""
singletons.py

This module groups up some of the global variables that originally wer ein the __init__.py.
This module, hopefully, does not have vocation to remain permanently. This is but a transition step
in the splitting of the __init__.py file.
"""

import json
import uuid

from aqt import mw

# ... [imports omitted for brevity] ...

# start loggerobject for Ankimon
logger = ShowInfoLogger()

# Create the Settings object
settings_obj = Settings()

# Pass the correct attributes to SettingsWindow
settings_window = SettingsWindow(
    config=settings_obj.config,
    set_config_callback=settings_obj.set,
    save_config_callback=settings_obj.save_config,
    load_config_callback=settings_obj.load_config,
)

# Init Translator
translator = Translator(language=int(settings_obj.get("misc.language")))

mw.settings_ankimon = settings_window
mw.logger = logger
mw.translator = translator
mw.settings_obj = settings_obj

main_pokemon, mainpokemon_empty = update_main_pokemon()

# ... [enemy_pokemon instantiation omitted for brevity] ...

ankimon_tracker_obj = AnkimonTracker(
    trainer_card=trainer_card,
)
ankimon_tracker_obj.set_main_pokemon(main_pokemon)
ankimon_tracker_obj.set_enemy_pokemon(enemy_pokemon)

# ... [UI window instantiations (item_window, evo_window, etc.) omitted] ...
```

## 3. `src/Ankimon/poke_engine/instruction_generator.py`
- **Why it was selected:** It is the core logic engine determining how state mutates based on actions.
- **Included:** Focused excerpt of the signature of a core function.
- **Preface:** Notice how it takes the `mutator` state, checks rules (like immunities), and appends tuples like `(constants.MUTATOR_DAMAGE, attacker, damage_taken)`. This is the pattern for all battle mechanics.

```python
# [EXCERPT: get_instructions_from_damage]
def get_instructions_from_damage(mutator, defender, damage, accuracy, attacking_move, instruction):
    attacker = opposite_side[defender]
    attacker_side = get_side_from_state(mutator.state, attacker)
    damage_side = get_side_from_state(mutator.state, defender)

    # ... [Implementation handles applying the damage instruction to the state] ...
```
