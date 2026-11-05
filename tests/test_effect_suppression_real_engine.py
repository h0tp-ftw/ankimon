"""Exercise production suppression lifecycle outside the suite's engine mocks."""

import subprocess
import sys
from pathlib import Path


def test_real_suppression_lifecycle():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "harness/checks/probe_effect_suppression.py")],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
