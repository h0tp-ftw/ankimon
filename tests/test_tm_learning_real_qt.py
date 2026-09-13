"""Run native modal TM regressions outside the suite's Qt/Anki mocks."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.tier2
def test_tm_learning_real_qt():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    available = subprocess.run(
        [sys.executable, "-c", "import PyQt6.QtWidgets"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    if available.returncode:
        pytest.skip(
            "Requires the optional real-Qt Tier-2 environment: "
            + (available.stderr.strip() or "PyQt6 import failed")
        )
    result = subprocess.run(
        [sys.executable, "-m", "harness.checks.probe_real_tm_learnsets"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
