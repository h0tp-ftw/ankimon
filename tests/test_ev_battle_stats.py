"""Keep the ongoing-battle regression isolated from the unit suite's mocks."""

import subprocess
import sys
from pathlib import Path


def test_real_ev_training_battle_stats():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "harness/checks/probe_ev_battle_stats.py")],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
