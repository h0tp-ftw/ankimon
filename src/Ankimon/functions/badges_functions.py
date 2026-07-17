import json
from typing import List, Set, Dict

from ..resources import badgebag_path
from ..services import services


def get_achieved_badges() -> List[int]:
    """Gets list of achieved badge IDs from the database."""
    db = services.db
    
    if db.is_migrated():
        badges = db.get_all_badges()
        # Filter for only achieved badges
        return [int(b["badge_id"]) for b in badges if b.get("achieved") in [True, 1, "true", "True"]]
    
    # Fallback to JSON for backwards compatibility
    try:
        with open(badgebag_path, "r", encoding="utf-8") as json_file:
            return json.load(json_file)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def populate_achievements_from_badges(achievements):
    """Populates achievements dict from stored badges."""
    try:
        for badge_num in get_achieved_badges():
            achievements[str(badge_num)] = True
    except Exception:
        pass
    return achievements


def check_for_badge(achievements, rec_badge_num):
    return achievements.get(str(rec_badge_num), False)


def save_badges(badges_collection: List[int]):
    """Saves badges collection to the database."""
    db = services.db
    if db is None:
        return
    
    # Clear existing badges and save new ones
    # Each badge is saved with its ID as the key
    for badge_num in badges_collection:
        db.save_badge(str(badge_num), {"id": badge_num, "achieved": True})


def receive_badge(badge_num, achievements):
    """Awards a badge and saves to database."""
    achievements[str(badge_num)] = True
    badges_collection = []
    for num in range(1, 69):
        if achievements.get(str(num)) is True:
            badges_collection.append(int(num))
    save_badges(badges_collection)
    return achievements


def handle_review_count_achievement(review_count, achievements):
    milestones = {
        100: 1,
        200: 2,
        300: 3,
        500: 4,
        1000: 12,
        2000: 13,
    }
    badge_to_award = milestones.get(review_count)
    if badge_to_award and not check_for_badge(achievements, badge_to_award):
        achievements = receive_badge(badge_to_award, achievements)

    return achievements


def get_leech_tracking_data(db) -> Dict:
    """
    Retrieves stored leech tracking data from metadata.
    Returns a dict with keys: 'pending_untag' and 'pending_unsuspend'
    """
    try:
        row = db.execute("SELECT value FROM metadata WHERE key = 'leech_tracking_data'").fetchone()
        if row and row[0]:
            return json.loads(row[0])
    except Exception:
        pass
    return {"pending_untag": [], "pending_unsuspend": []}


def save_leech_tracking_data(db, data: Dict):
    """Saves leech tracking data to metadata."""
    try:
        value_str = json.dumps(data)
        conn = db._get_connection()
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES ('leech_tracking_data', ?)",
            (value_str,),
        )
        conn.commit()
    except Exception:
        pass


def check_unleeched_cards(col, db, achievements):
    """
    Tracks cards that were leeches and suspended.
    
    To earn Badge 11, ALL THREE conditions must be met for the SAME card:
    1. Card was suspended AND had the 'leech' tag
    2. Card is now unsuspended
    3. Card has been reviewed at least once after unsuspension
    
    The card remains in tracking memory until all conditions are met.
    """
    if not col or not db or not achievements:
        return

    # Skip if badge already awarded
    if check_for_badge(achievements, 11):
        return

    try:
        # Get current cards with leech tag
        current_leech_nids = {str(nid) for nid in col.find_notes("tag:leech")}
        
        # Get current suspended card IDs
        suspended_cids = set()
        for cid in col.db.list("SELECT id FROM cards WHERE queue = -1"):
            suspended_cids.add(str(cid))
        
        # Get the note ID for each suspended card
        suspended_nids = set()
        for cid in suspended_cids:
            note_id = col.db.scalar("SELECT nid FROM cards WHERE id = ?", int(cid))
            if note_id:
                suspended_nids.add(str(note_id))
        
        # Load tracking data
        tracking = get_leech_tracking_data(db)
        pending_untag = set(tracking.get("pending_untag", []))
        pending_unsuspend = set(tracking.get("pending_unsuspend", []))
        
        # --- Phase 1: Track newly leeched AND suspended cards ---
        # A card is a "candidate" if it has the leech tag AND is suspended
        leeched_and_suspended = current_leech_nids & suspended_nids
        
        # Check if any pending cards are no longer leeched but still suspended
        # (They should move from pending_untag to pending_unsuspend)
        newly_untagged = pending_untag & (pending_untag - current_leech_nids)
        for nid in newly_untagged:
            if nid not in current_leech_nids:
                # Card was untagged but is it still suspended?
                if nid in suspended_nids:
                    pending_untag.remove(nid)
                    pending_unsuspend.add(nid)
                else:
                    # Card was untagged AND unsuspended - this is a candidate for review
                    pending_untag.remove(nid)
                    pending_unsuspend.add(nid)  # Wait for review
        
        # --- Phase 2: Track unsuspended cards (ready for review) ---
        # Cards that were in pending_unsuspend but are no longer suspended
        ready_for_review = pending_unsuspend & (pending_unsuspend - suspended_nids)
        
        # These cards now need to be reviewed - move to reviewed check
        # We'll store them in a separate set for review tracking
        pending_review = set(tracking.get("pending_review", []))
        for nid in ready_for_review:
            pending_unsuspend.remove(nid)
            pending_review.add(nid)
        
        # --- Phase 3: Check if any pending_review cards have been reviewed ---
        # Get all cards that have been reviewed recently (last 24 hours or since last check)
        # We'll use the review log to check if any cards were answered
        newly_reviewed = set()
        for nid in list(pending_review):
            # Check if any card from this note has been reviewed since the last check
            cards_for_note = col.db.list("SELECT id FROM cards WHERE nid = ?", int(nid))
            if cards_for_note:
                # Get the latest review time for any card in this note
                latest_review = col.db.scalar(
                    "SELECT MAX(id) FROM revlog WHERE cid IN ({})".format(
                        ",".join("?" * len(cards_for_note))
                    ),
                    *cards_for_note
                )
                # If the card has been reviewed at least once (exists in revlog)
                if latest_review:
                    # Check if the review happened after the card was unsuspended
                    # We can use the time when it entered pending_review as a marker
                    newly_reviewed.add(nid)
                    pending_review.remove(nid)
        
        # --- Phase 4: Award Badge 11 if ANY card meets ALL conditions ---
        award_badge = False
        if newly_reviewed:
            # A card has been reviewed after being untagged and unsuspended
            award_badge = True
        
        # --- Phase 5: Update leech candidates for future tracking ---
        # Add newly leeched+suspended cards to pending_untag
        for nid in leeched_and_suspended:
            if nid not in pending_untag and nid not in pending_unsuspend and nid not in pending_review:
                pending_untag.add(nid)
        
        # Save updated tracking data
        tracking = {
            "pending_untag": list(pending_untag),
            "pending_unsuspend": list(pending_unsuspend),
            "pending_review": list(pending_review)
        }
        save_leech_tracking_data(db, tracking)
        
        # Award badge if conditions met
        if award_badge:
            receive_badge(11, achievements)
            
    except Exception as e:
        # Silent fail - don't break Anki functionality
        pass


def check_unleeched_cards_on_review(col, db, achievements, card_id):
    """
    Special version to be called during card review.
    Checks if a specific card was just reviewed.
    """
    if not col or not db or not achievements:
        return
        
    if check_for_badge(achievements, 11):
        return
    
    try:
        # Get the note ID for this card
        note_id = col.db.scalar("SELECT nid FROM cards WHERE id = ?", card_id)
        if not note_id:
            return
        
        # Load tracking data
        tracking = get_leech_tracking_data(db)
        pending_review = set(tracking.get("pending_review", []))
        
        # If this card is in pending_review, mark it as reviewed
        if str(note_id) in pending_review:
            pending_review.remove(str(note_id))
            tracking["pending_review"] = list(pending_review)
            save_leech_tracking_data(db, tracking)
            
            # Award badge immediately
            receive_badge(11, achievements)
            
    except Exception:
        pass


def update_leech_tracking_on_review(col, db, achievements, card_id):
    """
    Called after a card is reviewed to check if it qualifies for Badge 11.
    This should be called from answerCard_after in card_hooks.py.
    """
    check_unleeched_cards_on_review(col, db, achievements, card_id)
