"""Tests for scripts/release_monthly_challenge.py."""

import datetime
import json
import pytest
from pathlib import Path

from scripts.release_monthly_challenge import (
    should_release,
    get_target_month,
    validate_challenge_structure,
    process_monthly_release,
)


def sample_challenge(month: str, name: str = "Pikachu") -> dict:
    return {
        "code": f"MONTHLY-{month.replace(' ', '-').upper()}-{name.upper()}",
        "month": month,
        "description": f"Challenge for {month}",
        "previous_challenge_individual_id": "prev-uuid-1234",
        "defeat_threshold": 100,
        "pokemon": {
            "name": name,
            "id": 25,
            "level": 1,
            "stats": {"hp": 35, "atk": 55, "def": 40, "spa": 50, "spd": 50, "spe": 90},
            "attacks": ["Thunder Shock", "Quick Attack"],
            "individual_id": "current-uuid-5678",
        },
    }


def test_should_release_timing():
    # Oct 30 with 1 day ahead -> Oct 31 (same month) -> False
    assert not should_release(datetime.date(2026, 10, 30), days_ahead=1)

    # Oct 31 with 1 day ahead -> Nov 01 (next month!) -> True
    assert should_release(datetime.date(2026, 10, 31), days_ahead=1)

    # Oct 30 with 2 days ahead -> Nov 01 -> True
    assert should_release(datetime.date(2026, 10, 30), days_ahead=2)

    # Dec 31 rolls into Jan next year -> True
    assert should_release(datetime.date(2026, 12, 31), days_ahead=1)


def test_get_target_month():
    assert get_target_month(datetime.date(2026, 10, 31), days_ahead=1) == "November 2026"
    assert get_target_month(datetime.date(2026, 12, 31), days_ahead=1) == "January 2027"


def test_validate_challenge_structure():
    valid = sample_challenge("November 2026")
    validate_challenge_structure(valid)  # Should not raise

    invalid = sample_challenge("November 2026")
    del invalid["pokemon"]["stats"]
    with pytest.raises(ValueError, match="missing key: 'stats'"):
        validate_challenge_structure(invalid)


def test_process_monthly_release_skips_when_not_in_window(tmp_path):
    challenges_file = tmp_path / "monthly_challenges.json"
    challenges_file.write_text(json.dumps([sample_challenge("October 2026")]), encoding="utf-8")

    secret_data = json.dumps([sample_challenge("November 2026", "Gengar")])

    # Date is mid-month (Oct 15), not in window, force=False
    updated = process_monthly_release(
        challenges_file=challenges_file,
        raw_secret_data=secret_data,
        force=False,
        days_ahead=1,
        reference_date=datetime.date(2026, 10, 15),
    )
    assert updated is False
    with open(challenges_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert len(data) == 1


def test_process_monthly_release_appends_when_in_window(tmp_path):
    challenges_file = tmp_path / "monthly_challenges.json"
    challenges_file.write_text(json.dumps([sample_challenge("October 2026")]), encoding="utf-8")

    secret_data = json.dumps([
        sample_challenge("November 2026", "Gengar"),
        sample_challenge("December 2026", "Delibird"),
    ])

    # Date is Oct 31, 1 day ahead -> November 2026
    updated = process_monthly_release(
        challenges_file=challenges_file,
        raw_secret_data=secret_data,
        force=False,
        days_ahead=1,
        reference_date=datetime.date(2026, 10, 31),
    )
    assert updated is True

    with open(challenges_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert len(data) == 2
    assert data[1]["month"] == "November 2026"
    assert data[1]["pokemon"]["name"] == "Gengar"


def test_process_monthly_release_idempotent(tmp_path):
    challenges_file = tmp_path / "monthly_challenges.json"
    challenges_file.write_text(
        json.dumps([sample_challenge("October 2026"), sample_challenge("November 2026", "Gengar")]),
        encoding="utf-8",
    )

    secret_data = json.dumps([sample_challenge("November 2026", "Gengar")])

    # Running on Oct 31 when November is already published should skip cleanly
    updated = process_monthly_release(
        challenges_file=challenges_file,
        raw_secret_data=secret_data,
        force=True,
        override_month="November 2026",
    )
    assert updated is False
    with open(challenges_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert len(data) == 2


def test_process_monthly_release_autolinks_previous_uuid(tmp_path):
    challenges_file = tmp_path / "monthly_challenges.json"
    initial_entry = sample_challenge("October 2026")
    initial_entry["pokemon"]["individual_id"] = "october-unique-uuid-999"
    challenges_file.write_text(json.dumps([initial_entry]), encoding="utf-8")

    upcoming = sample_challenge("November 2026", "Gengar")
    upcoming["previous_challenge_individual_id"] = ""  # empty, needs link
    secret_data = json.dumps([upcoming])

    updated = process_monthly_release(
        challenges_file=challenges_file,
        raw_secret_data=secret_data,
        force=True,
        override_month="November 2026",
    )
    assert updated is True
    with open(challenges_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data[1]["previous_challenge_individual_id"] == "october-unique-uuid-999"
