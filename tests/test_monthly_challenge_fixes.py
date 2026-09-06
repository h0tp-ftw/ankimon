import pytest
import sys
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

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
    "Ankimon.pyobj.pokemon_trade",
    _src / "Ankimon" / "pyobj" / "pokemon_trade.py",
)
pokemon_trade_module = importlib.util.module_from_spec(spec)
sys.modules["Ankimon.pyobj.pokemon_trade"] = pokemon_trade_module
spec.loader.exec_module(pokemon_trade_module)
check_and_award_monthly_pokemon = pokemon_trade_module.check_and_award_monthly_pokemon



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
    with patch("Ankimon.pyobj.pokemon_trade.mw") as mw_mock, \
         patch("Ankimon.pyobj.pokemon_trade.services.db", mock_db):
        mw_mock.ankimon_db = mock_db
        yield mw_mock

@pytest.fixture
def mock_requests():
    with patch("Ankimon.pyobj.pokemon_trade.requests.get") as get_mock:
        yield get_mock

@patch("Ankimon.pyobj.pokemon_trade.datetime")
@patch("Ankimon.pyobj.pokemon_trade.add_pokemon_to_collection")
@patch("Ankimon.pyobj.pokemon_trade.show_monthly_challenge_dialog")
@patch("Ankimon.pyobj.pokemon_trade.utils.showInfo")
def test_rate_this_check(show_info_mock, dialog_mock, add_pokemon_mock, datetime_mock, mock_requests, mock_mw, mock_db):
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
            "pokemon": {
                "name": "TestMon",
                "id": 1,
                "individual_id": "test-id"
            }
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
    mock_db.get_pokemon.return_value = None # Pokémon not found yet
    check_and_award_monthly_pokemon(logger, defer=False)
    mock_requests.assert_called_once()
    add_pokemon_mock.assert_called_once()

    mock_requests.reset_mock()
    add_pokemon_mock.reset_mock()

    # Test case 4: user rated using new boolean True
    mock_db.get_user_data.return_value = True
    check_and_award_monthly_pokemon(logger, defer=False)
    mock_requests.assert_called_once()
    add_pokemon_mock.assert_called_once()


@patch("Ankimon.pyobj.pokemon_trade.datetime")
@patch("Ankimon.pyobj.pokemon_trade.add_pokemon_to_collection")
@patch("Ankimon.pyobj.pokemon_trade.show_monthly_challenge_dialog")
@patch("Ankimon.pyobj.pokemon_trade.utils.showInfo")
def test_previous_challenge_pokemon_null_check(show_info_mock, dialog_mock, add_pokemon_mock, datetime_mock, mock_requests, mock_mw, mock_db):
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
            "pokemon": {
                "name": "TestMon",
                "id": 1,
                "individual_id": "test-id"
            }
        }
    ]
    mock_requests.return_value = mock_response

    mock_db.get_user_data.return_value = True

    # Mock dialog to accept the Pokémon
    dialog_mock.return_value = True

    # Setup get_pokemon to handle target and previous pokemon
    def get_pokemon_side_effect(individual_id):
        if individual_id == "test-id":
            return None # Don't have target
        if individual_id == "prev-id":
            return None # Simulate user missing the previous challenge pokemon
        return None

    mock_db.get_pokemon.side_effect = get_pokemon_side_effect

    # Call the function - it shouldn't crash
    check_and_award_monthly_pokemon(logger, defer=False)

    # Should have added the new pokemon (not shiny)
    add_pokemon_mock.assert_called_once()
    added_pokemon = add_pokemon_mock.call_args[0][0]
    assert added_pokemon["shiny"] is False


@patch("Ankimon.pyobj.pokemon_trade.datetime")
@patch("Ankimon.pyobj.pokemon_trade.add_pokemon_to_collection")
@patch("Ankimon.pyobj.pokemon_trade.show_monthly_challenge_dialog")
@patch("Ankimon.pyobj.pokemon_trade.utils.showInfo")
def test_previous_challenge_pokemon_has_enough_defeats(show_info_mock, dialog_mock, add_pokemon_mock, datetime_mock, mock_requests, mock_mw, mock_db):
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
            "pokemon": {
                "name": "TestMon",
                "id": 1,
                "individual_id": "test-id"
            }
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


@patch("Ankimon.pyobj.pokemon_trade.datetime")
@patch("Ankimon.pyobj.pokemon_trade.add_pokemon_to_collection")
@patch("Ankimon.pyobj.pokemon_trade.show_monthly_challenge_dialog")
@patch("Ankimon.pyobj.pokemon_trade.utils.showInfo")
def test_monthly_challenge_rejected_status(show_info_mock, dialog_mock, add_pokemon_mock, datetime_mock, mock_requests, mock_mw, mock_db):
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
            "pokemon": {
                "name": "TestMon",
                "id": 1,
                "individual_id": "test-id"
            }
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


@patch("Ankimon.pyobj.pokemon_trade.datetime")
@patch("Ankimon.pyobj.pokemon_trade.add_pokemon_to_collection")
@patch("Ankimon.pyobj.pokemon_trade.show_monthly_challenge_dialog")
@patch("Ankimon.pyobj.pokemon_trade.utils.showInfo")
def test_monthly_challenge_reconciliation_branch(show_info_mock, dialog_mock, add_pokemon_mock, datetime_mock, mock_requests, mock_mw, mock_db):
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
            "pokemon": {
                "name": "TestMon",
                "id": 1,
                "individual_id": "test-id"
            }
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

    # Should reconcile tracking values
    mock_db.set_user_data.assert_any_call("monthly_challenge_id", "test-id")
    mock_db.set_user_data.assert_any_call("monthly_challenge", 1)
    
    # Should NOT add Pokémon (already exists) or show dialog
    add_pokemon_mock.assert_not_called()
    dialog_mock.assert_not_called()


@patch("Ankimon.pyobj.pokemon_trade.datetime")
@patch("Ankimon.pyobj.pokemon_trade.add_pokemon_to_collection")
@patch("Ankimon.pyobj.pokemon_trade.show_monthly_challenge_dialog")
@patch("Ankimon.pyobj.pokemon_trade.utils.showInfo")
def test_monthly_challenge_rollback_on_add_failure(show_info_mock, dialog_mock, add_pokemon_mock, datetime_mock, mock_requests, mock_mw, mock_db):
    """Test that monthly_challenge is rolled back to 0 when add_pokemon_to_collection fails."""
    logger = MockLogger()

    dt_mock = MagicMock()
    dt_mock.month = 1
    dt_mock.year = 2024
    datetime_mock.now.return_value = dt_mock

    mock_response = MagicMock()
    mock_response.json.return_value = [
        {
            "month": "January 2024",
            "pokemon": {
                "name": "TestMon",
                "id": 1,
                "individual_id": "test-id"
            }
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
    
    # Should roll back monthly_challenge to 0 on failure
    mock_db.set_user_data.assert_any_call("monthly_challenge", 0)
    mock_db.set_user_data.assert_any_call("monthly_challenge_id", "test-id")
    
    # Should show the challenge dialog (user accepted)
    dialog_mock.assert_called_once()


@patch("Ankimon.pyobj.pokemon_trade.datetime")
@patch("Ankimon.pyobj.pokemon_trade.add_pokemon_to_collection")
@patch("Ankimon.pyobj.pokemon_trade.show_monthly_challenge_dialog")
@patch("Ankimon.pyobj.pokemon_trade.utils.showInfo")
def test_monthly_challenge_rejection_sets_status(show_info_mock, dialog_mock, add_pokemon_mock, datetime_mock, mock_requests, mock_mw, mock_db):
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
            "pokemon": {
                "name": "TestMon",
                "id": 1,
                "individual_id": "test-id"
            }
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
    
    # Should set monthly_challenge to 2 (rejected)
    mock_db.set_user_data.assert_any_call("monthly_challenge", 2)
    mock_db.set_user_data.assert_any_call("monthly_challenge_id", "test-id")
    
    # Should show the challenge dialog
    dialog_mock.assert_called_once()
