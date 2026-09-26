"""Keep real Qt/Anki module state isolated from the unit suite's import stubs."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


def test_monthly_challenge_real_dialogs():
    result = subprocess.run(
        [sys.executable, "-m", "harness.scenarios.monthly_challenge"],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True,
        text=True,
        timeout=480,
    )
    if result.returncode == 77 and "NO_QT:" in result.stdout:
        pytest.skip("requires Tier-2 PyQt6 environment")
    assert result.returncode == 0, result.stdout + result.stderr
