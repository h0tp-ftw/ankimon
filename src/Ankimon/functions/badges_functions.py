import orjson

from ..resources import badgebag_path

def populate_achievements_from_badges(achievements):
    # name change for clarification
    try:
        with open(badgebag_path, "rb") as f:
            badge_list = orjson.loads(f.read())
            for badge_num in badge_list:
                achievements[str(badge_num)] = True
    except (FileNotFoundError, orjson.JSONDecodeError):
        # If file doesn't exist or is empty, just return the initial achievements
        pass
    return achievements

def check_for_badge(achievements, rec_badge_num):
    return achievements.get(str(rec_badge_num), False)

def save_badges(badges_collection):
    with open(badgebag_path, 'wb') as f:
        f.write(orjson.dumps(badges_collection))

def receive_badge(badge_num,achievements):
    achievements[str(badge_num)] = True
    badges_collection = []
    for num in range(1,69):
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