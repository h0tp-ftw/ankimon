"""Issue #973: the active (main) Pokemon learns level-up moves through the same
checklist flow as XP Share recipients — free slots fill silently, a full set
gets ONE ``choose_moveset`` prompt per level, never one dialog per move.

Mirrors the ``save_main_pokemon_progress`` harness in ``test_levelup_fixes.py``
but keeps the real Gen 9 learnset so the scenarios match the issue:
charmander ember@4, bulbasaur poisonpowder+sleeppowder@15.
"""

import importlib
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

FULL_BULBASAUR_SET = ["tackle", "growl", "vinewhip", "razorleaf"]


@pytest.fixture
def main_levelup(monkeypatch):
    """Wire ``save_main_pokemon_progress`` with mocked services and a scripted UI."""
    ef = importlib.import_module("Ankimon.functions.encounter_functions")
    services = importlib.import_module("Ankimon.services").services

    # Earlier test files can leave these modules replaced by MagicMocks in
    # sys.modules (and ``ef`` bound to the mock's attributes). Re-import the
    # real ones for this test and rebind the function under test to them.
    for name in (
        "Ankimon.functions.learnset_retrieval",
        "Ankimon.functions.trainer_functions",
    ):
        if isinstance(sys.modules.get(name), mock.Mock):
            monkeypatch.delitem(sys.modules, name)
    tf = importlib.import_module("Ankimon.functions.trainer_functions")
    monkeypatch.setattr(ef, "learn_levelup_moves", tf.learn_levelup_moves)
    monkeypatch.setattr(tf, "services", services)
    # ...and the learnset loader may point at a stub path with a cached empty
    # table. Use the bundled Gen 9 learnset and drop the cache on both sides.
    lr = importlib.import_module("Ankimon.functions.learnset_retrieval")
    real_learnsets = (
        Path(__file__).resolve().parents[1] / "src" / "Ankimon" / "data_files" / "learnsets.json"
    )
    monkeypatch.setattr(lr, "learnset_path", real_learnsets, raising=False)
    lr.clear_learnset_cache()
    # The "learned" tooltip is UI output; under the suite's mocked Qt the real
    # painter raises, which would mask the learning result under test.
    monkeypatch.setattr(tf, "tooltipWithColour", lambda *args, **kwargs: None)
    monkeypatch.setattr(tf, "format_move_name", lambda move: str(move))

    settings = mock.MagicMock()
    settings.get = lambda k, d=None: {
        "misc.remove_level_cap": False,
        "gui.pop_up_dialog_message_on_defeat": False,
        "gui.hud_styling": False,
        "gui.reviewer_text_message_box": False,
        "gui.reviewer_text_message_box_time": 4,
    }.get(k, d)
    monkeypatch.setattr(ef, "settings_obj", settings, raising=False)
    monkeypatch.setattr(ef, "translator", mock.MagicMock(), raising=False)
    ef.translator.translate = mock.MagicMock(return_value="msg")
    monkeypatch.setattr(ef, "ankimon_db", mock.MagicMock(), raising=False)
    # Another test file leaves ``ef.services`` replaced by a MagicMock for the
    # rest of the session; the function must talk to the real registry here.
    monkeypatch.setattr(ef, "services", services, raising=False)
    monkeypatch.setattr(ef, "check_evolution_for_pokemon", mock.MagicMock(return_value=None))
    monkeypatch.setattr(
        ef, "check_friendship_evolution_for_pokemon", mock.MagicMock(return_value=None)
    )

    ui = mock.Mock()
    ui.choose_moveset.return_value = None
    monkeypatch.setattr(services, "settings", settings)
    monkeypatch.setattr(services, "db", mock.MagicMock())
    monkeypatch.setattr(services, "logger", mock.MagicMock())
    monkeypatch.setattr(services, "ui", ui)
    monkeypatch.setattr(services, "translator", None, raising=False)

    def run(species, growth, level, attacks):
        """Level ``species`` from ``level`` to ``level + 1`` with ``attacks`` stored."""
        stored = {
            "individual_id": "iid",
            "name": species.capitalize(),
            "attacks": list(attacks),
            "ev": {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0},
            "held_item": None,
            "pokemon_defeated": 0,
            "stats": {},
            "level": level,
            "xp": 0,
            "friendship": 100,
        }
        services.db.get_main_pokemon.return_value = stored
        main = types.SimpleNamespace(
            name=species.capitalize(), growth_rate=growth, level=level, xp=0,
            individual_id="iid", id=1, everstone=False, friendship=100, stats={},
            ev=stored["ev"], hp=100, held_item=None, pokemon_defeated=0,
            tier="Normal", is_favorite=False, evolution_rejected=False,
            invalidate_cp_cache=lambda: None, update_stats=lambda: None,
        )
        enemy = types.SimpleNamespace(ev_yield={})
        # Use the reference the function itself resolves, so an earlier test
        # file that left ``ef.find_experience_for_level`` mocked cannot skew
        # the amount needed to cross exactly one level.
        exp = int(ef.find_experience_for_level(growth, level, False)) + 1
        logger = mock.MagicMock()
        ef.save_main_pokemon_progress(main, enemy, exp, {}, logger, None)
        assert main.level == level + 1
        return stored

    yield types.SimpleNamespace(ef=ef, ui=ui, run=run)
    lr.clear_learnset_cache()


def test_free_slot_learns_move_without_prompt(main_levelup):
    """Charmander 3→4 with two moves learns Ember silently."""
    m = main_levelup
    stored = m.run("charmander", "medium-slow", 3, ["scratch", "growl"])
    assert stored["attacks"] == ["scratch", "growl", "ember"]
    m.ui.choose_moveset.assert_not_called()


def test_full_moveset_prompts_once_with_every_new_move(main_levelup):
    """Bulbasaur 14→15 with four moves gets ONE prompt listing both new moves."""
    m = main_levelup
    m.ui.choose_moveset.return_value = ["tackle", "vinewhip", "poisonpowder", "sleeppowder"]
    stored = m.run("bulbasaur", "medium-slow", 14, FULL_BULBASAUR_SET)
    m.ui.choose_moveset.assert_called_once_with(
        "Bulbasaur", FULL_BULBASAUR_SET, ["poisonpowder", "sleeppowder"]
    )
    # Retained moves keep their slots; new moves drop into the vacated ones.
    assert stored["attacks"] == ["tackle", "poisonpowder", "vinewhip", "sleeppowder"]


def test_declining_prompt_keeps_current_moves(main_levelup):
    """"Keep current moves" leaves the set untouched but the level-up still happens."""
    m = main_levelup
    stored = m.run("bulbasaur", "medium-slow", 14, FULL_BULBASAUR_SET)
    assert stored["attacks"] == FULL_BULBASAUR_SET
    m.ui.choose_moveset.assert_called_once()


def test_bulk_resolve_never_prompts(main_levelup, monkeypatch):
    """Replaying a phone backlog opens no dialog and keeps the moveset."""
    m = main_levelup
    utils = importlib.import_module("Ankimon.utils")
    monkeypatch.setattr(utils, "in_bulk_resolve", True, raising=False)
    stored = m.run("bulbasaur", "medium-slow", 14, FULL_BULBASAUR_SET)
    assert stored["attacks"] == FULL_BULBASAUR_SET
    m.ui.choose_moveset.assert_not_called()


def test_evolution_check_sees_moves_learned_on_this_level(main_levelup):
    """A move learned on the level is visible to the move-based evolution gate."""
    m = main_levelup
    m.run("charmander", "medium-slow", 3, ["scratch", "growl"])
    kwargs = m.ef.check_evolution_for_pokemon.call_args.kwargs
    assert "ember" in (kwargs.get("current_attacks") or [])
