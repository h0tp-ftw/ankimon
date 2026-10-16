"""Exercise native evolution rendering outside the suite's Qt/Anki mocks."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.tier2
def test_evolution_sprites_real_qt():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    available = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import importlib.util, sys\n"
                "if importlib.util.find_spec('PyQt6') is None: sys.exit(77)\n"
                "import PyQt6.QtWidgets\n"
            ),
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    if available.returncode == 77:
        pytest.skip("Requires the optional real-Qt Tier-2 environment")
    # An installed but broken Qt environment must fail, especially in Tier-2 CI.
    assert available.returncode == 0, available.stdout + available.stderr
    result = subprocess.run(
        [sys.executable, "-m", "harness.checks.probe_real_evolution_sprites"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
