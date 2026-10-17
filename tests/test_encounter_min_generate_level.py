"""Encounter minimum regressions use a clean process to avoid suite Qt mocks."""

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("section", ["ancestry", "cached", "encounters", "regional"])
def test_encounter_minimum_level(section):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            str(root / "harness" / "checks" / "probe_encounter_min_level.py"),
            section,
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
