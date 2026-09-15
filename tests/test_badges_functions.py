"""
Tests for badges_functions — the post-registry testing story.

Contrast this file with tests/test_encounter_functions.py and
tests/test_database_manager.py: those must stub ``aqt``, ``anki`` and a dozen
internal ``Ankimon.*`` modules in ``sys.modules`` by hand before they can even
import the code under test, and that mock list rots whenever an import changes.

Because badges_functions.py now reads its database from the aqt-free ``services``
registry instead of ``from aqt import mw``, this file needs none of that. A plain
import (the Ankimon namespace comes from conftest.py) and ``services.db = FakeDB()``
is the whole setup. No Anki runtime, no sys.modules surgery.
"""

import json

import pytest

# Plain imports. Binding the module objects here (rather than re-importing inside
# each test) also makes the file robust to other test modules mutating
# sys.modules during collection.
from Ankimon.services import services
from Ankimon.functions import badges_functions as bf


class FakeDB:
    """Stand-in for AnkimonDB exposing only what badges_functions touches."""

    def __init__(self, migrated=True, badges=None):
        self._migrated = migrated
        self._badges = badges if badges is not None else []
        self.saved = []

    def is_migrated(self):
        return self._migrated

    def get_all_badges(self):
        return list(self._badges)

    def save_badge(self, key, value):
        self.saved.append((key, value))


class FailOnceBadgeDB(FakeDB):
    """Raise on the first badge write, then persist normally."""

    def __init__(self):
        super().__init__(migrated=True)
        self.failures_remaining = 1

    def save_badge(self, key, value):
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise RuntimeError("forced badge persistence failure")
        super().save_badge(key, value)


class FakeCollection:
    def __init__(self, note_id):
        self.db = self
        self.note_id = note_id

    def scalar(self, query, card_id):
        assert "SELECT nid FROM cards" in query
        return self.note_id


@pytest.fixture(autouse=True)
def clean_registry():
    """Isolate each test: empty registry before and after."""
    services.reset()
    yield
    services.reset()


def test_get_achieved_badges_returns_only_achieved():
    services.db = FakeDB(
        migrated=True,
        badges=[
            {"badge_id": 1, "achieved": True},
            {"badge_id": 2, "achieved": 0},      # not achieved
            {"badge_id": 5, "achieved": "true"},
        ],
    )
    assert bf.get_achieved_badges() == [1, 5]


def test_get_achieved_badges_falls_back_to_json_when_not_migrated(tmp_path, monkeypatch):
    services.db = FakeDB(migrated=False)
    badge_file = tmp_path / "badges.json"
    badge_file.write_text(json.dumps([7, 9]))
    # badgebag_path was imported into the module's namespace; redirect it.
    monkeypatch.setattr(bf, "badgebag_path", str(badge_file))

    assert bf.get_achieved_badges() == [7, 9]


def test_receive_badge_marks_and_persists():
    db = FakeDB(migrated=True)
    services.db = db
    achievements = {str(i): False for i in range(1, 69)}

    result = bf.receive_badge(3, achievements)

    assert result["3"] is True
    assert ("3", {"id": 3, "achieved": True}) in db.saved


def test_handle_review_count_achievement_awards_milestone():
    services.db = FakeDB(migrated=True)
    achievements = {str(i): False for i in range(1, 69)}

    result = bf.handle_review_count_achievement(100, achievements)

    assert result["1"] is True  # 100 reviews -> badge 1


def test_handle_review_count_achievement_ignores_non_milestone():
    services.db = FakeDB(migrated=True)
    achievements = {str(i): False for i in range(1, 69)}

    result = bf.handle_review_count_achievement(150, achievements)

    assert all(value is False for value in result.values())


def test_check_for_badge_is_pure():
    assert bf.check_for_badge({"5": True}, 5) is True
    assert bf.check_for_badge({"5": True}, 6) is False


def test_badge_11_candidate_survives_failed_persistence_and_retries(monkeypatch):
    services.db = FailOnceBadgeDB()
    achievements = {str(i): False for i in range(1, 69)}
    pending = {"42"}
    saved_candidate_states = []

    monkeypatch.setattr(
        bf,
        "get_pending_badge_11_candidates",
        lambda db: set(pending),
    )

    def save_candidates(db, candidates):
        pending.clear()
        pending.update(candidates)
        saved_candidate_states.append(set(candidates))

    monkeypatch.setattr(bf, "save_pending_badge_11_candidates", save_candidates)
    col = FakeCollection(note_id=42)

    bf.check_and_award_badge_11_on_review(
        col, services.db, achievements, card_id=7
    )

    assert achievements["11"] is False
    assert pending == {"42"}
    assert saved_candidate_states == []

    bf.check_and_award_badge_11_on_review(
        col, services.db, achievements, card_id=7
    )

    assert achievements["11"] is True
    assert pending == set()
    assert saved_candidate_states == [set()]
