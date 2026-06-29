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
    with patch("Ankimon.pyobj.pokemon_trade.mw") as mw_mock:
        mw_mock.ankimon_db = mock_db
        yield mw_mock

@pytest.fixture
def mock_requests():
    with patch("Ankimon.pyobj.pokemon_trade.requests.get") as get_mock:
        yield get_mock

@patch("Ankimon.pyobj.pokemon_trade.datetime")
@patch("Ankimon.pyobj.pokemon_trade.add_pokemon_to_collection")
@patch("Ankimon.pyobj.pokemon_trade.utils.showInfo")
def test_rate_this_check(show_info_mock, add_pokemon_mock, datetime_mock, mock_requests, mock_mw, mock_db):
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

    # Test case 1: user hasn't rated (returns None)
    mock_db.get_user_data.return_value = None
    check_and_award_monthly_pokemon(logger)
    mock_requests.assert_not_called()

    # Test case 2: user hasn't rated (returns False)
    mock_db.get_user_data.return_value = False
    check_and_award_monthly_pokemon(logger)
    mock_requests.assert_not_called()

    # Test case 3: user rated using old 'true' string
    mock_db.get_user_data.return_value = "true"
    mock_db.get_pokemon.return_value = None # Pokémon not found yet
    check_and_award_monthly_pokemon(logger)
    mock_requests.assert_called_once()
    add_pokemon_mock.assert_called_once()

    mock_requests.reset_mock()
    add_pokemon_mock.reset_mock()

    # Test case 4: user rated using new boolean True
    mock_db.get_user_data.return_value = True
    check_and_award_monthly_pokemon(logger)
    mock_requests.assert_called_once()
    add_pokemon_mock.assert_called_once()


@patch("Ankimon.pyobj.pokemon_trade.datetime")
@patch("Ankimon.pyobj.pokemon_trade.add_pokemon_to_collection")
@patch("Ankimon.pyobj.pokemon_trade.utils.showInfo")
def test_previous_challenge_pokemon_null_check(show_info_mock, add_pokemon_mock, datetime_mock, mock_requests, mock_mw, mock_db):
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

    # Setup get_pokemon to handle target and previous pokemon
    def get_pokemon_side_effect(individual_id):
        if individual_id == "test-id":
            return None # Don't have target
        if individual_id == "prev-id":
            return None # Simulate user missing the previous challenge pokemon
        return None

    mock_db.get_pokemon.side_effect = get_pokemon_side_effect

    # Call the function - it shouldn't crash
    check_and_award_monthly_pokemon(logger)

    # Should have added the new pokemon (not shiny)
    add_pokemon_mock.assert_called_once()
    added_pokemon = add_pokemon_mock.call_args[0][0]
    assert added_pokemon["shiny"] is False


@patch("Ankimon.pyobj.pokemon_trade.datetime")
@patch("Ankimon.pyobj.pokemon_trade.add_pokemon_to_collection")
@patch("Ankimon.pyobj.pokemon_trade.utils.showInfo")
def test_previous_challenge_pokemon_has_enough_defeats(show_info_mock, add_pokemon_mock, datetime_mock, mock_requests, mock_mw, mock_db):
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

    def get_pokemon_side_effect(individual_id):
        if individual_id == "test-id":
            return None
        if individual_id == "prev-id":
            return {"pokemon_defeated": 15}
        return None

    mock_db.get_pokemon.side_effect = get_pokemon_side_effect

    check_and_award_monthly_pokemon(logger)

    add_pokemon_mock.assert_called_once()
    added_pokemon = add_pokemon_mock.call_args[0][0]
    assert added_pokemon["shiny"] is True
