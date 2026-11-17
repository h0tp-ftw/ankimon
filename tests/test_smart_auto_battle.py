"""Smart Battle (battle.smart_battle): move choice, team switch, level
balancing and the XP penalty. Pure-logic tests: matchup/move scoring is stubbed so each case pins
one decision rule."""

import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

_src = Path(__file__).parent.parent / "src"


def _ensure_pkg(name):
    mod = sys.modules.get(name)
    if not isinstance(mod, types.ModuleType) or not getattr(mod, "__path__", None):
        stub = types.ModuleType(name)
        stub.__path__ = [str(_src / name.replace(".", "/"))]
        stub.__package__ = name
        sys.modules[name] = stub


for _pkg in ("Ankimon", "Ankimon.functions", "Ankimon.pyobj"):
    _ensure_pkg(_pkg)

for _name in [n for n in list(sys.modules) if n.startswith("Ankimon.poke_engine")]:
    _m = sys.modules[_name]
    if not isinstance(_m, types.ModuleType) or getattr(_m, "__file__", None) is None:
        del sys.modules[_name]

from Ankimon.functions import smart_battle as sb  # noqa: E402
from Ankimon.functions import smart_move_selection as sms  # noqa: E402
from Ankimon.functions import smart_team_switch as sts  # noqa: E402


def _mon(ind_id, level, hp=50, **kw):
    return SimpleNamespace(individual_id=ind_id, level=level, hp=hp, **kw)


class _Settings(dict):
    def get(self, key, default=None):
        return super().get(key, default)


# ---------------------------------------------------------------- move choice


def test_choose_best_move_picks_highest_damage(monkeypatch):
    monkeypatch.setattr(
        sms, "score_moves",
        lambda *a, **k: [("tackle", 10.0, False), ("thunderbolt", 40.0, False),
                         ("growl", 0.0, True)],
    )
    assert sms.choose_best_move(None, None, fallback="tackle") == "thunderbolt"


def test_choose_best_move_never_falls_back_to_self_ko(monkeypatch):
    # Both moves score as status (Explosion is excluded), so nothing damages.
    monkeypatch.setattr(
        sms, "score_moves",
        lambda *a, **k: [("explosion", 0.0, True), ("splash", 0.0, True)],
    )
    assert sms.choose_best_move(None, None, fallback="explosion") == "splash"


def test_choose_best_move_keeps_self_ko_when_it_is_the_only_move(monkeypatch):
    monkeypatch.setattr(sms, "score_moves", lambda *a, **k: [("explosion", 0.0, True)])
    assert sms.choose_best_move(None, None, fallback="explosion") == "explosion"


def test_choose_best_move_returns_fallback_on_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("engine failure")

    monkeypatch.setattr(sms, "score_moves", boom)
    assert sms.choose_best_move(None, None, fallback="tackle") == "tackle"


def test_live_state_reused_only_for_the_same_individual():
    state = SimpleNamespace(_ankimon_main_individual_id="abc")
    assert sms._same_individual(state, SimpleNamespace(individual_id="abc"))
    # Same species, different individual: must not reuse.
    assert not sms._same_individual(state, SimpleNamespace(individual_id="xyz"))
    # A plain engine state carries no identity: never reuse.
    assert not sms._same_individual(SimpleNamespace(), SimpleNamespace(individual_id="abc"))


# ---------------------------------------------------------------- team switch


def test_pick_best_keeps_current_on_close_calls(monkeypatch):
    scores = {"a": 1.0, "b": 1.1}  # b is better, but under the 1.15 margin
    monkeypatch.setattr(sts, "matchup_score", lambda p, e: scores[p.individual_id])
    cands = [("a", _mon("a", 50)), ("b", _mon("b", 50))]
    assert sts.pick_best(cands, None, current_id="a") is None


def test_pick_best_switches_past_the_margin(monkeypatch):
    scores = {"a": 1.0, "b": 1.2}
    monkeypatch.setattr(sts, "matchup_score", lambda p, e: scores[p.individual_id])
    cands = [("a", _mon("a", 50)), ("b", _mon("b", 50))]
    assert sts.pick_best(cands, None, current_id="a") == "b"


def test_pick_best_skips_fainted(monkeypatch):
    monkeypatch.setattr(sts, "matchup_score", lambda p, e: None if p.hp <= 0 else 1.0)
    cands = [("a", _mon("a", 50, hp=0)), ("b", _mon("b", 50))]
    assert sts.pick_best(cands, None, current_id="a") == "b"


# ---------------------------------------------------------------- level balance


def test_balance_benches_members_far_above_the_lowest():
    team = [("miraidon", _mon("m", 106)), ("x", _mon("x", 80)), ("y", _mon("y", 83))]
    kept = [i for i, _ in sts.balance_by_level(team, 5)]
    assert kept == ["x", "y"]


def test_balance_ignores_fainted_members_for_the_floor():
    team = [("low", _mon("l", 10, hp=0)), ("a", _mon("a", 80)), ("b", _mon("b", 84))]
    kept = [i for i, _ in sts.balance_by_level(team, 5)]
    assert kept == ["a", "b"]


def test_balance_off_when_gap_is_zero():
    team = [("m", _mon("m", 106)), ("x", _mon("x", 80))]
    assert sts.balance_by_level(team, 0) == team


def test_benched_current_pokemon_is_replaced_without_margin(monkeypatch):
    # Miraidon is out and has by far the best matchup, but it is 26 levels
    # ahead, so the best of the balanced members goes out instead.
    scores = {"m": 9.0, "x": 0.5, "y": 0.7}
    monkeypatch.setattr(sts, "matchup_score", lambda p, e: scores[p.individual_id])
    team = [("m", _mon("m", 106)), ("x", _mon("x", 80)), ("y", _mon("y", 82))]
    balanced = sts.balance_by_level(team, 5)
    assert sts.pick_best(balanced, None, current_id="m") == "y"


@pytest.mark.parametrize("raw, expected", [
    (None, 5), ("7", 7), (-3, 0), (500, 100), ("junk", 5),
])
def test_level_gap_setting_is_clamped(raw, expected):
    settings = _Settings()
    if raw is not None:
        settings["battle.team_level_gap"] = raw
    assert sts.level_gap(settings) == expected


# ---------------------------------------------------------------- switch guards


def test_no_switch_while_the_active_pokemon_is_fainted(monkeypatch):
    # Double faint: the faint handler must see the fainted Pokémon first.
    monkeypatch.setattr(sts.services, "db", object(), raising=False)
    settings = _Settings({"battle.smart_battle": True})
    fainted = _mon("a", 50, hp=0)
    assert sts.smart_switch_for_new_enemy(fainted, _mon("e", 50), settings) is None


def test_no_switch_when_smart_battle_is_off(monkeypatch):
    monkeypatch.setattr(sts.services, "db", object(), raising=False)
    settings = _Settings({"battle.smart_battle": False})
    assert sts.smart_switch_for_new_enemy(_mon("a", 50), _mon("e", 50), settings) is None


def test_switch_applies_the_new_members_held_item(monkeypatch):
    calls = {}

    class _Main:
        individual_id = "a"
        level = 50
        hp = 40
        held_item = "leftovers"
        display_name = "Pikachu"

        def update_stats(self, **data):
            # Mirrors the real method: keys the record lacks are left alone.
            for k, v in data.items():
                setattr(self, k, v)

        def reset_bonuses(self):
            pass

    main = _Main()
    record = {"individual_id": "b", "name": "eevee", "level": 48, "hp": 30}

    monkeypatch.setattr(sts.services, "db", object(), raising=False)
    monkeypatch.setattr(sts, "_team_ids", lambda s: ["a", "b"])
    monkeypatch.setattr(
        sts, "_load_candidate",
        lambda m, i: (_mon("b", 48, hp=30), dict(record)),
    )
    monkeypatch.setattr(sts, "pick_best", lambda *a, **k: "b")

    fake_update = types.ModuleType("Ankimon.functions.update_main_pokemon")
    fake_update._apply_loaded_hp = lambda p, d: None
    fake_update.save_main_pokemon = lambda p: calls.setdefault("saved", p.held_item)
    fake_battle = types.ModuleType("Ankimon.battle_loop")
    fake_battle._cancel_main_faint_deferral = lambda: None
    monkeypatch.setitem(sys.modules, "Ankimon.functions.update_main_pokemon", fake_update)
    monkeypatch.setitem(sys.modules, "Ankimon.battle_loop", fake_battle)

    settings = _Settings({"battle.smart_battle": True, "battle.team_level_gap": 0})
    msg = sts.smart_switch_for_new_enemy(main, _mon("e", 50), settings)

    assert msg and "Lv. 48" in msg
    # The team record had no held item, so the old Leftovers must not carry over.
    assert main.held_item is None
    assert calls["saved"] is None


# ---------------------------------------------------------------- toggle + XP


@pytest.mark.parametrize("raw, on", [
    (True, True), ("true", True), (1, True), ("1", True),
    (False, False), ("false", False), (0, False), (None, False),
])
def test_smart_battle_toggle_reads_stored_values(raw, on):
    assert sb.smart_battle_enabled(_Settings({"battle.smart_battle": raw})) is on


def test_smart_battle_is_off_by_default():
    assert sb.smart_battle_enabled(_Settings()) is False


@pytest.mark.parametrize("choose, smart, half", [
    (False, False, False),
    (True, False, True),
    (False, True, True),
    (True, True, True),  # not stacked: still just 50%
])
def test_xp_penalty_matches_allow_choosing_moves(choose, smart, half):
    settings = _Settings({
        "controls.allow_to_choose_moves": choose,
        "battle.smart_battle": smart,
    })
    assert sb.reduced_xp(settings) is half
