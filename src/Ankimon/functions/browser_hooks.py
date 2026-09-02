"""
Browser hooks for Badge 11 detection and tracking.

This module monitors browser operations that affect card suspension status and
tag modifications. It detects when cards are unsuspended or have their 'leech'
tag removed, which are prerequisites for earning Badge 11. The hooks trigger
the badge eligibility check after relevant operations, updating the candidate
set for future review-based badge awarding.

For efficiency, only operations that can affect Badge 11 eligibility trigger
the expensive check. The module supports reload safety by preventing duplicate
hook registrations during add-on reloads.
"""

from aqt import gui_hooks, mw

from ..services import services
from .badges_functions import check_unleeched_cards

# Reload safety (F31): track registered handlers on services to prevent duplicate registration.
_HANDLER_RECORD = "_browser_hook_handlers"


def on_operation_did_execute(changes, _handler):
    """Refresh Badge 11 snapshots after card or note state changes.

    Anki's public hook supplies an ``OpChanges`` object rather than an operation
    name. Suspension changes set ``card``; tag edits set ``note_text``. Ignore
    unrelated operations so ordinary UI refreshes do not scan the collection.
    """
    if not (getattr(changes, "card", False) or getattr(changes, "note_text", False)):
        return

    try:
        check_unleeched_cards(
            services.col if services.col is not None else mw.col,
            services.db,
            getattr(services, "achievements", None),
        )
    except Exception:
        # Badge tracking must never interrupt Anki's operation-completion path.
        pass


def register_browser_hooks():
    """
    Register browser hooks for Badge 11 detection.
    
    This function uses Anki's public ``operation_did_execute`` hook so card
    suspension and note-tag changes are observed immediately in the same session.
    
    Implements reload safety (F31) by removing previously registered hooks
    before appending new ones. This prevents duplicate processing when the
    add-on is reloaded during the same Anki session.
    
    The hooks are stored in the services registry to survive module re-execution
    and enable proper cleanup during reloads.
    """
    # Remove previous registration first (F31 pattern)
    for hook, handler in getattr(services, _HANDLER_RECORD, ()):
        try:
            hook.remove(handler)
        except (ValueError, AttributeError):
            # Handler may already be removed - that's fine
            pass

    handlers = []
    operation_hook = getattr(gui_hooks, "operation_did_execute", None)
    if operation_hook is not None:
        handlers.append((operation_hook, on_operation_did_execute))

    # Register all handlers
    for hook, handler in handlers:
        hook.append(handler)
    
    # Store the handlers for reload safety
    setattr(services, _HANDLER_RECORD, handlers)
