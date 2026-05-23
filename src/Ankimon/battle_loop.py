import copy
import random
from dataclasses import dataclass, field
from typing import Any

from aqt import mw
from aqt.qt import QDialog

from .classes.choose_move_dialog import MoveSelectionDialog
from .functions.ankimon_hooks_to_poke_engine import simulate_battle_with_poke_engine
from .functions.badges_functions import (
    check_for_badge,
    handle_review_count_achievement,
    receive_badge,
)
from .functions.battle_functions import (
    process_battle_data,
    update_pokemon_battle_status,
    validate_pokemon_status,
)
from .functions.drawing_utils import tooltipWithColour
from .functions.encounter_functions import handle_enemy_faint, handle_main_pokemon_faint
from .pyobj.error_handler import show_warning_with_traceback
from .singletons import (
    achievements,
    ankimon_tracker_obj,
    enemy_pokemon,
    evo_window,
    logger,
    main_pokemon,
    reviewer_obj,
    settings_obj,
    test_window,
    trainer_card,
    translator,
)
from .utils import play_effect_sound, play_sound, safe_get_random_move


@dataclass
class BattleState:
    new_state: Any = None
    mutator_full_reset: int = 1
    user_hp_after: int = 0
    opponent_hp_after: int = 0
    dmg_from_enemy_move: int = 0
    dmg_from_user_move: int = 0
    item_receive_value: int = 0
    collected_pokemon_ids: set = field(default_factory=set)


_state = BattleState()


def init_battle_state(collected_pokemon_ids: set):
    _state.item_receive_value = random.randint(3, 385)
    _state.collected_pokemon_ids = collected_pokemon_ids


def _get_cards_per_round() -> int:
    cards_per_round = settings_obj.get("battle.cards_per_round")
    if isinstance(cards_per_round, int):
        return cards_per_round
    if isinstance(cards_per_round, str) and "-" in cards_per_round:
        try:
            min_val, max_val = map(int, cards_per_round.split("-"))
            return random.randint(min_val, max_val)
        except (ValueError, IndexError):
            return 2
    return 2


def on_review_card(*args):
    global _state
    s = _state

    try:
        multiplier = ankimon_tracker_obj.multiplier
        user_attack = (
            random.choice(main_pokemon.attacks) if main_pokemon.attacks else "splash"
        )
        enemy_attack = (
            random.choice(enemy_pokemon.attacks) if enemy_pokemon.attacks else "splash"
        )

        battle_sounds = settings_obj.get("audio.battle_sounds")

        ankimon_tracker_obj.cards_battle_round += 1
        ankimon_tracker_obj.cry_counter += 1
        cry_counter = ankimon_tracker_obj.cry_counter
        total_reviews = ankimon_tracker_obj.get_total_reviews()
        reviewer_obj.seconds = 0
        reviewer_obj.myseconds = 0
        ankimon_tracker_obj.general_card_count_for_battle += 1

        color = "#F0B27A"

        handle_review_count_achievement(total_reviews, achievements)

        s.item_receive_value -= 1
        if s.item_receive_value <= 0:
            s.item_receive_value = random.randint(3, 385)
            test_window.display_item()
            if not check_for_badge(achievements, 6):
                receive_badge(6, achievements)

        if total_reviews == settings_obj.get("battle.daily_average"):
            settings_obj.set("trainer.cash", settings_obj.get("trainer.cash") + 200)
            trainer_card.cash = settings_obj.get("trainer.cash")

        if (
            battle_sounds == True
            and ankimon_tracker_obj.general_card_count_for_battle == 1
        ):
            play_sound(enemy_pokemon.id, settings_obj)

        if ankimon_tracker_obj.cards_battle_round >= _get_cards_per_round():
            ankimon_tracker_obj.cards_battle_round = 0
            ankimon_tracker_obj.attack_counter = 0
            ankimon_tracker_obj.pokemon_encounter += 1
            multiplier = ankimon_tracker_obj.multiplier

            if (
                ankimon_tracker_obj.pokemon_encounter > 0
                and enemy_pokemon.hp > 0
                and multiplier < 1
            ):
                enemy_move = safe_get_random_move(enemy_pokemon.attacks, logger=logger)
                enemy_move_category = enemy_move.get("category")
                if enemy_move_category == "Status":
                    color = "#F7DC6F"
                elif enemy_move_category == "Special":
                    color = "#D2B4DE"
                else:
                    color = "#F0B27A"
            else:
                enemy_attack = "splash"

            move = safe_get_random_move(main_pokemon.attacks, logger=logger)
            category = move.get("category")

            if (
                ankimon_tracker_obj.pokemon_encounter > 0
                and main_pokemon.hp > 0
                and enemy_pokemon.hp > 0
            ):
                if settings_obj.get("controls.allow_to_choose_moves") == True:
                    dialog = MoveSelectionDialog(main_pokemon.attacks)
                    if dialog.exec() == QDialog.DialogCode.Accepted:
                        if dialog.selected_move:
                            user_attack = dialog.selected_move

                if category == "Status":
                    color = "#F7DC6F"
                elif category == "Special":
                    color = "#D2B4DE"
                else:
                    color = "#F0B27A"

            results = simulate_battle_with_poke_engine(
                main_pokemon,
                enemy_pokemon,
                user_attack,
                enemy_attack,
                s.mutator_full_reset,
                s.new_state,
            )

            battle_info = results[0]
            s.new_state = copy.deepcopy(results[1])
            s.dmg_from_enemy_move = results[2]
            s.dmg_from_user_move = results[3]
            s.mutator_full_reset = results[4]
            current_battle_info_changes = results[5]
            instructions = results[0]["instructions"]
            heals_to_user = sum(
                inst[2] for inst in instructions if inst[0:2] == ["heal", "user"]
            )
            heals_to_opponent = sum(
                inst[2] for inst in instructions if inst[0:2] == ["heal", "opponent"]
            )
            true_dmg_from_enemy_move = sum(
                inst[2] for inst in instructions if inst[0:2] == ["damage", "user"]
            )
            true_dmg_from_user_move = sum(
                inst[2] for inst in instructions if inst[0:2] == ["damage", "opponent"]
            )

            if true_dmg_from_enemy_move < 0:
                true_dmg_from_enemy_move = 0
                heals_to_user += abs(true_dmg_from_enemy_move)
            if true_dmg_from_user_move < 0:
                true_dmg_from_user_move = 0
                heals_to_opponent += abs(true_dmg_from_user_move)

            main_pokemon.hp = s.new_state.user.active.hp
            main_pokemon.current_hp = s.new_state.user.active.hp
            enemy_pokemon.hp = s.new_state.opponent.active.hp
            enemy_pokemon.current_hp = s.new_state.opponent.active.hp

            enemy_status_changed, main_status_changed = update_pokemon_battle_status(
                battle_info, enemy_pokemon, main_pokemon
            )
            enemy_pokemon.battle_status = validate_pokemon_status(enemy_pokemon)
            main_pokemon.battle_status = validate_pokemon_status(main_pokemon)

            formatted_battle_log = process_battle_data(
                battle_info=battle_info,
                multiplier=multiplier,
                main_pokemon=main_pokemon,
                enemy_pokemon=enemy_pokemon,
                user_attack=user_attack,
                enemy_attack=enemy_attack,
                dmg_from_user_move=true_dmg_from_user_move,
                dmg_from_enemy_move=true_dmg_from_enemy_move,
                user_hp_after=main_pokemon.hp,
                opponent_hp_after=enemy_pokemon.hp,
                battle_status=main_pokemon.battle_status,
                pokemon_encounter=ankimon_tracker_obj.pokemon_encounter,
                translator=translator,
                changes=current_battle_info_changes,
            )

            tooltipWithColour(formatted_battle_log, color)

            if true_dmg_from_enemy_move > 0 and multiplier < 1:
                reviewer_obj.myseconds = settings_obj.compute_special_variable(
                    "animate_time"
                )
                tooltipWithColour(
                    f" -{true_dmg_from_enemy_move} HP ", "#F06060", x=-200
                )
                play_effect_sound(settings_obj, "HurtNormal")

            if true_dmg_from_user_move > 0:
                reviewer_obj.seconds = settings_obj.compute_special_variable(
                    "animate_time"
                )
                tooltipWithColour(f" -{true_dmg_from_user_move} HP ", "#F06060", x=200)
                if multiplier == 1:
                    play_effect_sound(settings_obj, "HurtNormal")
                elif multiplier < 1:
                    play_effect_sound(settings_obj, "HurtNotEffective")
                elif multiplier > 1:
                    play_effect_sound(settings_obj, "HurtSuper")
            else:
                reviewer_obj.seconds = 0

            if int(heals_to_user) != 0:
                heal_color = "#68FA94" if heals_to_user > 0 else "#F06060"
                sign = "+" if heals_to_user > 0 else ""
                tooltipWithColour(
                    f" {sign}{int(heals_to_user)} HP ", heal_color, x=-250
                )

            if int(heals_to_opponent) != 0:
                heal_color = "#68FA94" if heals_to_opponent > 0 else "#F06060"
                sign = "+" if heals_to_opponent > 0 else ""
                tooltipWithColour(
                    f" {sign}{int(heals_to_opponent)} HP ", heal_color, x=250
                )

            if enemy_pokemon.hp < 1:
                enemy_pokemon.hp = 0
                test_window.display_battle()
                handle_enemy_faint(
                    main_pokemon,
                    enemy_pokemon,
                    s.collected_pokemon_ids,
                    test_window,
                    evo_window,
                    reviewer_obj,
                    logger,
                    achievements,
                )
                s.mutator_full_reset = 1

        if cry_counter == 10 and battle_sounds is True:
            play_sound(enemy_pokemon.id, settings_obj)

        if main_pokemon.hp < 1:
            handle_main_pokemon_faint(
                main_pokemon, enemy_pokemon, test_window, reviewer_obj, translator
            )
            s.mutator_full_reset = 1

        class Container:
            pass

        reviewer = Container()
        reviewer.web = mw.reviewer.web
        reviewer_obj.update_life_bar(reviewer, 0, 0)
        if test_window is not None:
            if enemy_pokemon.hp > 0:
                test_window.display_battle()
    except Exception as e:
        show_warning_with_traceback(
            parent=mw, exception=e, message="An error occurred in reviewer:"
        )
