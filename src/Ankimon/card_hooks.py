from aqt import gui_hooks, mw, utils
from aqt.utils import tooltip
import logging

from .services import services
from .singletons import ankimon_tracker_obj, reviewer_obj

# Set up logger for this module
logger = logging.getLogger(__name__)


def on_show_question(Card):
    ankimon_tracker_obj.start_card_timer()


def on_show_answer(Card):
    ankimon_tracker_obj.stop_card_timer()


def on_reviewer_did_show_question(card):
    reviewer_obj.update_life_bar(mw.reviewer, None, None)


# card id -> (card.type, answer-button count) captured in
# reviewer_will_answer_card. reviewer_did_answer_card runs only after Anki
# has answered the card and reloaded it, so the card object in that hook
# already has the post-answer type (a new card answered Easy, or a learning
# card that graduates, is Review / type 2 by then).
_PRE_ANSWER_STATE = {}

# Anki card.type: 0 New, 1 Learning, 2 Review, 3 Relearning.
_NEW_AND_LEARNING = (0, 1)


def answerCard_before(filter, reviewer, card):
    button_count = reviewer.mw.col.sched.answerButtons(card)
    utils.answBtnAmt = button_count
    cid = getattr(card, "id", None)
    if cid is not None:
        _PRE_ANSWER_STATE[cid] = (getattr(card, "type", None), button_count)
    return filter


def _consume_pre_answer_state(rev, card):
    """Return (card_type, answer_button_count) from before this answer.

    Falls back to the card as passed in when the will-answer hook did not
    run (direct callers, tests). That fallback sees the post-answer card
    inside a real Anki review, which is why the snapshot exists.
    """
    cid = getattr(card, "id", None)
    if cid is not None and cid in _PRE_ANSWER_STATE:
        return _PRE_ANSWER_STATE.pop(cid)
    try:
        button_count = rev.mw.col.sched.answerButtons(card)
    except Exception:
        button_count = 4
    return getattr(card, "type", 2), button_count


def _grade_for_ease(ease, max_ease):
    """Map a pressed ease to a grade using the button count shown at answer time.

    Anki's labels are Again/Good for 2 buttons, Again/Good/Easy for 3, and
    Again/Hard/Good/Easy for 4. A 2-button Good is ease 2; treating that as
    Easy (the old ``ease == maxEase`` branch) scores it at double Good.
    """
    if max_ease <= 2:
        if ease == 1:
            return "again"
        if ease == 2:
            return "good"
        return None
    if ease == 1:
        return "again"
    if ease == max_ease - 2:
        return "hard"
    if ease == max_ease - 1:
        return "good"
    if ease == max_ease:
        return "easy"
    return None


def _ignore_learning_enabled():
    settings = services.settings
    if settings is None:
        return False
    try:
        return bool(settings.get("battle.ignore_learning_cards", False))
    except Exception:
        return False


def answerCard_after(rev, card, ease):
    card_type, max_ease = _consume_pre_answer_state(rev, card)
    grade = _grade_for_ease(ease, max_ease)
    if grade is None:
        tooltip("Error in ColorConfirmation: Couldn't interpret ease")
    elif _ignore_learning_enabled() and card_type in _NEW_AND_LEARNING:
        # Neutralize the damage window only. Streak and grade tallies keep
        # the button the user actually pressed. Relearning (type 3) is excluded.
        ankimon_tracker_obj.review(grade, multiplier_grade="good")
    else:
        ankimon_tracker_obj.review(grade)

    ankimon_tracker_obj.reset_card_timer()

    # Mobile-review de-dupe (F29): this review was just turned into battle
    # progress on desktop, so record its revlog id (and card id) to keep it out
    # of the mobile queue. Without this the exclusion set is never populated and
    # detect_mobile_reviews() re-queues every desktop review as a "mobile"
    # review after the next sync, double-processing it (double XP / catches).
    try:
        from .functions.mobile_sync import record_desktop_review
        revlog_id = rev.mw.col.db.scalar(
            "SELECT MAX(id) FROM revlog WHERE cid = ?", card.id
        )
        record_desktop_review(revlog_id, card_id=card.id)
    except Exception:
        pass
    
    # Check for Badge 11 on card review
    # If this card was previously unsuspended OR had its leech tag removed,
    # and is now being reviewed, award Badge 11
    try:
        from .functions.badges_functions import update_leech_tracking_on_review
        update_leech_tracking_on_review(
            rev.mw.col,
            services.db,
            getattr(services, 'achievements', None),
            card.id
        )
    except Exception as e:
        # Preserve review-flow protection by not raising exceptions
        # Log the error for diagnosability
        logger.error(
            f"Failed to update leech tracking for card {card.id}: {e}",
            exc_info=True
        )

# Reload safety (F31): a second boot in the same session must not leave the
# reviewer hooks registered twice, or every review would be double-counted.
# The registration record lives on the services registry — which, unlike a
# module-level flag, survives a re-execution of this module — and it stores
# the exact handler objects that were appended: after a re-exec the functions
# above are NEW objects, so only the stored originals can still be found and
# removed from gui_hooks. Re-registering therefore swaps the handlers for the
# current module's instead of stacking a second set.
_HANDLER_RECORD = "_card_hook_handlers"


def register_card_hooks():
    # Drop the previous registration first. No-op on a first boot (no record);
    # on a re-boot the stored pairs are removed so the appends below cannot
    # double-register. gui_hooks' remove() tolerates already-absent callbacks.
    for hook, handler in getattr(services, _HANDLER_RECORD, ()):
        hook.remove(handler)

    handlers = (
        (gui_hooks.reviewer_did_show_question, on_show_question),
        (gui_hooks.reviewer_did_show_answer, on_show_answer),
        (gui_hooks.reviewer_did_show_question, on_reviewer_did_show_question),
        (gui_hooks.reviewer_will_answer_card, answerCard_before),
        (gui_hooks.reviewer_did_answer_card, answerCard_after),
    )
    for hook, handler in handlers:
        hook.append(handler)
    setattr(services, _HANDLER_RECORD, handlers)
