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


def answerCard_before(filter, reviewer, card):
    utils.answBtnAmt = reviewer.mw.col.sched.answerButtons(card)
    return filter


def answerCard_after(rev, card, ease):
    maxEase = rev.mw.col.sched.answerButtons(card)
    if ease == 1:
        ankimon_tracker_obj.review("again")
    elif ease == maxEase - 2:
        ankimon_tracker_obj.review("hard")
    elif ease == maxEase - 1:
        ankimon_tracker_obj.review("good")
    elif ease == maxEase:
        ankimon_tracker_obj.review("easy")
    else:
        tooltip("Error in ColorConfirmation: Couldn't interpret ease")
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
