"""Keep real Qt isolated from the suite's Anki/Qt import stubs."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_move_selection_real_qt():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    available = subprocess.run(
        [sys.executable, "-c", "import PyQt6.QtWidgets"],
        capture_output=True, text=True, env=env, timeout=30,
    )
    if available.returncode and "No module named 'PyQt6'" in available.stderr:
        pytest.skip("Requires the optional real-Qt Tier-2 environment")
    assert available.returncode == 0, available.stdout + available.stderr
    result = subprocess.run(
        [sys.executable, "-m", "harness.checks.probe_real_move_selection"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
