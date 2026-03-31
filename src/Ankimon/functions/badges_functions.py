import json
from typing import List

from ..resources import badgebag_path
from aqt import mw


def get_achieved_badges() -> List[int]:
    """Gets list of achieved badge IDs from the database."""
    db = mw.ankimon_db
    
    if db.is_migrated():
        badges = db.get_all_badges()
        return [int(b.get("badge_id", b.get("id", 0))) for b in badges]
    
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
    db = mw.ankimon_db
    
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
    }
    badge_to_award = milestones.get(review_count)
    if badge_to_award and not check_for_badge(achievements, badge_to_award):
        achievements = receive_badge(badge_to_award, achievements)

    return achievements