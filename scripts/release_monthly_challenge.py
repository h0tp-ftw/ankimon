#!/usr/bin/env python3
"""Reveal one monthly challenge, keeping the remaining secret payload private.

UPCOMING_MONTHLY_CHALLENGES contains a JSON object or array. The default release
window starts on the last UTC day of the preceding month; daily retries can
recover a missed release during the current month. FORCE_RELEASE and TARGET_MONTH
mirror the CLI options. Validation and dry runs never print challenge contents.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from uuid import UUID


CHALLENGES_PATH = (
    Path(__file__).resolve().parents[1] / "assets/challenges/monthly_challenges.json"
)
MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
STATS = ("hp", "atk", "def", "spa", "spd", "spe")


def month_date(value: str) -> datetime.date:
    """Parse the exact English month format used by the add-on, without locale."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z]+ [0-9]{4}", value):
        raise ValueError("month must use 'Month Year' format (e.g. 'November 2026').")
    name, year = value.split(" ")
    if name not in MONTHS or int(year) == 0:
        raise ValueError("month must use a valid English month and year.")
    return datetime.date(int(year), MONTHS.index(name) + 1, 1)


def month_name(date: datetime.date) -> str:
    """Format a month in English regardless of the runner's locale."""
    return f"{MONTHS[date.month - 1]} {date.year:04d}"


def get_target_month(reference_date: datetime.date | None = None) -> str:
    """Return the next calendar month, including for mid-month forced releases."""
    date = reference_date or datetime.datetime.now(datetime.timezone.utc).date()
    return month_name(
        (date.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)
    )


def should_release(reference_date: datetime.date | None = None) -> bool:
    """Return whether the next month's 24-hour UTC reveal window has begun."""
    date = reference_date or datetime.datetime.now(datetime.timezone.utc).date()
    return date + datetime.timedelta(days=1) == month_date(get_target_month(date))


def _text(value: object) -> bool:
    """Accept nonempty, trimmed single-line strings without control characters."""
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and all(ord(char) >= 32 and ord(char) != 127 for char in value)
    )


def _integer(value: object, minimum: int, maximum: int | None = None) -> bool:
    """Check JSON integers without accepting booleans as numbers."""
    return (
        type(value) is int
        and value >= minimum
        and (maximum is None or value <= maximum)
    )


def _uuid(value: object) -> bool:
    """Require a canonical UUID so database lookups use a stable identifier."""
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def validate_challenge_structure(entry: object, index: int | None = None) -> list[str]:
    """Validate runtime fields; diagnostics contain field names, never secret values."""
    prefix = f"Entry [{index}]: " if index is not None else "Entry: "
    if not isinstance(entry, dict):
        return [prefix + "must be a JSON object."]
    errors = []
    for key in ("code", "month"):
        if not _text(entry.get(key)):
            errors.append(prefix + f"'{key}' must be a nonempty single-line string.")
    try:
        month_date(entry.get("month"))
    except ValueError as exc:
        errors.append(prefix + str(exc))
    if (
        not isinstance(entry.get("description"), str)
        or not entry["description"].strip()
    ):
        errors.append(prefix + "'description' must be a nonempty string.")
    if not _integer(entry.get("defeat_threshold"), 1):
        errors.append(prefix + "'defeat_threshold' must be a positive integer.")
    previous = entry.get("previous_challenge_individual_id")
    if previous not in (None, "") and not _uuid(previous):
        errors.append(
            prefix + "'previous_challenge_individual_id' must be a UUID or omitted."
        )

    pokemon = entry.get("pokemon")
    if not isinstance(pokemon, dict):
        return errors + [prefix + "'pokemon' must be an object."]
    if not _text(pokemon.get("name")):
        errors.append(prefix + "pokemon 'name' must be a nonempty single-line string.")
    if not _uuid(pokemon.get("individual_id")):
        errors.append(prefix + "pokemon 'individual_id' must be a canonical UUID.")
    for key, minimum, maximum in (("id", 1, None), ("level", 1, 100)):
        if not _integer(pokemon.get(key), minimum, maximum):
            errors.append(
                prefix + f"pokemon '{key}' must be an integer in its valid range."
            )
    for key, minimum, maximum in (("stats", 1, None), ("ev", 0, 252), ("iv", 0, 31)):
        if key != "stats" and key not in pokemon:
            continue
        values = pokemon.get(key)
        if not isinstance(values, dict) or any(
            not _integer(values.get(stat), minimum, maximum) for stat in STATS
        ):
            errors.append(
                prefix + f"pokemon '{key}' must contain six valid integer stats."
            )
        if (
            isinstance(values, dict)
            and "xp" in values
            and not _integer(values["xp"], 0)
        ):
            errors.append(prefix + f"pokemon '{key}.xp' must be a nonnegative integer.")
    attacks = pokemon.get("attacks")
    if (
        not isinstance(attacks, list)
        or not 1 <= len(attacks) <= 4
        or not all(map(_text, attacks))
    ):
        errors.append(
            prefix + "pokemon 'attacks' must contain one to four move-name strings."
        )
    for key in (
        "xp",
        "base_experience",
        "current_hp",
        "friendship",
        "pokemon_defeated",
    ):
        if key in pokemon and not _integer(pokemon[key], 0):
            errors.append(prefix + f"pokemon '{key}' must be a nonnegative integer.")
    for key in ("shiny", "everstone", "mega", "is_favorite"):
        if key in pokemon and type(pokemon[key]) is not bool:
            errors.append(prefix + f"pokemon '{key}' must be a boolean.")
    for key in ("ability", "growth_rate", "gender", "tier"):
        if key in pokemon and not _text(pokemon[key]):
            errors.append(
                prefix + f"pokemon '{key}' must be a nonempty single-line string."
            )
    if "type" in pokemon and (
        not isinstance(pokemon["type"], list)
        or not 1 <= len(pokemon["type"]) <= 2
        or not all(map(_text, pokemon["type"]))
    ):
        errors.append(
            prefix + "pokemon 'type' must contain one or two type-name strings."
        )
    return errors


def _unique_object(pairs: list[tuple]) -> dict:
    """Reject ambiguous duplicate JSON keys without disclosing their values."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key.")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    """Reject JavaScript numeric constants that are not valid JSON."""
    raise ValueError("Non-finite numbers are not valid JSON.")


def validate_challenges_payload(raw_data: str) -> tuple[bool, list[str], list[dict]]:
    """Parse and validate a challenge object/array without exposing its contents."""
    try:
        data = json.loads(
            raw_data, object_pairs_hook=_unique_object, parse_constant=_reject_constant
        )
    except (ValueError, TypeError, RecursionError):
        return (
            False,
            ["Invalid JSON syntax (including duplicate keys or non-finite numbers)."],
            [],
        )
    entries = [data] if isinstance(data, dict) else data
    if not isinstance(entries, list) or not entries:
        return False, ["Expected a challenge object or nonempty array."], []
    errors = []
    seen = {key: set() for key in ("month", "code", "individual_id")}
    for index, entry in enumerate(entries):
        errors.extend(validate_challenge_structure(entry, index))
        if not isinstance(entry, dict):
            continue
        pokemon = entry.get("pokemon")
        values = {"month": entry.get("month"), "code": entry.get("code")}
        values["individual_id"] = (
            pokemon.get("individual_id") if isinstance(pokemon, dict) else None
        )
        for key, value in values.items():
            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized in seen[key]:
                    errors.append(f"Entry [{index}]: duplicate {key} in payload.")
                seen[key].add(normalized)
    return not errors, errors, entries


def set_github_output(key: str, value: str) -> None:
    """Write single-line metadata to Actions, without echoing secret data."""
    if not re.fullmatch(r"[a-z_]+", key) or not _text(value):
        raise ValueError("Invalid Actions output.")
    if path := os.environ.get("GITHUB_OUTPUT"):
        with open(path, "a", encoding="utf-8") as output:
            output.write(f"{key}={value}\n")


def _write_catalogue(path: Path, entries: list[dict]) -> None:
    """Replace the catalogue atomically, preserving the old file on write errors."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(entries, stream, indent=4, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
        temporary.chmod(path.stat().st_mode)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def process_monthly_release(
    challenges_file: Path = CHALLENGES_PATH,
    raw_secret_data: str | None = None,
    force: bool = False,
    override_month: str | None = None,
    reference_date: datetime.date | None = None,
    dry_run: bool = False,
) -> bool:
    """Append only the eligible month, or return False for an unchanged/dry run.

    Automatic runs select next month on the final UTC day, otherwise the current
    month for recovery. Forced runs default to next month. An explicit target
    still obeys the reveal date unless force is also selected.
    """
    set_github_output("updated", "false")
    today = reference_date or datetime.datetime.now(datetime.timezone.utc).date()
    target = (
        month_date(override_month)
        if override_month
        else month_date(
            get_target_month(today)
            if force or should_release(today)
            else month_name(today)
        )
    )
    if not force and today < target - datetime.timedelta(days=1):
        print("The target month's reveal window has not begun; no changes.")
        return False

    existing = json.loads(
        challenges_file.read_text(encoding="utf-8"),
        object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
    )
    if not isinstance(existing, list):
        raise ValueError("Published catalogue must be a JSON array.")
    if existing:
        valid, errors, _ = validate_challenges_payload(json.dumps(existing))
        if not valid:
            raise ValueError("Invalid published catalogue: " + "; ".join(errors))
    by_month = {entry["month"]: entry for entry in existing}
    target_name = month_name(target)
    if target_name in by_month:
        print(f"{target_name} is already published; no changes.")
        return False

    if raw_secret_data is None:
        raw_secret_data = os.environ.get("UPCOMING_MONTHLY_CHALLENGES", "")
    valid, errors, upcoming = validate_challenges_payload(raw_secret_data)
    if not valid:
        raise ValueError("Invalid UPCOMING_MONTHLY_CHALLENGES: " + "; ".join(errors))
    candidate = next(
        (entry for entry in upcoming if entry["month"] == target_name), None
    )
    if candidate is None:
        raise ValueError(
            "The eligible month is missing from UPCOMING_MONTHLY_CHALLENGES."
        )
    for entry in existing:
        if candidate["code"].lower() == entry["code"].lower():
            raise ValueError("Candidate code is already used by a published month.")
        if candidate["pokemon"]["individual_id"] == entry["pokemon"]["individual_id"]:
            raise ValueError(
                "Candidate individual_id is already used by a published month."
            )

    previous_name = month_name(target - datetime.timedelta(days=1))
    previous = by_month.get(previous_name)
    if previous is None and existing:
        raise ValueError("The preceding calendar month must be published first.")
    if previous is not None:
        previous_id = previous["pokemon"]["individual_id"]
        if candidate.get("previous_challenge_individual_id") not in (
            None,
            "",
            previous_id,
        ):
            raise ValueError(
                "Candidate previous_challenge_individual_id does not match the preceding month."
            )
        candidate["previous_challenge_individual_id"] = previous_id

    if dry_run:
        print(f"Dry run: {target_name} is valid and eligible; no changes.")
        return False
    _write_catalogue(challenges_file, existing + [candidate])
    set_github_output("month", target_name)
    set_github_output("release_month", target.strftime("%Y-%m"))
    set_github_output("updated", "true")
    print(f"Prepared {target_name} for publication.")
    return True


def cmd_validate(target_input: str) -> int:
    """Validate a local file or inline JSON; report only counts and field errors."""
    raw = target_input
    if not target_input.lstrip().startswith(("[", "{")):
        try:
            path = Path(target_input)
            if path.is_file():
                raw = path.read_text(encoding="utf-8")
        except OSError:
            print("Could not read the validation file.", file=sys.stderr)
            return 1
    valid, errors, entries = validate_challenges_payload(raw)
    if not valid:
        print("Validation failed: " + "; ".join(errors), file=sys.stderr)
        return 1
    print(f"Format is valid: {len(entries)} challenge(s).")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run validation or prepare a release, returning a failing status on errors."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--validate", metavar="FILE_OR_JSON", help="Validate without revealing content."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Explicitly allow an early reveal; defaults to next month.",
    )
    parser.add_argument(
        "--target-month", help="Exact English month, e.g. 'November 2026'."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the release without writing anything.",
    )
    parser.add_argument("--challenges-file", type=Path, default=CHALLENGES_PATH)
    args = parser.parse_args(argv)
    try:
        if args.validate is not None:
            return cmd_validate(args.validate)
        process_monthly_release(
            challenges_file=args.challenges_file,
            force=args.force
            or os.environ.get("FORCE_RELEASE", "").lower() in ("true", "1", "yes"),
            override_month=args.target_month or os.environ.get("TARGET_MONTH") or None,
            dry_run=args.dry_run
            or os.environ.get("DRY_RUN", "").lower() in ("true", "1", "yes"),
        )
    except (ValueError, OSError, RecursionError, OverflowError) as exc:
        # Validation errors above contain only static field names. Never render
        # JSONDecodeError or filesystem messages that could contain input text.
        message = (
            str(exc)
            if type(exc) is ValueError
            else "Unable to read or write challenge data."
        )
        print("Release failed: " + message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
