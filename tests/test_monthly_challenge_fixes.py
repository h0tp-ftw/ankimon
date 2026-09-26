import pytest
import sys
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch, call

# Mock Anki dependencies
sys.modules["aqt"] = MagicMock()
sys.modules["aqt.qt"] = MagicMock()
sys.modules["aqt.utils"] = MagicMock()
sys.modules["aqt.theme"] = MagicMock()
sys.modules["anki"] = MagicMock()
sys.modules["anki.buildinfo"] = MagicMock()
sys.modules["anki.utils"] = MagicMock()
sys.modules["PyQt6"] = MagicMock()
sys.modules["PyQt6.QtWidgets"] = MagicMock()
sys.modules["PyQt6.QtGui"] = MagicMock()
sys.modules["PyQt6.QtCore"] = MagicMock()

# Instead of direct import, use importlib to avoid breaking conftest's module stubbing
_src = Path(__file__).parent.parent / "src"
spec = importlib.util.spec_from_file_location(
    "Ankimon.pyobj.monthly_challenge",
    _src / "Ankimon" / "pyobj" / "monthly_challenge.py",
)
monthly_challenge_module = importlib.util.module_from_spec(spec)
sys.modules["Ankimon.pyobj.monthly_challenge"] = monthly_challenge_module
spec.loader.exec_module(monthly_challenge_module)
check_and_award_monthly_pokemon = (
    monthly_challenge_module.check_and_award_monthly_pokemon
)


class MockLogger:
    def log(self, level, message):
        print(f"[{level}] {message}")


@pytest.fixture
def mock_db():
    db = MagicMock()
    return db


@pytest.fixture
def mock_mw(mock_db):
    # The module reads the database through the services seam
    # (`services.db`); keep mw.ankimon_db set too for any legacy access.
    with (
        patch("Ankimon.pyobj.monthly_challenge.mw") as mw_mock,
        patch("Ankimon.pyobj.monthly_challenge.services.db", mock_db),
    ):
        mw_mock.ankimon_db = mock_db
        yield mw_mock


@pytest.fixture
def mock_requests():
    with patch("Ankimon.pyobj.monthly_challenge.requests.get") as get_mock:
        yield get_mock


@patch("Ankimon.pyobj.monthly_challenge.datetime")
@patch("Ankimon.pyobj.monthly_challenge.add_pokemon_to_collection")
@patch("Ankimon.pyobj.monthly_challenge.show_monthly_challenge_dialog")
@patch("Ankimon.pyobj.monthly_challenge.show_monthly_acceptance_dialog")
def test_rate_this_check(
    acceptance_dialog_mock,
    dialog_mock,
    add_pokemon_mock,
    datetime_mock,
    mock_requests,
    mock_mw,
    mock_db,
):
    logger = MockLogger()

    # Setup datetime to return known values
    dt_mock = MagicMock()
    dt_mock.month = 1
    dt_mock.year = 2024
    datetime_mock.now.return_value = dt_mock

    mock_response = MagicMock()
    mock_response.json.return_value = [
        {
            "month": "January 2024",
            "pokemon": {"name": "TestMon", "id": 1, "individual_id": "test-id"},
        }
    ]
    mock_requests.return_value = mock_response

    # Mock dialog to accept the Pokémon
    dialog_mock.return_value = True

    # Test case 1: user hasn't rated (returns None)
    mock_db.get_user_data.return_value = None
    check_and_award_monthly_pokemon(logger, defer=False)
    mock_requests.assert_not_called()

    # Test case 2: user hasn't rated (returns False)
    mock_db.get_user_data.return_value = False
    check_and_award_monthly_pokemon(logger, defer=False)
    mock_requests.assert_not_called()

    # Test case 3: user rated using old 'true' string
    mock_db.get_user_data.return_value = "true"
    mock_db.get_pokemon.return_value = None  # Pokémon not found yet
    check_and_award_monthly_pokemon(logger, defer=False)
    mock_requests.assert_called_once()
    add_pokemon_mock.assert_called_once()
    acceptance_dialog_mock.assert_called_once()

    mock_requests.reset_mock()
    add_pokemon_mock.reset_mock()
    acceptance_dialog_mock.reset_mock()

    # Test case 4: user rated using new boolean True
    mock_db.get_user_data.return_value = True
    check_and_award_monthly_pokemon(logger, defer=False)
    mock_requests.assert_called_once()
    add_pokemon_mock.assert_called_once()
    acceptance_dialog_mock.assert_called_once()


@patch("Ankimon.pyobj.monthly_challenge.datetime")
@patch("Ankimon.pyobj.monthly_challenge.add_pokemon_to_collection")
@patch("Ankimon.pyobj.monthly_challenge.show_monthly_challenge_dialog")
@patch("Ankimon.pyobj.monthly_challenge.show_monthly_acceptance_dialog")
def test_previous_challenge_pokemon_null_check(
    acceptance_dialog_mock,
    dialog_mock,
    add_pokemon_mock,
    datetime_mock,
    mock_requests,
    mock_mw,
    mock_db,
):
    logger = MockLogger()

    # Setup datetime to return known values
    dt_mock = MagicMock()
    dt_mock.month = 1
    dt_mock.year = 2024
    datetime_mock.now.return_value = dt_mock

    mock_response = MagicMock()
    mock_response.json.return_value = [
        {
            "month": "January 2024",
            "previous_challenge_individual_id": "prev-id",
            "defeat_threshold": 10,
            "pokemon": {"name": "TestMon", "id": 1, "individual_id": "test-id"},
        }
    ]
    mock_requests.return_value = mock_response

    mock_db.get_user_data.return_value = True

    # Mock dialog to accept the Pokémon
    dialog_mock.return_value = True

    # Setup get_pokemon to handle target and previous pokemon
    def get_pokemon_side_effect(individual_id):
        if individual_id == "test-id":
            return None  # Don't have target
        if individual_id == "prev-id":
            return None  # Simulate user missing the previous challenge pokemon
        return None

    mock_db.get_pokemon.side_effect = get_pokemon_side_effect

    # Call the function - it shouldn't crash
    check_and_award_monthly_pokemon(logger, defer=False)

    # Should have added the new pokemon (not shiny)
    add_pokemon_mock.assert_called_once()
    added_pokemon = add_pokemon_mock.call_args[0][0]
    assert added_pokemon["shiny"] is False
    acceptance_dialog_mock.assert_called_once()


@patch("Ankimon.pyobj.monthly_challenge.datetime")
@patch("Ankimon.pyobj.monthly_challenge.add_pokemon_to_collection")
@patch("Ankimon.pyobj.monthly_challenge.show_monthly_challenge_dialog")
@patch("Ankimon.pyobj.monthly_challenge.show_monthly_acceptance_dialog")
def test_previous_challenge_pokemon_has_enough_defeats(
    acceptance_dialog_mock,
    dialog_mock,
    add_pokemon_mock,
    datetime_mock,
    mock_requests,
    mock_mw,
    mock_db,
):
    logger = MockLogger()

    dt_mock = MagicMock()
    dt_mock.month = 1
    dt_mock.year = 2024
    datetime_mock.now.return_value = dt_mock

    mock_response = MagicMock()
    mock_response.json.return_value = [
        {
            "month": "January 2024",
            "previous_challenge_individual_id": "prev-id",
            "defeat_threshold": 10,
            "pokemon": {"name": "TestMon", "id": 1, "individual_id": "test-id"},
        }
    ]
    mock_requests.return_value = mock_response

    mock_db.get_user_data.return_value = True

    # Mock dialog to accept the Pokémon
    dialog_mock.return_value = True

    def get_pokemon_side_effect(individual_id):
        if individual_id == "test-id":
            return None
        if individual_id == "prev-id":
            return {"pokemon_defeated": 15}
        return None

    mock_db.get_pokemon.side_effect = get_pokemon_side_effect

    check_and_award_monthly_pokemon(logger, defer=False)

    add_pokemon_mock.assert_called_once()
    added_pokemon = add_pokemon_mock.call_args[0][0]
    assert added_pokemon["shiny"] is True
    acceptance_dialog_mock.assert_called_once()


@patch("Ankimon.pyobj.monthly_challenge.datetime")
@patch("Ankimon.pyobj.monthly_challenge.add_pokemon_to_collection")
@patch("Ankimon.pyobj.monthly_challenge.show_monthly_challenge_dialog")
def test_monthly_challenge_rejected_status(
    dialog_mock, add_pokemon_mock, datetime_mock, mock_requests, mock_mw, mock_db
):
    """Test that monthly_status == 2 causes early return without awarding Pokémon."""
    logger = MockLogger()

    dt_mock = MagicMock()
    dt_mock.month = 1
    dt_mock.year = 2024
    datetime_mock.now.return_value = dt_mock

    mock_response = MagicMock()
    mock_response.json.return_value = [
        {
            "month": "January 2024",
            "pokemon": {"name": "TestMon", "id": 1, "individual_id": "test-id"},
        }
    ]
    mock_requests.return_value = mock_response

    mock_db.get_user_data.return_value = True

    # Set monthly_status to 2 (rejected) and monthly_challenge_id to current ID
    def get_user_data_side_effect(key, default=None):
        if key == "monthly_challenge":
            return 2
        if key == "monthly_challenge_id":
            return "test-id"  # Match current ID to prevent reset
        return True

    mock_db.get_user_data.side_effect = get_user_data_side_effect
    mock_db.get_pokemon.return_value = None

    check_and_award_monthly_pokemon(logger, defer=False)

    # Should NOT add Pokémon or show dialog
    add_pokemon_mock.assert_not_called()
    dialog_mock.assert_not_called()


@patch("Ankimon.pyobj.monthly_challenge.datetime")
@patch("Ankimon.pyobj.monthly_challenge.add_pokemon_to_collection")
@patch("Ankimon.pyobj.monthly_challenge.show_monthly_challenge_dialog")
def test_monthly_challenge_reconciliation_branch(
    dialog_mock, add_pokemon_mock, datetime_mock, mock_requests, mock_mw, mock_db
):
    """Test reconciliation when Pokémon exists but tracking is stale/missing."""
    logger = MockLogger()

    dt_mock = MagicMock()
    dt_mock.month = 1
    dt_mock.year = 2024
    datetime_mock.now.return_value = dt_mock

    mock_response = MagicMock()
    mock_response.json.return_value = [
        {
            "month": "January 2024",
            "pokemon": {"name": "TestMon", "id": 1, "individual_id": "test-id"},
        }
    ]
    mock_requests.return_value = mock_response

    mock_db.get_user_data.return_value = True

    # Simulate Pokémon exists in collection but tracking is stale
    def get_user_data_side_effect(key, default=None):
        if key == "monthly_challenge_id":
            return "old-stale-id"  # Stale ID
        if key == "monthly_challenge":
            return 0  # Unclaimed status
        return True

    mock_db.get_user_data.side_effect = get_user_data_side_effect

    def get_pokemon_side_effect(individual_id):
        if individual_id == "test-id":
            return {"name": "TestMon", "id": 1}  # Pokémon exists
        return None

    mock_db.get_pokemon.side_effect = get_pokemon_side_effect

    check_and_award_monthly_pokemon(logger, defer=False)

    # Should reconcile tracking values using set_monthly_challenge_state
    mock_db.set_monthly_challenge_state.assert_called_with("test-id", 1)

    # Should NOT add Pokémon (already exists) or show dialog
    add_pokemon_mock.assert_not_called()
    dialog_mock.assert_not_called()


@patch("Ankimon.pyobj.monthly_challenge.datetime")
@patch("Ankimon.pyobj.monthly_challenge.add_pokemon_to_collection")
@patch("Ankimon.pyobj.monthly_challenge.show_monthly_challenge_dialog")
@patch("Ankimon.pyobj.monthly_challenge.show_monthly_acceptance_dialog")
def test_monthly_challenge_add_failure_leaves_status_unclaimed(
    acceptance_dialog_mock,
    dialog_mock,
    add_pokemon_mock,
    datetime_mock,
    mock_requests,
    mock_mw,
    mock_db,
):
    """A failed save leaves the initially unclaimed decision intact."""
    logger = MockLogger()

    dt_mock = MagicMock()
    dt_mock.month = 1
    dt_mock.year = 2024
    datetime_mock.now.return_value = dt_mock

    mock_response = MagicMock()
    mock_response.json.return_value = [
        {
            "month": "January 2024",
            "pokemon": {"name": "TestMon", "id": 1, "individual_id": "test-id"},
        }
    ]
    mock_requests.return_value = mock_response

    mock_db.get_user_data.return_value = True
    mock_db.get_pokemon.return_value = None

    # User accepts the Pokémon
    dialog_mock.return_value = True

    # Simulate add_pokemon_to_collection failure
    add_pokemon_mock.return_value = False

    check_and_award_monthly_pokemon(logger, defer=False)

    # Should have attempted to add Pokémon
    add_pokemon_mock.assert_called_once()

    # Only initialize the new challenge; do not write a failure "rollback"
    # after the save helper, which can open a modal error dialog.
    mock_db.set_monthly_challenge_state.assert_called_once_with("test-id", 0)

    # Should show the challenge dialog (user accepted)
    dialog_mock.assert_called_once()
    # Should NOT show acceptance dialog (since add failed)
    acceptance_dialog_mock.assert_not_called()


@patch("Ankimon.pyobj.monthly_challenge.datetime")
@patch("Ankimon.pyobj.monthly_challenge.add_pokemon_to_collection")
@patch("Ankimon.pyobj.monthly_challenge.show_monthly_challenge_dialog")
@patch("Ankimon.pyobj.monthly_challenge.show_monthly_rejection_dialog")
def test_monthly_challenge_rejection_sets_status(
    rejection_dialog_mock,
    dialog_mock,
    add_pokemon_mock,
    datetime_mock,
    mock_requests,
    mock_mw,
    mock_db,
):
    """Test that rejecting the monthly challenge sets monthly_challenge to 2."""
    logger = MockLogger()

    dt_mock = MagicMock()
    dt_mock.month = 1
    dt_mock.year = 2024
    datetime_mock.now.return_value = dt_mock

    mock_response = MagicMock()
    mock_response.json.return_value = [
        {
            "month": "January 2024",
            "pokemon": {"name": "TestMon", "id": 1, "individual_id": "test-id"},
        }
    ]
    mock_requests.return_value = mock_response

    mock_db.get_user_data.return_value = True
    mock_db.get_pokemon.return_value = None

    # User rejects the Pokémon
    dialog_mock.return_value = False

    check_and_award_monthly_pokemon(logger, defer=False)

    # Should NOT add Pokémon
    add_pokemon_mock.assert_not_called()

    # Should set monthly_challenge to 2 (rejected) using set_monthly_challenge_state
    mock_db.set_monthly_challenge_state.assert_called_with("test-id", 2)

    # Should show the challenge dialog and rejection dialog
    dialog_mock.assert_called_once()
    rejection_dialog_mock.assert_called_once()


class _Future:
    """Stand-in for the future Anki's task manager hands to on_done."""

    def __init__(self, value):
        self._value = value

    def result(self):
        return self._value


def _serve_january_challenge(datetime_mock, mock_requests):
    dt_mock = MagicMock()
    dt_mock.month = 1
    dt_mock.year = 2024
    datetime_mock.now.return_value = dt_mock

    mock_response = MagicMock()
    mock_response.json.return_value = [
        {
            "month": "January 2024",
            "pokemon": {"name": "TestMon", "id": 1, "individual_id": "test-id"},
        }
    ]
    mock_requests.return_value = mock_response
    return mock_response


def _user_data(challenge_id, status):
    def get_user_data(key, default=None):
        if key == "monthly_challenge_id":
            return challenge_id
        if key == "monthly_challenge":
            return status
        return True

    return get_user_data


def _capture_background_task(mock_mw):
    scheduled = []
    mock_mw.taskman.run_in_background.side_effect = lambda task, on_done: (
        scheduled.append((task, on_done))
    )
    return scheduled


@pytest.mark.parametrize(
    "switch", ["switch_database", "replace_services_db", "reopen_profile"]
)
@patch("Ankimon.pyobj.monthly_challenge.datetime")
@patch("Ankimon.pyobj.monthly_challenge.add_pokemon_to_collection")
@patch("Ankimon.pyobj.monthly_challenge.show_monthly_challenge_dialog")
@patch("Ankimon.pyobj.monthly_challenge.show_monthly_acceptance_dialog")
@patch("Ankimon.pyobj.monthly_challenge.show_monthly_rejection_dialog")
def test_deferred_result_is_discarded_when_session_changes_mid_fetch(
    rejection_dialog_mock,
    acceptance_dialog_mock,
    dialog_mock,
    add_pokemon_mock,
    datetime_mock,
    switch,
    mock_requests,
    mock_mw,
    mock_db,
):
    """The check is dispatched against one database and profile, one of them
    changes while the request is in flight, and the late result must not read,
    prompt for or award into whatever is active when it lands."""
    logger = MockLogger()
    response = _serve_january_challenge(datetime_mock, mock_requests)
    mock_db.get_user_data.side_effect = _user_data(None, 0)
    mock_db.get_pokemon.return_value = None
    mock_db.identity_token.return_value = ("ankimon.db", 0)
    scheduled = _capture_background_task(mock_mw)

    check_and_award_monthly_pokemon(logger, defer=True)

    assert len(scheduled) == 1
    task, on_done = scheduled[0]
    mock_requests.assert_not_called()

    other_db = MagicMock()

    def switch_while_request_blocks(*args, **kwargs):
        if switch == "switch_database":
            mock_db.identity_token.return_value = ("ankimonDEV.db", 1)
        elif switch == "replace_services_db":
            monthly_challenge_module.services.db = other_db
        else:
            # The Ankimon DB is shared across Anki profiles; a reopened
            # profile shows up as a different collection object.
            mock_mw.col = MagicMock()
        return response

    mock_requests.side_effect = switch_while_request_blocks
    mock_db.reset_mock()

    result = task()

    assert result is not None
    # The worker only fetched and validated; no database work left the GUI thread.
    assert mock_db.method_calls == []
    assert other_db.method_calls == []

    on_done(_Future(result))

    dialog_mock.assert_not_called()
    add_pokemon_mock.assert_not_called()
    acceptance_dialog_mock.assert_not_called()
    rejection_dialog_mock.assert_not_called()
    for db in (mock_db, other_db):
        db.get_user_data.assert_not_called()
        db.get_pokemon.assert_not_called()
        db.set_monthly_challenge_state.assert_not_called()


@patch("Ankimon.pyobj.monthly_challenge.datetime")
@patch("Ankimon.pyobj.monthly_challenge.add_pokemon_to_collection")
@patch("Ankimon.pyobj.monthly_challenge.show_monthly_challenge_dialog")
@patch("Ankimon.pyobj.monthly_challenge.show_monthly_acceptance_dialog")
def test_deferred_result_is_applied_when_database_is_unchanged(
    acceptance_dialog_mock,
    dialog_mock,
    add_pokemon_mock,
    datetime_mock,
    mock_requests,
    mock_mw,
    mock_db,
):
    """The deferred path still offers and awards the Pokémon when nothing switched."""
    logger = MockLogger()
    _serve_january_challenge(datetime_mock, mock_requests)
    mock_db.get_user_data.side_effect = _user_data(None, 0)
    mock_db.get_pokemon.return_value = None
    mock_db.identity_token.return_value = ("ankimon.db", 0)
    dialog_mock.return_value = True
    add_pokemon_mock.return_value = True
    scheduled = _capture_background_task(mock_mw)

    check_and_award_monthly_pokemon(logger, defer=True)

    task, on_done = scheduled[0]
    mock_db.reset_mock()
    result = task()
    assert mock_db.method_calls == []

    on_done(_Future(result))

    dialog_mock.assert_called_once()
    add_pokemon_mock.assert_called_once()
    acceptance_dialog_mock.assert_called_once()
    mock_db.set_monthly_challenge_state.assert_called_once_with("test-id", 0)
    assert add_pokemon_mock.call_args.kwargs["accept_monthly_challenge"] is True


@pytest.mark.parametrize("accepted", [True, False])
@patch("Ankimon.pyobj.monthly_challenge.datetime")
@patch("Ankimon.pyobj.monthly_challenge.add_pokemon_to_collection")
@patch("Ankimon.pyobj.monthly_challenge.show_monthly_challenge_dialog")
@patch("Ankimon.pyobj.monthly_challenge.show_monthly_acceptance_dialog")
@patch("Ankimon.pyobj.monthly_challenge.show_monthly_rejection_dialog")
def test_decision_is_not_recorded_when_database_switches_while_dialog_is_open(
    rejection_dialog_mock,
    acceptance_dialog_mock,
    dialog_mock,
    add_pokemon_mock,
    datetime_mock,
    accepted,
    mock_requests,
    mock_mw,
    mock_db,
):
    """The decision dialog spins an event loop; a switch during it drops the decision."""
    logger = MockLogger()
    _serve_january_challenge(datetime_mock, mock_requests)
    mock_db.get_user_data.side_effect = _user_data("test-id", 0)
    mock_db.get_pokemon.return_value = None
    mock_db.identity_token.return_value = ("ankimon.db", 0)

    def switch_then_decide(*args, **kwargs):
        mock_db.identity_token.return_value = ("ankimonDEV.db", 1)
        return accepted

    dialog_mock.side_effect = switch_then_decide

    check_and_award_monthly_pokemon(logger, defer=False)

    dialog_mock.assert_called_once()
    add_pokemon_mock.assert_not_called()
    acceptance_dialog_mock.assert_not_called()
    rejection_dialog_mock.assert_not_called()
    mock_db.set_monthly_challenge_state.assert_not_called()


@pytest.mark.parametrize("restored", [True, False])
@patch("Ankimon.pyobj.monthly_challenge.datetime")
@patch("Ankimon.pyobj.monthly_challenge.add_pokemon_to_collection")
@patch("Ankimon.pyobj.monthly_challenge.show_monthly_challenge_dialog")
@patch("Ankimon.pyobj.monthly_challenge.show_monthly_acceptance_dialog")
@patch("Ankimon.pyobj.monthly_challenge.show_monthly_rejection_dialog")
def test_accepted_challenge_with_missing_pokemon_is_re_awarded_without_prompt(
    rejection_dialog_mock,
    acceptance_dialog_mock,
    dialog_mock,
    add_pokemon_mock,
    datetime_mock,
    restored,
    mock_requests,
    mock_mw,
    mock_db,
):
    """Status 1 means the user already accepted: a missing Pokémon is restored
    directly, and a failed restore leaves the status at 1 for the next retry."""
    logger = MockLogger()
    _serve_january_challenge(datetime_mock, mock_requests)
    mock_db.get_user_data.side_effect = _user_data("test-id", 1)
    mock_db.get_pokemon.return_value = None
    add_pokemon_mock.return_value = restored

    check_and_award_monthly_pokemon(logger, defer=False)

    add_pokemon_mock.assert_called_once()
    assert add_pokemon_mock.call_args[0][0]["individual_id"] == "test-id"
    dialog_mock.assert_not_called()
    rejection_dialog_mock.assert_not_called()
    assert acceptance_dialog_mock.call_count == (1 if restored else 0)
    mock_db.set_monthly_challenge_state.assert_not_called()


@pytest.fixture
def monthly_case(mock_db, mock_mw, mock_requests):
    """Mutable decision state: expose writes and nested-loop changes to reads."""
    from types import SimpleNamespace
    from contextlib import ExitStack

    state = {
        "rate_this": True,
        "monthly_challenge_id": "test-id",
        "monthly_challenge": 0,
    }
    mock_db.get_user_data.side_effect = lambda key, default=None: state.get(
        key, default
    )
    mock_db.set_monthly_challenge_state.side_effect = lambda iid, status: state.update(
        monthly_challenge_id=iid, monthly_challenge=status
    )
    mock_db.get_pokemon.return_value = None
    mock_db.identity_token.return_value = ("save-A", 0)
    scheduled = _capture_background_task(mock_mw)
    with ExitStack() as stack:
        date = stack.enter_context(patch.object(monthly_challenge_module, "datetime"))
        _serve_january_challenge(date, mock_requests)
        decision = stack.enter_context(
            patch.object(
                monthly_challenge_module,
                "show_monthly_challenge_dialog",
                return_value=True,
            )
        )
        add = stack.enter_context(
            patch.object(
                monthly_challenge_module, "add_pokemon_to_collection", return_value=True
            )
        )

        def save_award(pokemon, **kwargs):
            if add.return_value and kwargs.get("accept_monthly_challenge"):
                state.update(
                    monthly_challenge_id=pokemon["individual_id"], monthly_challenge=1
                )
            return add.return_value

        add.side_effect = save_award
        stack.enter_context(
            patch.object(monthly_challenge_module, "_refresh_collection")
        )
        stack.enter_context(
            patch.object(monthly_challenge_module, "show_monthly_acceptance_dialog")
        )
        stack.enter_context(
            patch.object(monthly_challenge_module, "show_monthly_rejection_dialog")
        )
        stack.enter_context(
            patch.object(
                monthly_challenge_module.services,
                "_monthly_challenge_request",
                None,
                create=True,
            )
        )
        yield SimpleNamespace(
            state=state,
            queued=scheduled,
            decision=decision,
            add=add,
            db=mock_db,
            mw=mock_mw,
        )


def test_monthly_coalesces_fetch_and_nested_dialog_requests(monthly_case):
    c = monthly_case
    logger = MockLogger()
    check_and_award_monthly_pokemon(logger)
    check_and_award_monthly_pokemon(logger)
    assert len(c.queued) == 1

    def nested_request(*args, **kwargs):
        check_and_award_monthly_pokemon(logger, reclaim=True)
        assert len(c.queued) == 1
        return True

    c.decision.side_effect = nested_request
    task, done = c.queued[0]
    done(_Future(task()))
    assert c.state["monthly_challenge"] == 1
    c.add.assert_called_once()
    assert monthly_challenge_module.services._monthly_challenge_request is None


@pytest.mark.parametrize("failure", ["dispatch", "fetch", "future", "dialog"])
def test_monthly_pending_request_is_released_after_failure(
    monthly_case, mock_requests, failure
):
    c = monthly_case
    if failure == "dispatch":
        c.mw.taskman.run_in_background.side_effect = RuntimeError("dispatch failed")
    elif failure == "fetch":
        mock_requests.side_effect = (
            monthly_challenge_module.requests.exceptions.ConnectionError("offline")
        )
    elif failure == "dialog":
        c.decision.side_effect = RuntimeError("dialog failed")
    check_and_award_monthly_pokemon(MockLogger())
    if failure != "dispatch":
        task, done = c.queued.pop()
        if failure == "future":
            future = MagicMock()
            future.result.side_effect = RuntimeError("worker failed")
        else:
            future = _Future(task())
        done(future)
    assert monthly_challenge_module.services._monthly_challenge_request is None
    c.mw.taskman.run_in_background.side_effect = lambda task, done: c.queued.append(
        (task, done)
    )
    check_and_award_monthly_pokemon(MockLogger())
    assert len(c.queued) == 1


def test_old_completion_cannot_release_new_sessions_request(monthly_case):
    c = monthly_case
    logger = MockLogger()
    check_and_award_monthly_pokemon(logger)
    old_task, old_done = c.queued.pop()
    c.db.identity_token.return_value = ("save-B", 1)
    check_and_award_monthly_pokemon(logger)
    new_request = monthly_challenge_module.services._monthly_challenge_request
    old_done(_Future(old_task()))
    assert monthly_challenge_module.services._monthly_challenge_request is new_request
    check_and_award_monthly_pokemon(logger)
    assert len(c.queued) == 1
    task, done = c.queued.pop()
    done(_Future(task()))
    c.add.assert_called_once()
    assert monthly_challenge_module.services._monthly_challenge_request is None


@pytest.mark.parametrize("choice", [True, False])
@pytest.mark.parametrize("change", ["decision", "collection"])
def test_stale_prompt_cannot_overwrite_new_decision_or_owned_progress(
    monthly_case, choice, change
):
    c = monthly_case

    def change_while_open(*args, **kwargs):
        if change == "decision":
            c.state["monthly_challenge"] = 2
        else:
            c.db.get_pokemon.return_value = {"individual_id": "test-id", "level": 70}
        return choice

    c.decision.side_effect = change_while_open
    check_and_award_monthly_pokemon(MockLogger(), defer=False)
    c.db.set_monthly_challenge_state.assert_not_called()
    c.add.assert_not_called()


@pytest.mark.parametrize("status", [0, 1, 2])
def test_save_failure_preserves_previous_decision(monthly_case, status):
    c = monthly_case
    c.state["monthly_challenge"] = status
    c.add.return_value = False
    check_and_award_monthly_pokemon(MockLogger(), defer=False, reclaim=True)
    assert c.state["monthly_challenge"] == status
    c.db.set_monthly_challenge_state.assert_not_called()
    assert c.decision.call_count == (0 if status == 1 else 1)


def test_reclaim_offers_rejected_reward_without_resetting_it_first(monthly_case):
    c = monthly_case
    c.state["monthly_challenge"] = 2
    check_and_award_monthly_pokemon(MockLogger(), defer=False)
    c.decision.assert_not_called()

    def accept(*args, **kwargs):
        assert c.state["monthly_challenge"] == 2
        return True

    c.decision.side_effect = accept
    check_and_award_monthly_pokemon(MockLogger(), defer=False, reclaim=True)
    assert c.state["monthly_challenge"] == 1
    c.add.assert_called_once()


def test_save_error_dialog_switch_cannot_write_new_account(monthly_case):
    c = monthly_case

    def fail_and_switch(*args, **kwargs):
        c.db.identity_token.return_value = ("save-B", 1)
        c.state.update(monthly_challenge_id="B", monthly_challenge=2)
        return False

    c.add.side_effect = fail_and_switch
    check_and_award_monthly_pokemon(MockLogger(), defer=False)
    assert c.state["monthly_challenge_id"] == "B"
    assert c.state["monthly_challenge"] == 2
    c.db.set_monthly_challenge_state.assert_not_called()


@pytest.mark.parametrize("accepted", [True, False])
def test_decision_preserves_remote_rating_eligibility_check(monthly_case, accepted):
    c = monthly_case

    def change_eligibility(*args, **kwargs):
        c.state["rate_this"] = False
        return accepted

    c.decision.side_effect = change_eligibility
    check_and_award_monthly_pokemon(MockLogger(), defer=False)
    c.add.assert_not_called()
    c.db.set_monthly_challenge_state.assert_not_called()


@pytest.mark.parametrize("outcome", ["rejected", "owned", "offline"])
def test_menu_request_during_automatic_fetch_keeps_explicit_intent(
    monthly_case, mock_requests, outcome
):
    c = monthly_case
    c.state["monthly_challenge"] = 2
    if outcome == "owned":
        c.db.get_pokemon.return_value = {
            "name": "Pikachu",
            "level": 70,
            "pokemon_defeated": 42,
        }
    elif outcome == "offline":
        mock_requests.side_effect = (
            monthly_challenge_module.requests.exceptions.ConnectionError("offline")
        )
    with patch.object(monthly_challenge_module.services, "ui") as ui:
        check_and_award_monthly_pokemon(MockLogger())
        check_and_award_monthly_pokemon(MockLogger(), reclaim=True)
        check_and_award_monthly_pokemon(MockLogger())
        assert len(c.queued) == 1
        task, done = c.queued.pop()
        done(_Future(task()))
        if outcome == "rejected":
            c.decision.assert_called_once()
            c.add.assert_called_once()
            assert c.state["monthly_challenge"] == 1
        else:
            c.decision.assert_not_called()
            c.add.assert_not_called()
            kind, message = ui.notify.call_args.args
            assert kind == ("info" if outcome == "owned" else "warning")
            assert (
                ("70" in message and "42" in message)
                if outcome == "owned"
                else "try again" in message
            )
        assert monthly_challenge_module.services._monthly_challenge_request is None
