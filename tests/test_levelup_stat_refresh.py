"""Keep the level-up probe isolated from the unit suite's module mocks."""

import subprocess
import sys
from pathlib import Path


def test_real_levelup_stat_refresh():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "harness/checks/probe_levelup_stats.py")],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
