import copy
import random
from dataclasses import dataclass, field
from typing import Any, Optional

from .services import services
from .events import events
from .functions.encounter_functions import handle_enemy_faint, handle_main_pokemon_faint
from .functions.badges_functions import (
    handle_review_count_achievement,
    check_for_badge,
    receive_badge,
)
from .functions.battle_functions import (
    update_pokemon_battle_status,
    validate_pokemon_status,
    process_battle_data,
)
from .functions.drawing_utils import tooltipWithColour
from .utils import (
    safe_get_random_move,
    play_effect_sound,
    play_sound,
    is_alive,
    random_item,
)
from .functions.ankimon_hooks_to_poke_engine import simulate_battle_with_poke_engine
from .pyobj.error_handler import show_warning_with_traceback

# Shared game state used as bare module globals below. core.bind_runtime_globals()
# points these at the live registry objects after composition (so the module
# imports without `from .singletons import ...`, and thus without aqt). The
# move-selection dialog and the reviewer HUD are reached through services.ui /
# services.reviewer respectively.
main_pokemon = None
enemy_pokemon = None
settings_obj = None
reviewer_obj = None
ankimon_tracker_obj = None
test_window = None
evo_window = None
logger = None
achievements = None
trainer_card = None
translator = None


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
    # Startup-readiness gate: a review that arrives before the async boot has
    # finished cannot be battled. Instead of reading exp's raw
    # ``mw.ankimon_startup_finished`` here, the gate is applied one level up on
    # the seam — ``__init__``'s ``_on_review_card_gated`` forwards to this
    # function only once ``services.startup_finished`` is True (F32) — so this
    # body always runs against a fully-booted registry.
    """Process a review event and advance the current battle state."""
    global _state
    s = _state

    try:
        multiplier = ankimon_tracker_obj.multiplier
        user_attack = random.choice(main_pokemon.attacks) if main_pokemon.attacks else "splash"
        enemy_attack = random.choice(enemy_pokemon.attacks) if enemy_pokemon.attacks else "splash"

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
            # Reward the item every time this trigger fires (main granted it
            # unconditionally). display_item() both rolls+grants the reward (via
            # random_item -> give_item) AND paints the popup; the grant is the
            # actual reward, the QDialog is only the visual. Two reasons we may
            # skip the popup: the user turned it off (it interrupts reviews), or
            # the liveness guard (F24) found the seam window dead — it may have
            # been closed (its C++ object deleted) since boot. In both cases we
            # still roll+grant the reward directly via random_item() so it is
            # never silently dropped; only the popup is skipped.
            item_window = services.test_window
            show_item_popup = settings_obj.get(
                "gui.pop_up_dialog_message_on_item", True
            )
            if show_item_popup and is_alive(item_window):
                try:
                    item_window.display_item()
                except RuntimeError:
                    pass
            else:
                try:
                    random_item()
                except Exception:
                    pass
            if not check_for_badge(achievements, 6):
                receive_badge(6, achievements)

        try:
            cash_interval = int(settings_obj.get("trainer.cash_reward_interval", 10))
            cash_amount = int(settings_obj.get("trainer.cash_reward_amount", 100))
        except (ValueError, TypeError):
            cash_interval = 10
            cash_amount = 100
        if cash_interval > 0 and total_reviews % cash_interval == 0:
            from datetime import date
            today_str = str(date.today())
            last_reward_date = settings_obj.get("trainer.last_cash_reward_date", "")
            cash_earned_today = settings_obj.get("trainer.cash_earned_today", 0)
            
            if last_reward_date != today_str:
                cash_earned_today = 0
                settings_obj.set("trainer.last_cash_reward_date", today_str)
            
            if cash_earned_today < 400:
                allowed_amount = min(cash_amount, 400 - cash_earned_today)
                if allowed_amount > 0:
                    settings_obj.set("trainer.cash_earned_today", cash_earned_today + allowed_amount)
                    settings_obj.set("trainer.cash", settings_obj.get("trainer.cash") + allowed_amount)
                    trainer_card.cash = settings_obj.get("trainer.cash")
                    try:
                        from .singletons import notify_stats_changed
                        notify_stats_changed()
                    except Exception:
                        pass

        if battle_sounds == True and ankimon_tracker_obj.general_card_count_for_battle == 1:
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
                    # Real dialog under Anki (QtPresenter); scripted/None headless.
                    chosen = services.ui.choose_move(main_pokemon.attacks)
                    if chosen:
                        user_attack = chosen

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
                # abs() must be taken BEFORE zeroing, or the heal tooltip always
                # shows +0 instead of the recovered magnitude.
                heals_to_user += abs(true_dmg_from_enemy_move)
                true_dmg_from_enemy_move = 0
            if true_dmg_from_user_move < 0:
                heals_to_opponent += abs(true_dmg_from_user_move)
                true_dmg_from_user_move = 0

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

            # Observable turn outcome for the agent harness.
            events.emit(
                "battle",
                user=main_pokemon.name,
                enemy=enemy_pokemon.name,
                user_move=user_attack,
                enemy_move=enemy_attack,
                dmg_to_enemy=int(true_dmg_from_user_move),
                dmg_to_user=int(true_dmg_from_enemy_move),
                user_hp=int(main_pokemon.hp),
                enemy_hp=int(enemy_pokemon.hp),
                multiplier=multiplier,
            )

            if true_dmg_from_enemy_move > 0 and multiplier < 1:
                reviewer_obj.myseconds = settings_obj.compute_special_variable("animate_time")
                tooltipWithColour(f" -{true_dmg_from_enemy_move} HP ", "#F06060", x=-200)
                play_effect_sound(settings_obj, "HurtNormal")

            if true_dmg_from_user_move > 0:
                reviewer_obj.seconds = settings_obj.compute_special_variable("animate_time")
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
                tooltipWithColour(f" {sign}{int(heals_to_user)} HP ", heal_color, x=-250)

            if int(heals_to_opponent) != 0:
                heal_color = "#68FA94" if heals_to_opponent > 0 else "#F06060"
                sign = "+" if heals_to_opponent > 0 else ""
                tooltipWithColour(f" {sign}{int(heals_to_opponent)} HP ", heal_color, x=250)

            if enemy_pokemon.hp < 1:
                enemy_pokemon.hp = 0
                # Liveness guards (F24): resolve both windows fresh from the
                # registry and only touch/pass them while their C++ objects are
                # alive. A dead window becomes None so the faint handler's own
                # None-checks (new_pokemon / display paths) take over instead of
                # raising "wrapped C/C++ object deleted".
                faint_window = services.test_window
                live_faint_window = faint_window if is_alive(faint_window) else None
                if live_faint_window is not None:
                    try:
                        live_faint_window.display_battle()
                    except RuntimeError:
                        live_faint_window = None
                evo = services.evo_window
                handle_enemy_faint(
                    main_pokemon,
                    enemy_pokemon,
                    s.collected_pokemon_ids,
                    live_faint_window,
                    evo if is_alive(evo) else None,
                    reviewer_obj,
                    logger,
                    achievements,
                )
                s.mutator_full_reset = 1

        if cry_counter == 10 and battle_sounds is True:
            play_sound(enemy_pokemon.id, settings_obj)

        if main_pokemon.hp < 1:
            # Liveness guard (F24): hand the faint handler a live window or None
            # (new_pokemon already None-checks before painting).
            main_faint_window = services.test_window
            handle_main_pokemon_faint(
                main_pokemon,
                enemy_pokemon,
                main_faint_window if is_alive(main_faint_window) else None,
                reviewer_obj,
                translator,
            )
            s.mutator_full_reset = 1

        reviewer_obj.refresh_hud()
        # Liveness guard (F24): is_alive replaces the bare None-check so a
        # deleted-but-non-None widget can't raise on the end-of-turn repaint.
        final_window = services.test_window
        if is_alive(final_window) and enemy_pokemon.hp > 0:
            try:
                final_window.display_battle()
            except RuntimeError:
                pass
    except Exception as e:
        show_warning_with_traceback(
            exception=e, message="An error occurred in reviewer:"
        )
