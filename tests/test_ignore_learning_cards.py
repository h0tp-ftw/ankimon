"""Ignore Learning Cards (#846) must use the card from before Anki answers it.

``reviewer_did_answer_card`` reloads the card, so a new card answered Easy
(and a learning card that graduates) is already Review (type 2) when the
hook runs. The setting also has to leave the pressed button in the grade
tallies, and mobile replays have to apply the same Good contribution.
"""

import sys
import types
from unittest.mock import MagicMock

import pytest

for _name in (
    "aqt",
    "aqt.qt",
    "aqt.utils",
    "aqt.gui_hooks",
    "anki",
    "anki.hooks",
    "PyQt6",
    "PyQt6.QtCore",
    "PyQt6.QtWidgets",
    "PyQt6.QtGui",
):
    sys.modules.setdefault(_name, MagicMock())


class _Tracker:
    def __init__(self):
        self.calls = []

    def review(self, grade, multiplier_grade=None):
        self.calls.append((grade, multiplier_grade))

    def reset_card_timer(self):
        pass


class _Sched:
    def __init__(self, buttons):
        self.buttons = buttons

    def answerButtons(self, _card):
        return self.buttons


class _Settings:
    def __init__(self, enabled):
        self.enabled = enabled

    def get(self, key, default=None):
        if key == "battle.ignore_learning_cards":
            return self.enabled
        return default


def _reviewer(sched):
    db = types.SimpleNamespace(scalar=lambda *_a, **_k: None)
    col = types.SimpleNamespace(sched=sched, db=db)
    return types.SimpleNamespace(mw=types.SimpleNamespace(col=col))


@pytest.fixture
def hooks(monkeypatch):
    stub = types.ModuleType("Ankimon.singletons")
    tracker = _Tracker()
    stub.ankimon_tracker_obj = tracker
    stub.reviewer_obj = MagicMock()
    monkeypatch.setitem(sys.modules, "Ankimon.singletons", stub)
    monkeypatch.delitem(sys.modules, "Ankimon.card_hooks", raising=False)

    import Ankimon.card_hooks as ch

    ch._PRE_ANSWER_STATE.clear()
    yield ch, tracker
    ch._PRE_ANSWER_STATE.clear()


def _enable(monkeypatch, enabled):
    from Ankimon.services import services

    monkeypatch.setattr(services, "settings", _Settings(enabled), raising=False)


def test_graduating_easy_uses_pre_answer_type(hooks, monkeypatch):
    """New + Easy graduates to Review before the did-answer hook. Still Good."""
    ch, tracker = hooks
    _enable(monkeypatch, True)
    sched = _Sched(4)
    reviewer = _reviewer(sched)
    card = types.SimpleNamespace(id=7, type=0)

    ch.answerCard_before((True, 4), reviewer, card)
    # Anki's success callback reloads the card before reviewer_did_answer_card.
    card.type = 2
    ch.answerCard_after(reviewer, card, ease=4)

    assert tracker.calls == [("easy", "good")]
    assert ch._PRE_ANSWER_STATE == {}


@pytest.mark.parametrize(
    "buttons,ease,grade",
    [
        (4, 1, "again"),
        (4, 2, "hard"),
        (4, 3, "good"),
        (4, 4, "easy"),
        (3, 1, "again"),
        (3, 2, "good"),
        (3, 3, "easy"),
        (2, 1, "again"),
        (2, 2, "good"),
    ],
)
def test_ease_labels_match_anki_buttons(hooks, buttons, ease, grade):
    ch, _tracker = hooks
    assert ch._grade_for_ease(ease, buttons) == grade


def test_graduating_good_is_not_reread_as_hard(hooks, monkeypatch):
    """A 2-button Good (ease 2) must not become Hard if the card then has 4 buttons."""
    ch, tracker = hooks
    _enable(monkeypatch, False)
    sched = _Sched(2)
    reviewer = _reviewer(sched)
    card = types.SimpleNamespace(id=8, type=1)

    ch.answerCard_before((True, 2), reviewer, card)
    card.type = 2
    sched.buttons = 4
    ch.answerCard_after(reviewer, card, ease=2)

    assert tracker.calls == [("good", None)]


def test_again_on_learning_counts_as_good_for_damage_only(hooks, monkeypatch):
    ch, tracker = hooks
    _enable(monkeypatch, True)
    sched = _Sched(4)
    reviewer = _reviewer(sched)
    card = types.SimpleNamespace(id=9, type=1)

    ch.answerCard_before((True, 1), reviewer, card)
    ch.answerCard_after(reviewer, card, ease=1)

    assert tracker.calls == [("again", "good")]


def test_relearning_and_review_keep_their_grade(hooks, monkeypatch):
    ch, tracker = hooks
    _enable(monkeypatch, True)
    sched = _Sched(4)
    reviewer = _reviewer(sched)

    relearn = types.SimpleNamespace(id=10, type=3)
    ch.answerCard_before((True, 1), reviewer, relearn)
    ch.answerCard_after(reviewer, relearn, ease=1)

    review = types.SimpleNamespace(id=11, type=2)
    ch.answerCard_before((True, 4), reviewer, review)
    ch.answerCard_after(reviewer, review, ease=4)

    assert tracker.calls == [("again", None), ("easy", None)]


def test_setting_off_keeps_again_on_a_new_card(hooks, monkeypatch):
    ch, tracker = hooks
    _enable(monkeypatch, False)
    sched = _Sched(4)
    reviewer = _reviewer(sched)
    card = types.SimpleNamespace(id=12, type=0)

    ch.answerCard_before((True, 1), reviewer, card)
    card.type = 1
    ch.answerCard_after(reviewer, card, ease=1)

    assert tracker.calls == [("again", None)]


def test_missing_settings_does_not_override(hooks, monkeypatch):
    ch, tracker = hooks
    from Ankimon.services import services

    monkeypatch.setattr(services, "settings", None, raising=False)
    sched = _Sched(4)
    reviewer = _reviewer(sched)
    card = types.SimpleNamespace(id=13, type=0)

    ch.answerCard_before((True, 1), reviewer, card)
    ch.answerCard_after(reviewer, card, ease=1)

    assert tracker.calls == [("again", None)]


def test_unknown_ease_does_not_record_a_grade(hooks, monkeypatch):
    ch, tracker = hooks
    _enable(monkeypatch, True)
    sched = _Sched(4)
    reviewer = _reviewer(sched)
    card = types.SimpleNamespace(id=14, type=0)

    ch.answerCard_before((True, 5), reviewer, card)
    ch.answerCard_after(reviewer, card, ease=5)

    assert tracker.calls == []


def test_multiplier_window_uses_good_but_tallies_keep_again(monkeypatch):
    from Ankimon.pyobj.ankimon_tracker import AnkimonTracker
    from Ankimon.services import services

    monkeypatch.setattr(
        services,
        "db",
        types.SimpleNamespace(get_all_pokemon_ids=lambda: []),
        raising=False,
    )
    tracker = AnkimonTracker(trainer_card=None)
    tracker.cards_until_calc_multiplier = 2
    tracker.review("again", multiplier_grade="good")
    tracker.review("easy")

    assert tracker.card_ratings_count["again"] == 1
    assert tracker.card_ratings_count["easy"] == 1
    assert tracker.card_ratings_count["good"] == 0
    # Again resets the streak; the following Easy starts it at 1.
    assert tracker.card_streak == 1
    # Window was Good (10) + Easy (20), not Again (0) + Easy (20).
    assert tracker.multiplier == pytest.approx(1.5)


def test_mobile_learning_reviews_count_as_good_when_enabled():
    from Ankimon.functions.mobile_sync import multiplier_from_reviews

    class _SettingsObj:
        def __init__(self, enabled):
            self.enabled = enabled

        def get(self, key, default=None):
            if key == "battle.ignore_learning_cards":
                return self.enabled
            return default

    learning_again = {"ease": 1, "review_type": 0}
    review_easy = {"ease": 4, "review_type": 1}
    relearn_again = {"ease": 1, "review_type": 2}
    reviews = [learning_again, review_easy, relearn_again]

    # Good 10 + Easy 20 + Again 0, over 3 cards * 10.
    assert multiplier_from_reviews(reviews, _SettingsObj(True)) == pytest.approx(1.0)
    # Again 0 + Easy 20 + Again 0.
    assert multiplier_from_reviews(reviews, _SettingsObj(False)) == pytest.approx(
        20 / 30
    )
    # Revlog rows that still use the detector's "type" key.
    assert multiplier_from_reviews(
        [{"ease": 1, "type": 0}], _SettingsObj(True)
    ) == pytest.approx(1.0)
