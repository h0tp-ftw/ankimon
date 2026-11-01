"""Regression coverage for public release timing, secrecy, and catalogue integrity."""

import datetime
import json
from uuid import NAMESPACE_DNS, uuid5

import pytest

from scripts import release_monthly_challenge as release


@pytest.fixture(autouse=True)
def release_environment(monkeypatch):
    """Never inherit a developer's release flags, secret, or Actions output file."""
    for key in (
        "UPCOMING_MONTHLY_CHALLENGES",
        "FORCE_RELEASE",
        "TARGET_MONTH",
        "DRY_RUN",
        "GITHUB_OUTPUT",
    ):
        monkeypatch.delenv(key, raising=False)


def sample_challenge(month="November 2026", name="Pikachu"):
    """Build a synthetic reward with a stable, month-specific database identity."""
    return {
        "code": f"MONTHLY-{month.replace(' ', '-').upper()}-{name.upper()}",
        "month": month,
        "description": f"Challenge for {month}",
        "defeat_threshold": 100,
        "pokemon": {
            "name": name,
            "id": 25,
            "level": 1,
            "stats": {"hp": 35, "atk": 55, "def": 40, "spa": 50, "spd": 50, "spe": 90},
            "attacks": ["Thunder Shock", "Quick Attack"],
            "individual_id": str(uuid5(NAMESPACE_DNS, month + name)),
        },
    }


@pytest.fixture
def catalogue(tmp_path):
    """Provide the preceding month in a temporary public catalogue."""
    path = tmp_path / "monthly_challenges.json"
    path.write_text(json.dumps([sample_challenge("October 2026")]), encoding="utf-8")
    return path


def publish(path, entries=None, **kwargs):
    """Release synthetic secret entries at a fixed UTC month boundary."""
    options = {"reference_date": datetime.date(2026, 10, 31)}
    options.update(kwargs)
    return release.process_monthly_release(
        challenges_file=path,
        raw_secret_data=json.dumps(
            entries if entries is not None else [sample_challenge()]
        ),
        **options,
    )


@pytest.mark.parametrize(
    "date,eligible,target",
    [
        (datetime.date(2026, 10, 15), False, "November 2026"),
        (datetime.date(2026, 10, 30), False, "November 2026"),
        (datetime.date(2026, 10, 31), True, "November 2026"),
        (datetime.date(2026, 12, 31), True, "January 2027"),
        (datetime.date(2027, 2, 27), False, "March 2027"),
        (datetime.date(2027, 2, 28), True, "March 2027"),
        (datetime.date(2028, 2, 28), False, "March 2028"),
        (datetime.date(2028, 2, 29), True, "March 2028"),
        (datetime.date(2026, 4, 30), True, "May 2026"),
    ],
)
def test_calendar_boundaries(date, eligible, target):
    """Reveal only on the final UTC day and always force the next calendar month."""
    assert release.should_release(date) is eligible
    assert release.get_target_month(date) == target


def test_append_only_selected_month_and_link_previous(
    catalogue, monkeypatch, tmp_path, capsys
):
    """Publish one entry, keeping later secret entries out of the file and logs."""
    output = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    previous = json.loads(catalogue.read_text())
    assert publish(
        catalogue,
        [sample_challenge(), sample_challenge("December 2026", "FUTURE_SECRET")],
    )
    data = json.loads(catalogue.read_text())
    assert data[:-1] == previous
    assert data[-1]["month"] == "November 2026"
    assert (
        data[-1]["previous_challenge_individual_id"]
        == previous[0]["pokemon"]["individual_id"]
    )
    assert (
        "FUTURE_SECRET"
        not in catalogue.read_text() + output.read_text() + capsys.readouterr().out
    )
    assert "updated=true\n" in output.read_text()
    assert "release_month=2026-11\n" in output.read_text()
    assert "pokemon=" not in output.read_text()
    before = catalogue.read_bytes()
    assert not publish(catalogue)
    assert catalogue.read_bytes() == before


def test_force_without_target_releases_next_month(catalogue):
    """A mid-month forced dispatch must not silently select the existing month."""
    assert publish(catalogue, force=True, reference_date=datetime.date(2026, 10, 15))
    assert json.loads(catalogue.read_text())[-1]["month"] == "November 2026"


@pytest.mark.parametrize("target", [None, "November 2026", "December 2026"])
def test_no_early_release_without_force(catalogue, target):
    """Selecting a target alone never overrides the reveal deadline."""
    before = catalogue.read_bytes()
    assert not publish(
        catalogue, override_month=target, reference_date=datetime.date(2026, 10, 15)
    )
    assert catalogue.read_bytes() == before


@pytest.mark.parametrize("day", [1, 2, 15, 29])
def test_recover_after_missed_schedule(catalogue, day):
    """A delayed job can still publish the current month after the 1st."""
    assert publish(catalogue, reference_date=datetime.date(2026, 11, day))
    assert json.loads(catalogue.read_text())[-1]["month"] == "November 2026"


def test_link_uses_calendar_not_array_order(catalogue):
    """An unsorted public catalogue must still link to October's identity."""
    october = sample_challenge("October 2026")
    catalogue.write_text(json.dumps([october, sample_challenge("September 2026")]))
    assert publish(catalogue)
    assert (
        json.loads(catalogue.read_text())[-1]["previous_challenge_individual_id"]
        == october["pokemon"]["individual_id"]
    )


@pytest.mark.parametrize(
    "problem", ["code", "individual_id", "wrong_previous", "missing_previous"]
)
def test_conflicting_history_is_rejected_without_writing(catalogue, problem):
    """Protect claim identity and shiny eligibility across monthly releases."""
    candidate = sample_challenge()
    previous = sample_challenge("October 2026")
    if problem == "code":
        candidate["code"] = previous["code"]
    elif problem == "individual_id":
        candidate["pokemon"]["individual_id"] = previous["pokemon"]["individual_id"]
    elif problem == "wrong_previous":
        candidate["previous_challenge_individual_id"] = str(
            uuid5(NAMESPACE_DNS, "wrong")
        )
    else:
        catalogue.write_text(json.dumps([sample_challenge("September 2026")]))
    before = catalogue.read_bytes()
    with pytest.raises(ValueError):
        publish(catalogue, [candidate])
    assert catalogue.read_bytes() == before


def test_explicit_correct_previous_id_is_preserved(catalogue):
    """An author-supplied correct predecessor is accepted unchanged."""
    candidate = sample_challenge()
    candidate["previous_challenge_individual_id"] = sample_challenge("October 2026")[
        "pokemon"
    ]["individual_id"]
    assert publish(catalogue, [candidate])
    assert json.loads(catalogue.read_text())[-1] == candidate


@pytest.mark.parametrize(
    "field,value",
    [
        ("month", 202611),
        ("month", []),
        ("month", "november 2026"),
        ("month", " November 2026"),
        ("month", "November 2026\nupdated=true"),
        ("code", 123),
        ("code", {}),
        ("code", "x\nupdated=true"),
        ("description", None),
        ("defeat_threshold", False),
        ("defeat_threshold", -1),
        ("previous_challenge_individual_id", 123),
        ("pokemon", []),
    ],
)
def test_invalid_top_level_types_are_errors(field, value):
    """Free-form secret JSON should produce validation errors, never tracebacks."""
    candidate = sample_challenge()
    candidate[field] = value
    valid, errors, _ = release.validate_challenges_payload(json.dumps(candidate))
    assert not valid and errors


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", []),
        ("name", ""),
        ("name", "Pikachu\rnew-output=yes"),
        ("individual_id", None),
        ("individual_id", "not-a-uuid"),
        ("id", True),
        ("id", -1),
        ("id", "25"),
        ("level", 101),
        ("level", 0),
        ("stats", {}),
        ("stats", dict.fromkeys(release.STATS, "35")),
        ("stats", dict.fromkeys(release.STATS, True)),
        ("iv", dict.fromkeys(release.STATS, 32)),
        ("ev", None),
        ("attacks", []),
        ("attacks", [None]),
        ("attacks", ["Tackle"] * 5),
        ("xp", -1),
        ("current_hp", "35"),
        ("shiny", "false"),
        ("type", "Electric"),
        ("growth_rate", []),
    ],
)
def test_invalid_pokemon_fields_are_errors(field, value):
    """Reject data that would break the consumer or silently alter its meaning."""
    candidate = sample_challenge()
    candidate["pokemon"][field] = value
    valid, errors, _ = release.validate_challenges_payload(json.dumps(candidate))
    assert not valid and errors


@pytest.mark.parametrize(
    "payload", ["", "{", "null", "42", "[]", "[null]", '{"x":1,"x":2}', "[NaN]"]
)
def test_invalid_json_shapes(payload):
    """Reject empty, ambiguous, nonstandard, and non-object inputs cleanly."""
    valid, errors, _ = release.validate_challenges_payload(payload)
    assert not valid and errors


@pytest.mark.parametrize("field", ["month", "code", "individual_id"])
def test_duplicate_secret_identities(field):
    """Duplicate month, code, and UUID values are ambiguous release plans."""
    first, second = sample_challenge(), sample_challenge("December 2026")
    if field == "individual_id":
        second["pokemon"][field] = first["pokemon"][field]
    else:
        second[field] = first[field]
    valid, errors, _ = release.validate_challenges_payload(json.dumps([first, second]))
    assert not valid
    assert any(f"duplicate {field}" in error for error in errors)


def test_validation_and_cli_never_disclose_secret(
    catalogue, tmp_path, monkeypatch, capsys
):
    """Success, duplicate errors, and publication failures must keep plans private."""
    secret = sample_challenge(name="UNRELEASED_SECRET_MARKER")
    secret["description"] = "x" * 1000
    raw = json.dumps(secret)
    assert release.cmd_validate(raw) == 0  # Longer than a legal filename.
    path = tmp_path / "secret.json"
    path.write_text(raw)
    assert release.cmd_validate(str(path)) == 0
    monkeypatch.setenv("UPCOMING_MONTHLY_CHALLENGES", json.dumps([secret, secret]))
    assert (
        release.main(
            [
                "--force",
                "--target-month",
                "November 2026",
                "--challenges-file",
                str(catalogue),
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert "UNRELEASED_SECRET_MARKER" not in captured.out + captured.err
    assert "duplicate code" in captured.err


def test_dry_run_is_nonmutating_and_private(catalogue, monkeypatch, tmp_path, capsys):
    """Dry-run workflow dispatches must never signal that a push should happen."""
    output = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv(
        "UPCOMING_MONTHLY_CHALLENGES", json.dumps(sample_challenge(name="SECRET"))
    )
    monkeypatch.setenv("TARGET_MONTH", "November 2026")
    monkeypatch.setenv("FORCE_RELEASE", "true")
    monkeypatch.setenv("DRY_RUN", "true")
    before = catalogue.read_bytes()
    assert release.main(["--challenges-file", str(catalogue)]) == 0
    assert catalogue.read_bytes() == before
    assert output.read_text() == "updated=false\n"
    assert "SECRET" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "raw", ["", "not json", json.dumps(sample_challenge("December 2026"))]
)
def test_missing_or_invalid_due_challenge_fails(catalogue, monkeypatch, raw):
    """A due release with no usable candidate must fail CI instead of looking green."""
    monkeypatch.setenv("UPCOMING_MONTHLY_CHALLENGES", raw)
    before = catalogue.read_bytes()
    assert (
        release.main(
            [
                "--force",
                "--target-month",
                "November 2026",
                "--challenges-file",
                str(catalogue),
            ]
        )
        == 1
    )
    assert catalogue.read_bytes() == before


def test_already_published_does_not_require_secret(catalogue):
    """Idempotent retries can succeed after the consumed secret entry was removed."""
    assert not release.process_monthly_release(
        catalogue, reference_date=datetime.date(2026, 10, 15)
    )


@pytest.mark.parametrize(
    "contents", ["{}", "[null]", "{", '[{"month":"October 2026"}]']
)
def test_invalid_catalogue_is_not_overwritten(catalogue, contents):
    """Do not replace malformed public history with an apparently valid new entry."""
    catalogue.write_text(contents)
    with pytest.raises(ValueError):
        publish(catalogue)
    assert catalogue.read_text() == contents


def test_failed_atomic_replace_keeps_catalogue(catalogue, monkeypatch):
    """A filesystem error must leave the original bytes and no partial temp file."""
    before = catalogue.read_bytes()

    def fail_replace(*args):
        """Simulate an unavailable destination after serialization."""
        raise OSError("simulated write failure")

    monkeypatch.setattr(release.os, "replace", fail_replace)
    with pytest.raises(OSError):
        publish(catalogue)
    assert catalogue.read_bytes() == before
    assert list(catalogue.parent.iterdir()) == [catalogue]


def test_ambiguous_catalogue_keys_are_rejected(catalogue):
    """Reject duplicate keys before JSON parsing can silently discard history."""
    ambiguous = catalogue.read_text().replace('"code":', '"code": "duplicate", "code":')
    catalogue.write_text(ambiguous)
    with pytest.raises(ValueError, match="Duplicate JSON object key"):
        publish(catalogue)
    assert catalogue.read_text() == ambiguous


def test_out_of_range_target_is_a_cli_error(catalogue, capsys):
    """Calendar arithmetic at the minimum supported date must not traceback."""
    assert (
        release.main(
            [
                "--force",
                "--target-month",
                "January 0001",
                "--challenges-file",
                str(catalogue),
            ]
        )
        == 1
    )
    assert "Traceback" not in capsys.readouterr().err


def test_actions_output_rejects_newlines(tmp_path, monkeypatch):
    """Reject attempts to add extra Actions outputs through a value."""
    output = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    with pytest.raises(ValueError):
        release.set_github_output("month", "November 2026\nupdated=true")
    assert not output.exists()


def test_repository_catalogue_satisfies_schema():
    """The publisher must accept the real historical schema before deployment."""
    valid, errors, _ = release.validate_challenges_payload(
        release.CHALLENGES_PATH.read_text()
    )
    assert valid, errors
