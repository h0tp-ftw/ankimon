"""PR #961 follow-up: committed feedback and atomic reward ownership."""

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from test_pokemon_attacks_atomicity import attacks_db, _on_disk  # noqa: F401
from test_xp_share_moves import BULBASAUR, CHARMANDER, xp_share  # noqa: F401
from test_mobile_sync import friendship_companion  # noqa: F401


def test_tooltip_only_announces_the_committed_choice(xp_share, monkeypatch):
    s = xp_share
    s.save("holder", BULBASAUR, 14, ["tackle", "growl", "vinewhip"])
    shown = []
    monkeypatch.setattr(
        s.trainer, "tooltipWithColour", lambda message, *_: shown.append(message)
    )
    chosen = ["tackle", "growl", "vinewhip", "sleeppowder"]

    def choose(*_):
        assert not s.db._get_connection().in_transaction
        return chosen

    s.ui.choose_moveset.side_effect = choose
    s.grant(s.xp_to_reach(BULBASAUR, 14, 15))

    assert s.db.get_pokemon("holder")["attacks"] == chosen
    assert any("Sleep Powder" in message for message in shown)
    assert not any("Poison Powder" in message for message in shown)


@pytest.mark.parametrize("encoded", [False, True])
def test_duplicate_slots_with_a_short_pool_learn_without_an_impossible_dialog(
    xp_share, encoded
):
    s = xp_share
    original = ["Scratch", "Growl", "scratch", "growl"]
    s.save("holder", CHARMANDER, 3, json.dumps(original) if encoded else original)
    s.grant(s.xp_to_reach(CHARMANDER, 3, 4))
    saved = s.db.get_pokemon("holder")
    assert saved["level"] == 4
    assert saved["attacks"] == ["Scratch", "Growl", "ember"]
    s.ui.choose_moveset.assert_not_called()


@pytest.mark.parametrize("outer", [False, True])
def test_reward_failure_preserves_record_and_outer_work(attacks_db, outer):
    db, expected = attacks_db
    conn = db._get_connection()
    before = _on_disk(db)
    if outer:
        conn.execute("BEGIN").close()
        conn.execute("INSERT INTO metadata VALUES ('outer-reward', 'keep')").close()
    with pytest.raises(RuntimeError, match="reward calculation failed"):
        with db.pokemon_reward_transaction("recipient") as pokemon:
            pokemon.update(xp=999, attacks=["growl"])
            raise RuntimeError("reward calculation failed")
    assert _on_disk(db) == before
    assert conn.in_transaction is outer
    if outer:
        assert (
            conn.execute(
                "SELECT value FROM metadata WHERE key='outer-reward'"
            ).fetchone()[0]
            == "keep"
        )
        conn.commit()
    assert db.get_pokemon("recipient") == expected


def test_reward_commit_failure_cannot_leak_into_a_later_write(attacks_db):
    db, _ = attacks_db
    conn = db._get_connection()
    conn.execute("PRAGMA busy_timeout=40").close()
    before = _on_disk(db)
    reader = sqlite3.connect(db.db_path)
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT data FROM captured_pokemon").fetchall()
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            with db.pokemon_reward_transaction("recipient") as pokemon:
                pokemon["xp"] += 25
        assert not conn.in_transaction
    finally:
        reader.rollback()
        reader.close()
    db.set_user_data("after-failed-reward", True)
    assert _on_disk(db) == before


def test_bulk_reward_update_stays_rollbackable(attacks_db):
    db, _ = attacks_db
    before = _on_disk(db)
    conn = db._get_connection()
    conn._disable_commit = True
    try:
        with db.pokemon_reward_transaction("recipient") as pokemon:
            pokemon["xp"] += 7
        assert conn.in_transaction
        assert _on_disk(db) == before
        conn.rollback()
    finally:
        conn._disable_commit = False
        conn.rollback()
    assert _on_disk(db) == before


def test_reward_cannot_recreate_a_deleted_recipient_or_cross_save_generation(
    attacks_db,
):
    db, _ = attacks_db
    identity = db.identity_token()
    assert db.close()
    before = _on_disk(db)
    with db.pokemon_reward_transaction(
        "recipient", expected_db_identity=identity
    ) as pokemon:
        assert pokemon is None
    assert _on_disk(db) == before
    db.delete_pokemon("recipient")
    with db.pokemon_reward_transaction("recipient") as pokemon:
        assert pokemon is None
    assert _on_disk(db) == []


def test_mobile_calculation_failure_leaves_stored_reward_unchanged(
    xp_share, monkeypatch
):
    import importlib

    s = xp_share
    s.save("holder", BULBASAUR, 14, ["tackle", "growl"])
    mobile = importlib.import_module("Ankimon.functions.mobile_sync")
    original = s.db.get_pokemon("holder")

    def fail(record, *_args, **_kwargs):
        record.update(level=99, xp=123, attacks=["solarbeam"])
        raise RuntimeError("failed after calculating reward")

    monkeypatch.setattr(mobile, "_calculate_mobile_reward", fail)
    with pytest.raises(RuntimeError, match="after calculating"):
        mobile._attribute_xp_and_evs_to_companion("holder", 5, {}, s.settings, db=s.db)
    with sqlite3.connect(s.db.db_path) as connection:
        data = connection.execute(
            "SELECT data FROM captured_pokemon WHERE individual_id='holder'"
        ).fetchone()[0]
    assert json.loads(data) == original


@pytest.mark.tier2
def test_xp_share_followup_real_qt():
    """Run native dialogs/threads in a fresh process, outside suite import mocks."""
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    # Exercise production QueryOp dispatch in the child, not the in-process
    # pytest seam that intentionally runs mobile work synchronously.
    env.pop("PYTEST_CURRENT_TEST", None)
    available = subprocess.run(
        [
            sys.executable,
            "-c",
            "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('PyQt6') else 77)",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    if available.returncode == 77:
        pytest.skip("Requires the optional real-Qt Tier-2 environment")
    assert available.returncode == 0, available.stdout + available.stderr
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "tests/proofs/proof_pr_961_tier2.py"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_failed_mobile_commit_does_not_advance_the_live_pokemon(friendship_companion):
    s = friendship_companion
    main = s.create(active=True)
    before = main.to_dict()
    stored = s.db.get_pokemon(main.individual_id)
    conn = s.db._get_connection()
    conn.execute("PRAGMA busy_timeout=40").close()
    reader = sqlite3.connect(s.db.db_path)
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT data FROM captured_pokemon").fetchall()
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            s.sync._attribute_xp_and_evs_to_companion(
                main.individual_id,
                10000,
                {"hp": 1},
                s.services.settings,
                db=s.db,
            )
    finally:
        reader.rollback()
        reader.close()
    assert main.to_dict() == before
    assert s.db.get_pokemon(main.individual_id) == stored


@pytest.mark.parametrize("failure", ["notification", "prompt", "patch-commit"])
def test_post_commit_mobile_ui_failure_keeps_exactly_one_reward(
    friendship_companion, monkeypatch, failure
):
    import importlib

    s = friendship_companion
    main = s.create(active=True, friendship=70)
    moves = ["thundershock", "growl", "tailwhip", "quickattack"]
    if failure == "notification":
        moves = moves[:3]
    main.attacks = list(moves)
    s.db.save_main_pokemon(main.to_dict())
    functions = importlib.import_module("Ankimon.functions.pokemon_functions")
    drawing = importlib.import_module("Ankimon.functions.drawing_utils")
    xp = int(functions.find_experience_for_level(main.growth_rate, 50, False)) + 1
    monkeypatch.setattr(
        functions, "get_levelup_move_for_pokemon", lambda *_: ["thunder"]
    )
    monkeypatch.setattr(s.sync.random, "randint", lambda *_: 7)
    conn = s.db._get_connection()
    conn.execute("PRAGMA busy_timeout=40").close()
    reader = sqlite3.connect(s.db.db_path)
    statements = []

    def choose(*_):
        assert not conn.in_transaction
        assert s.db.get_pokemon(main.individual_id)["level"] == 51
        if failure == "prompt":
            raise RuntimeError("move dialog closed unexpectedly")
        if failure == "patch-commit":
            reader.execute("BEGIN")
            reader.execute("SELECT data FROM captured_pokemon").fetchall()
            conn._conn.set_trace_callback(statements.append)
        return "growl"

    def tooltip(message, *_):
        if "learned" in message:
            raise RuntimeError("tooltip delivery failed")

    monkeypatch.setattr(
        s.services, "ui", SimpleNamespace(choose_attack_to_replace=choose)
    )
    monkeypatch.setattr(drawing, "tooltipWithColour", tooltip)
    try:
        s.sync._attribute_xp_and_evs_to_companion(
            main.individual_id,
            xp,
            {"hp": 1},
            s.services.settings,
            db=s.db,
        )
    finally:
        reader.rollback()
        reader.close()
        conn._conn.set_trace_callback(None)
    saved = s.db.get_pokemon(main.individual_id)
    assert saved["level"] == main.level == 51
    assert saved["xp"] == main.xp == 1
    assert saved["friendship"] == main.friendship == 77
    assert saved["ev"]["hp"] == main.ev["hp"] == 1
    expected = moves + ["thunder"] if failure == "notification" else moves
    assert saved["attacks"] == main.attacks == expected
    assert not conn.in_transaction
    if failure == "patch-commit":
        assert "COMMIT" in statements and "ROLLBACK" in statements
    s.db.set_user_data("after-mobile-ui-failure", True)
    assert s.db.get_pokemon(main.individual_id) == saved
