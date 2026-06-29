"""Shared snapshot + ease helpers used by both the Tier-1 and Tier-2 drivers."""

from __future__ import annotations

EASE_TO_GRADE = {1: "again", 2: "hard", 3: "good", 4: "easy"}
GRADE_ALIASES = {"again": 1, "hard": 2, "good": 3, "easy": 4}


def normalize_ease(ease) -> int:
    """Accept 1-4 or 'again'/'hard'/'good'/'easy' -> Anki ease int (1-4)."""
    if isinstance(ease, str):
        return GRADE_ALIASES.get(ease.lower(), 3)
    return int(ease)


def grade_for(ease) -> str:
    return EASE_TO_GRADE.get(normalize_ease(ease), "good")


def snapshot(services) -> dict:
    """A JSON-serialisable snapshot of the world, from the service registry."""
    s = services
    mp, ep, tr, db, st = (
        s.main_pokemon, s.enemy_pokemon, s.tracker, s.db, s.settings,
    )

    def pk(p):
        return {
            "name": p.name, "id": p.id, "level": p.level,
            "hp": int(p.hp), "max_hp": int(p.max_hp), "xp": int(p.xp),
            "status": p.battle_status, "attacks": list(p.attacks),
            "shiny": bool(p.shiny), "tier": p.tier,
        }

    try:
        ids = sorted(db.get_all_pokemon_ids())
    except Exception:
        ids = []
    try:
        count = db.get_pokemon_count()
    except Exception:
        count = len(ids)

    return {
        "main": pk(mp),
        "enemy": pk(ep),
        "tracker": {
            "encounter": tr.pokemon_encounter,
            "cards_round": tr.cards_battle_round,
            "multiplier": tr.multiplier,
            "caught": tr.caught,
            "card_streak": tr.card_streak,
        },
        "collection": {"count": count, "ids": ids},
        "trainer": {
            "name": s.trainer_card.trainer_name,
            "level": s.trainer_card.level,
            "xp": s.trainer_card.xp,
            "cash": st.get("trainer.cash"),
        },
    }
