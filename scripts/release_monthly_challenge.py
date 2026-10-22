#!/usr/bin/env python3
"""
release_monthly_challenge.py

Automates revealing the upcoming month's Pokémon challenge from an encrypted
secret (or environment variable) into `assets/challenges/monthly_challenges.json`.
Also provides CLI tools to validate the format of challenge JSON strings or files.

Supported Environment Variables:
  - UPCOMING_MONTHLY_CHALLENGES: JSON array string of future challenge objects.
  - GITHUB_OUTPUT: (Optional) Path to GitHub Actions step output file.
  - FORCE_RELEASE: (Optional, "true"/"1") If set, forces release of the specified
    or next target month regardless of how many days remain in the current month.
  - TARGET_MONTH: (Optional) Override target month string (e.g. "November 2026").
  - DAYS_AHEAD: (Optional, default: 1) How many days before the 1st of the next
    month to trigger the release. Defaults to 1 day (e.g. on Oct 31 for Nov 1).
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


CHALLENGES_PATH = Path("assets/challenges/monthly_challenges.json")


def set_github_output(key: str, value: str) -> None:
    """Write an output key-value pair to GITHUB_OUTPUT if present."""
    github_output_path = os.environ.get("GITHUB_OUTPUT")
    if github_output_path:
        with open(github_output_path, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")
    print(f"[OUTPUT] {key}={value}")


def get_target_month(
    reference_date: Optional[datetime.date] = None,
    days_ahead: int = 1,
) -> str:
    """
    Returns the target month name and year (e.g. 'November 2026').

    If `reference_date + timedelta(days=days_ahead)` rolls into a new month,
    it returns that new month. Otherwise, it returns the current month of `reference_date`.
    """
    if reference_date is None:
        reference_date = datetime.datetime.now(datetime.timezone.utc).date()

    target_date = reference_date + datetime.timedelta(days=days_ahead)
    return target_date.strftime("%B %Y")


def should_release(
    reference_date: Optional[datetime.date] = None,
    days_ahead: int = 1,
) -> bool:
    """
    Determine if reference_date is within `days_ahead` days of the next month.
    For example, with days_ahead=1:
      Oct 30 -> Oct 31 (same month) -> False
      Oct 31 -> Nov 01 (different month) -> True
    """
    if reference_date is None:
        reference_date = datetime.datetime.now(datetime.timezone.utc).date()

    target_date = reference_date + datetime.timedelta(days=days_ahead)
    return target_date.month != reference_date.month


def validate_challenge_structure(entry: Dict[str, Any], index: Optional[int] = None) -> List[str]:
    """
    Validate that a challenge entry has all required fields and correct types.
    Returns a list of error strings (empty if valid).
    """
    errors: List[str] = []
    prefix = f"Entry [{index}] " if index is not None else "Entry: "

    if not isinstance(entry, dict):
        return [f"{prefix}must be a JSON object, got {type(entry).__name__}"]

    required_keys = ["code", "month", "description", "pokemon"]
    for key in required_keys:
        if key not in entry or not entry[key]:
            errors.append(f"{prefix}missing required field '{key}'")

    # Validate month format e.g. "November 2026"
    month_val = entry.get("month", "")
    if month_val:
        try:
            datetime.datetime.strptime(month_val.strip(), "%B %Y")
        except ValueError:
            errors.append(f"{prefix}field 'month' must be formatted as 'Month Year' (e.g. 'November 2026'), got '{month_val}'")

    pokemon = entry.get("pokemon")
    if not isinstance(pokemon, dict):
        errors.append(f"{prefix}'pokemon' must be an object")
        return errors

    pokemon_required = ["name", "id", "level", "stats", "attacks"]
    for pkey in pokemon_required:
        if pkey not in pokemon:
            errors.append(f"{prefix}pokemon missing required key '{pkey}'")

    if "stats" in pokemon:
        if not isinstance(pokemon["stats"], dict):
            errors.append(f"{prefix}pokemon 'stats' must be a dictionary")
        else:
            stat_keys = ["hp", "atk", "def", "spa", "spd", "spe"]
            for skey in stat_keys:
                if skey not in pokemon["stats"]:
                    errors.append(f"{prefix}pokemon stats missing '{skey}'")

    if "attacks" in pokemon:
        if not isinstance(pokemon["attacks"], list) or len(pokemon["attacks"]) == 0:
            errors.append(f"{prefix}pokemon 'attacks' must be a non-empty list of move names")

    return errors


def validate_challenges_payload(raw_data: str) -> Tuple[bool, List[str], List[Dict[str, Any]]]:
    """
    Parses and thoroughly checks a challenges JSON payload.
    Returns (is_valid, list_of_errors, parsed_entries).
    """
    errors: List[str] = []
    if not raw_data.strip():
        return False, ["Payload is empty."], []

    try:
        data = json.loads(raw_data)
    except Exception as e:
        return False, [f"Invalid JSON syntax: {e}"], []

    # Support single entry dict or list of entries
    if isinstance(data, dict):
        entries = [data]
    elif isinstance(data, list):
        entries = data
    else:
        return False, [f"Expected JSON array or object, got {type(data).__name__}"], []

    if len(entries) == 0:
        return False, ["JSON array is empty."], []

    seen_months = set()
    seen_codes = set()
    for i, entry in enumerate(entries):
        entry_errors = validate_challenge_structure(entry, index=i)
        errors.extend(entry_errors)
        if isinstance(entry, dict):
            m = entry.get("month", "").strip().lower()
            if m:
                if m in seen_months:
                    errors.append(f"Entry [{i}]: duplicate month '{entry.get('month')}' in payload")
                seen_months.add(m)
            code = entry.get("code", "").strip().lower()
            if code:
                if code in seen_codes:
                    errors.append(f"Entry [{i}]: duplicate code '{entry.get('code')}' in payload")
                seen_codes.add(code)

    return (len(errors) == 0, errors, entries)


def process_monthly_release(
    challenges_file: Path = CHALLENGES_PATH,
    raw_secret_data: Optional[str] = None,
    force: bool = False,
    override_month: Optional[str] = None,
    days_ahead: int = 1,
    reference_date: Optional[datetime.date] = None,
) -> bool:
    """
    Check if a monthly challenge needs to be released, and if so, append it to `challenges_file`.

    Returns True if an entry was added and file updated, False otherwise.
    """
    if not challenges_file.exists():
        raise FileNotFoundError(f"Challenges file not found at: {challenges_file}")

    if not raw_secret_data:
        raw_secret_data = os.environ.get("UPCOMING_MONTHLY_CHALLENGES", "")

    if not raw_secret_data.strip():
        print("Warning: UPCOMING_MONTHLY_CHALLENGES is empty or not set. Nothing to process.")
        set_github_output("updated", "false")
        return False

    is_valid, errors, upcoming_list = validate_challenges_payload(raw_secret_data)
    if not is_valid:
        raise ValueError("Invalid UPCOMING_MONTHLY_CHALLENGES secret payload:\n  - " + "\n  - ".join(errors))

    with open(challenges_file, "r", encoding="utf-8") as f:
        existing_challenges: List[Dict[str, Any]] = json.load(f)

    existing_months = {
        item.get("month", "").strip().lower(): item for item in existing_challenges if "month" in item
    }

    if override_month:
        target_month_str = override_month.strip()
    else:
        # Check window
        is_release_window = should_release(reference_date=reference_date, days_ahead=days_ahead)
        if not is_release_window and not force:
            ref = reference_date or datetime.datetime.now(datetime.timezone.utc).date()
            print(f"Current date {ref} is not within {days_ahead} day(s) of next month. Skipping (use force to bypass).")
            set_github_output("updated", "false")
            return False

        target_month_str = get_target_month(reference_date=reference_date, days_ahead=days_ahead)

    print(f"Target month for release: '{target_month_str}'")

    if target_month_str.lower() in existing_months:
        print(f"Month '{target_month_str}' is already published in {challenges_file}. Skipping.")
        set_github_output("updated", "false")
        return False

    # Look for candidate in secret
    candidate = None
    for entry in upcoming_list:
        if entry.get("month", "").strip().lower() == target_month_str.lower():
            candidate = entry
            break

    if not candidate:
        print(f"No entry found in UPCOMING_MONTHLY_CHALLENGES for '{target_month_str}'.")
        set_github_output("updated", "false")
        return False

    # If candidate previous_challenge_individual_id is not set or wants linking:
    # If the user omitted or left placeholder, link to the last existing challenge's pokemon individual_id
    if not candidate.get("previous_challenge_individual_id") and existing_challenges:
        last_pokemon = existing_challenges[-1].get("pokemon", {})
        last_ind_id = last_pokemon.get("individual_id")
        if last_ind_id:
            print(f"Auto-linking previous_challenge_individual_id to previous entry: {last_ind_id}")
            candidate["previous_challenge_individual_id"] = last_ind_id

    existing_challenges.append(candidate)

    # Write formatted JSON
    with open(challenges_file, "w", encoding="utf-8") as f:
        json.dump(existing_challenges, f, indent=4)
        f.write("\n")

    pokemon_name = candidate.get("pokemon", {}).get("name", "Unknown")
    code = candidate.get("code", "")
    print(f"Successfully added challenge for '{target_month_str}': {pokemon_name} ({code})")

    set_github_output("updated", "true")
    set_github_output("month", target_month_str)
    set_github_output("pokemon", pokemon_name)
    set_github_output("code", code)
    return True


def cmd_validate(target_input: str) -> int:
    """Validate JSON text or file content."""
    p = Path(target_input)
    if p.exists() and p.is_file():
        print(f"Validating file: {p}")
        raw_text = p.read_text(encoding="utf-8")
    else:
        raw_text = target_input

    is_valid, errors, entries = validate_challenges_payload(raw_text)
    if not is_valid:
        print("❌ Validation FAILED with errors:")
        for err in errors:
            print(f"  - {err}")
        return 1

    print(f"✅ Format is valid! Found {len(entries)} valid challenge(s):")
    for i, e in enumerate(entries, 1):
        poke = e.get("pokemon", {})
        print(f"  {i}. {e.get('month')}: {poke.get('name')} (Level {poke.get('level')}) [Code: {e.get('code')}]")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Automate monthly challenge releases and validate challenge formats.")
    parser.add_argument("--validate", type=str, metavar="FILE_OR_JSON", help="Validate a JSON string or file path containing challenge entries.")
    parser.add_argument("--force", action="store_true", help="Force release regardless of the calendar day.")
    parser.add_argument("--target-month", type=str, default=None, help="Explicit month to release (e.g. 'November 2026').")
    parser.add_argument("--days-ahead", type=int, default=1, help="Number of days ahead to target (default: 1).")
    parser.add_argument("--challenges-file", type=Path, default=CHALLENGES_PATH, help="Path to monthly_challenges.json.")
    args = parser.parse_args()

    if args.validate:
        return cmd_validate(args.validate)

    force_env = os.environ.get("FORCE_RELEASE", "").lower() in ("true", "1", "yes")
    force = args.force or force_env
    target_month = args.target_month or os.environ.get("TARGET_MONTH")
    days_ahead = int(os.environ.get("DAYS_AHEAD", args.days_ahead))

    try:
        updated = process_monthly_release(
            challenges_file=args.challenges_file,
            force=force,
            override_month=target_month,
            days_ahead=days_ahead,
        )
        return 0
    except Exception as e:
        print(f"Error during monthly release process: {e}", file=sys.stderr)
        set_github_output("updated", "false")
        return 1


if __name__ == "__main__":
    sys.exit(main())
