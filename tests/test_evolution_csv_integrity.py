"""Data-integrity tests for the bundled ``pokemon_evolution.csv``.

Pure data tests: they read the two bundled CSVs directly and import nothing
from ``Ankimon``, so they stay fast and need none of the ``aqt`` stubbing the
behavioural evolution suites do.

Background: the first column used to be PokeAPI's sequential evolution-row id,
which looked like a species id but was not one. It was remapped to the actual
pre-evolution species and renamed ``evolves_from_species_id`` to match the
column of the same name in ``pokemon_species.csv``. These tests pin that
mapping (so a future regeneration cannot silently reintroduce the offset) and
pin the fact that the file has no unique key, which is why
``pokedex_functions._load_poke_evo_cache`` returns a list rather than a dict.
"""

import csv
from pathlib import Path

import pytest

_DATA = Path(__file__).parent.parent / "src" / "Ankimon" / "data_files"
_EVO_CSV = _DATA / "pokemon_evolution.csv"
_SPECIES_CSV = _DATA / "pokemon_species.csv"


def _rows(path):
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def evo_rows():
    return _rows(_EVO_CSV)


@pytest.fixture(scope="module")
def species_by_id():
    return {row["id"]: row for row in _rows(_SPECIES_CSV)}


def test_header_names_the_pre_evolution_species(evo_rows):
    """The first column is the pre-evolution species, and says so."""
    assert evo_rows, "pokemon_evolution.csv is empty"
    assert "evolves_from_species_id" in evo_rows[0]
    # A bare ``id`` would imply a unique row key, which this file does not have.
    assert "id" not in evo_rows[0]


def test_every_row_matches_pokemon_species_csv(evo_rows, species_by_id):
    """``evolves_from_species_id`` agrees with ``pokemon_species.csv``.

    For each row, the species named in ``evolved_species_id`` must record the
    row's ``evolves_from_species_id`` as its own pre-evolution. This is the
    check that would have caught the original off-by-N id column.
    """
    mismatches = []
    for row in evo_rows:
        evolved = row["evolved_species_id"]
        species = species_by_id.get(evolved)
        assert species is not None, f"unknown evolved_species_id {evolved!r}"
        expected = species.get("evolves_from_species_id") or ""
        if row["evolves_from_species_id"] != expected:
            mismatches.append(
                f"{evolved} ({species.get('identifier')}): "
                f"csv says {row['evolves_from_species_id']!r}, "
                f"species says {expected!r}"
            )
    assert not mismatches, "evolution rows disagree with species data:\n" + "\n".join(
        mismatches
    )


def test_pre_evolution_ids_are_real_species(evo_rows, species_by_id):
    for row in evo_rows:
        pre = row["evolves_from_species_id"]
        assert pre, f"row for {row['evolved_species_id']} has no pre-evolution"
        assert pre in species_by_id, f"unknown pre-evolution species {pre!r}"


def test_no_evolution_rules_were_lost(evo_rows, species_by_id):
    """Every species with a pre-evolution keeps at least one evolution row.

    Five gen-8/9 species (Melmetal, Dipplin, Sinistcha, Archaludon, Hydrapple)
    have never had rows in this file; they are pinned as known gaps so the
    number cannot grow unnoticed.
    """
    known_gaps = {"809", "1011", "1013", "1018", "1019"}
    covered = {row["evolved_species_id"] for row in evo_rows}
    missing = {
        sid
        for sid, row in species_by_id.items()
        if row.get("evolves_from_species_id") and sid not in covered
    }
    assert missing == known_gaps, f"evolution coverage changed: {missing ^ known_gaps}"


def test_file_has_no_unique_key(evo_rows):
    """Documents why the loader must not key a dict on either species column.

    Branching evolutions repeat ``evolves_from_species_id`` and multi-method
    evolutions repeat ``evolved_species_id``, so both would collapse rows.
    """
    froms = [row["evolves_from_species_id"] for row in evo_rows]
    intos = [row["evolved_species_id"] for row in evo_rows]
    assert len(set(froms)) < len(froms)
    assert len(set(intos)) < len(intos)
