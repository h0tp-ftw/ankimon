"""Keep real Qt isolated from the suite's Anki/Qt import stubs."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.tier2
def test_move_selection_real_qt():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    available = subprocess.run(
        [sys.executable, "-c", "import PyQt6.QtWidgets"],
        capture_output=True, text=True, env=env, timeout=30,
    )
    if available.returncode:
        # Tier 2 is opt-in (AGENTS.md). A missing wheel, missing native Qt libs
        # ("libEGL.so.1: cannot open shared object file") and a partial install
        # all mean the environment is absent, not that the add-on is broken --
        # only the first of those matched the old literal check. Tier-2 CI
        # installs Qt, so a genuine regression still fails there.
        pytest.skip("Requires the optional real-Qt Tier-2 environment: "
                    + (available.stderr.strip() or "PyQt6 import failed"))
    result = subprocess.run(
        [sys.executable, "-m", "harness.checks.probe_real_move_selection"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
