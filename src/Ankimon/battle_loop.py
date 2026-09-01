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

# Set while a manual-mode double faint is waiting on the player's enemy
# catch/defeat choice, so a later battle round doesn't register a second
# resolution callback for the same pending main faint.
_main_faint_deferred = False
# The callback currently registered on the catch/defeat hook buckets. Kept at
# module scope so an ABANDONED deferral can be disarmed from outside _resolve()
# itself -- see _cancel_main_faint_deferral().
_main_faint_resolver = None


def _cancel_main_faint_deferral():
    """Disarm a pending main-faint deferral and unregister its callback.

    ``_resolve()`` used to be the only thing that ever cleared
    ``_main_faint_deferred`` or removed itself from the hook buckets. So a
    deferral the player never answered -- they closed the Ankimon Window
    instead of the death screen, and the next round healed the main Pokemon
    through the immediate branch in on_review_card() -- stayed armed forever.
    The stale callback then fired on the next unrelated catch/defeat and ran
    handle_main_pokemon_faint() against a FULL-HP Pokemon: a bogus faint
    message and sound, a spurious ``faint`` event, and reset_bonuses()
    silently wiping that battle's stat boosts.

    Safe to call unconditionally -- a no-op when nothing is armed.
    """
    global _main_faint_deferred, _main_faint_resolver

    _main_faint_deferred = False
    resolver, _main_faint_resolver = _main_faint_resolver, None
    if resolver is None:
        return

    from . import hook_registry

    for bucket in (
        hook_registry.catch_pokemon_hooks,
        hook_registry.defeat_pokemon_hooks,
    ):
        try:
            bucket.remove(resolver)
        except ValueError:
            pass


def _defer_main_faint_until_enemy_resolved(
    main_pokemon, enemy_pokemon, reviewer_obj, translator
):
    """Manual-mode double faint: run handle_main_pokemon_faint() only after the
    player answers the enemy catch/defeat screen that handle_enemy_faint() left
    open. Doing it now would heal the main Pokemon and spawn a fresh encounter
    over that screen before the choice is made.

    Returns True when a deferral is in effect -- newly armed here, or already
    armed by an earlier round -- and False when one could not be armed at all.
    On False the caller MUST handle the faint immediately; a deferral nothing
    can resolve would strand the main Pokemon at 0 HP forever.
    """
    global _main_faint_deferred, _main_faint_resolver
    if _main_faint_deferred:
        return True

    try:
        from . import hook_registry
    except Exception:
        # hook_registry imports aqt, so this is the headless / no-Anki case.
        # Nothing there could ever fire the resolver, and deferring anyway
        # would park the main Pokemon at 0 HP with no way back -- so report
        # failure and let the caller handle the faint immediately.
        return False

    def _resolve():
        if not _main_faint_deferred:
            return
        # Clears the flag AND unregisters this callback from both buckets
        # before any of the work below, so an exception here cannot leave the
        # deferral armed with a callback nothing will ever remove.
        _cancel_main_faint_deferral()
        resolve_window = services.test_window
        handle_main_pokemon_faint(
            main_pokemon,
            enemy_pokemon,
            resolve_window if is_alive(resolve_window) else None,
            reviewer_obj,
            translator,
            spawn_replacement=False,
        )
        try:
            reviewer_obj.refresh_hud()
        except Exception:
            pass
        # The catch/defeat that triggered this already ran new_pokemon(), which
        # composited the fresh encounter's intro frame while main_pokemon.hp
        # was still 0. Repaint so the healed main HP bar is visible now rather
        # than only after the next answered card.
        try:
            if is_alive(resolve_window) and resolve_window.current_view == "battle":
                resolve_window.force_display_battle()
        except Exception:
            pass

    _main_faint_deferred = True
    _main_faint_resolver = _resolve
    hook_registry.add_catch_pokemon_hook(_resolve)
    hook_registry.add_defeat_pokemon_hook(_resolve)
    return True
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
        # Amulet Coin / Lucky Incense: both double prize money in the mainline
        # games (yes, identically — held either doubles the payout, not
        # stacking if you somehow had both). There's no "trainer battle" here
        # to key off of, so the effect applies to Ankimon's own cash-reward
        # interval instead.
        if getattr(main_pokemon, "held_item", None) in ("amulet-coin", "luck-incense"):
            cash_amount *= 2
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

        # This turn's battle-log line and per-side damage. Only the
        # cards-per-round batch below actually runs the poke_engine
        # simulation, so on every other review they keep these defaults —
        # bound here rather than read back out of locals() by name further
        # down, where a rename would silently disable the message box and
        # both shakes instead of raising.
        formatted_battle_log = None
        true_dmg_from_user_move = 0
        true_dmg_from_enemy_move = 0

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

            encounter_replaced = False
            if enemy_pokemon.hp < 1:
                enemy_pokemon.hp = 0
                # Liveness guards (F24): resolve both windows fresh from the
                # registry and only touch/pass them while their C++ objects are
                # alive. A dead window becomes None so the faint handler's own
                # None-checks (new_pokemon / display paths) take over instead of
                # raising "wrapped C/C++ object deleted".
                faint_window = services.test_window
                live_faint_window = faint_window if is_alive(faint_window) else None
                # Only when the window is actually showing the battle view.
                # Manual mode (the shipped default) never sets
                # faint_processed, so while the catch/defeat screen is up this
                # block is re-entered on every completed round — and with
                # paint_now that would flash the battle scene over the death
                # screen each time before handle_enemy_faint() restored it.
                if (
                    live_faint_window is not None
                    and getattr(live_faint_window, "current_view", None) == "battle"
                ):
                    try:
                        # The killing blow's own frame: this turn's log line
                        # over the enemy sprite tipped on its side at 0 HP.
                        # It has to carry message_text — the end-of-turn
                        # repaint further down is skipped on exactly the
                        # turns a side faints, so without it the message box
                        # would show the PREVIOUS turn's text on the frame
                        # where the enemy actually died, and the last line of
                        # every battle would never be shown at all.
                        # force_ + paint_now because the faint handler
                        # replaces this frame (fresh encounter, or the death
                        # screen) inside this same call stack, well within
                        # the debounce window and long before Qt would paint
                        # on its own. No shake: the animation's timer steps
                        # would land on the NEXT encounter and jitter the
                        # wrong sprites.
                        live_faint_window.force_display_battle(
                            message_text=formatted_battle_log, paint_now=True
                        )
                    except RuntimeError:
                        live_faint_window = None
                evo = services.evo_window
                # handle_enemy_faint() returns True when it replaced
                # enemy_pokemon with a fresh wild encounter (auto-catch/
                # auto-defeat/override/wishlist all do, via new_pokemon(),
                # which already painted that encounter's own intro frame) —
                # same reasoning as handle_main_pokemon_faint below: skip the
                # end-of-turn display_battle() so it doesn't immediately
                # overwrite that intro frame with this turn's stale text.
                # Manual mode (False) shows the death/catch screen instead,
                # already excluded from that repaint via the enemy_pokemon.hp
                # > 0 check further down.
                encounter_replaced = bool(
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
                )
                s.mutator_full_reset = 1
        else:
            encounter_replaced = False

        if cry_counter == 10 and battle_sounds is True:
            play_sound(enemy_pokemon.id, settings_obj)

        if main_pokemon.hp < 1:
            main_pokemon.hp = 0
            # Liveness guard (F24): hand the faint handler a live window or None
            # (new_pokemon already None-checks before painting).
            main_faint_window = services.test_window
            live_main_faint_window = (
                main_faint_window if is_alive(main_faint_window) else None
            )
            # Not on a double faint: handle_enemy_faint() has already replaced
            # enemy_pokemon with a fresh wild encounter, so this frame would
            # show the NEXT enemy at full HP standing over the tipped-out main
            # Pokémon under the previous fight's log line.
            #
            # The current_view check mirrors the enemy-faint branch above, and
            # a same-turn double faint is exactly when it earns its keep: in
            # manual mode handle_enemy_faint() puts the death/catch screen up
            # and returns False, so encounter_replaced stays False and this
            # paint_now repaint would flash the battle scene over the screen
            # the player still has to answer.
            if (
                live_main_faint_window is not None
                and not encounter_replaced
                and getattr(live_main_faint_window, "current_view", None) == "battle"
            ):
                try:
                    # Mirror of the enemy-faint frame above, and it has to
                    # happen HERE: handle_main_pokemon_faint() heals the main
                    # Pokémon back to full HP as one of its first statements,
                    # so this is the only moment its sprite can be drawn
                    # tipped over at 0 HP, and the only moment the killing
                    # blow's log line can reach the message box before
                    # new_pokemon() paints the replacement encounter.
                    live_main_faint_window.force_display_battle(
                        message_text=formatted_battle_log, paint_now=True
                    )
                except RuntimeError:
                    live_main_faint_window = None
            # Manual-mode double faint: handle_enemy_faint() left the enemy
            # catch/defeat decision to the player instead of replacing the
            # encounter. Running handle_main_pokemon_faint() now would heal
            # main and spawn a fresh encounter before they answer it, so defer
            # the main-faint bookkeeping until the choice completes.
            #
            # "Enemy still fainted AND not replaced" is the whole condition.
            # This also used to require the Ankimon Window to be alive and on
            # its "death" view, which silently excluded the case where that
            # window is CLOSED -- the decision is still pending there, just via
            # the reviewer-side Catch/Defeat buttons (mw.catchpokemon /
            # mw.defeatpokemon) rather than the popup. In that case the faint
            # was handled immediately, new_pokemon() refreshed enemy_pokemon in
            # place to full HP, and the player's later Catch hit
            # CatchPokemonHook's `enemy_pokemon.hp < 1` guard and silently did
            # nothing -- the fainted Pokemon was simply lost.
            #
            # Auto modes cannot reach here: they replace the encounter, so
            # encounter_replaced is True by this point.
            enemy_decision_pending = (
                not encounter_replaced and enemy_pokemon.hp < 1
            )
            if enemy_decision_pending and _defer_main_faint_until_enemy_resolved(
                main_pokemon, enemy_pokemon, reviewer_obj, translator
            ):
                pass  # the resolver will run handle_main_pokemon_faint() later
            else:
                # Handling the faint HERE supersedes any deferral still armed
                # from an earlier round the player walked away from (they
                # closed the Ankimon Window rather than answering its death
                # screen). Disarm it first, or its stale callback fires a
                # phantom faint on the next unrelated catch/defeat.
                _cancel_main_faint_deferral()
                handle_main_pokemon_faint(
                    main_pokemon,
                    enemy_pokemon,
                    live_main_faint_window,
                    reviewer_obj,
                    translator,
                )
            s.mutator_full_reset = 1
            # Either a fresh encounter now stands in enemy_pokemon's place, or
            # (deferred double faint) the enemy death screen is up — neither
            # wants the end-of-turn repaint below.
            # handle_main_pokemon_faint() heals main and, via new_pokemon(),
            # replaces enemy_pokemon with a fresh wild encounter AND already
            # painted that encounter's own intro frame. Below, the final
            # display_battle() call would otherwise immediately repaint over
            # that intro frame with THIS turn's now-stale battle-log text and
            # shake flags — both describe the fight that just ended against
            # the enemy that no longer exists.
            encounter_replaced = True

        reviewer_obj.refresh_hud()
        # Liveness guard (F24): is_alive replaces the bare None-check so a
        # deleted-but-non-None widget can't raise on the end-of-turn repaint.
        final_window = services.test_window
        if not encounter_replaced and is_alive(final_window) and enemy_pokemon.hp > 0:
            # The ATTACKER shakes, not the one hit: main dealing damage means
            # main attacked (shake main's sprite), enemy dealing damage means
            # enemy attacked (shake enemy's sprite) — both can be true in the
            # same turn if neither side fainted the other first.
            try:
                final_window.display_battle(
                    message_text=formatted_battle_log,
                    shake_enemy=bool(true_dmg_from_enemy_move),
                    shake_main=bool(true_dmg_from_user_move),
                )
            except RuntimeError:
                pass
    except Exception as e:
        show_warning_with_traceback(
            exception=e, message="An error occurred in reviewer:"
        )
