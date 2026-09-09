"""Run the real-engine probe in isolation from the suite's engine mocks."""
import subprocess
import sys
from pathlib import Path


def test_real_stancechange_outcomes():
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, str(root / "harness/checks/probe_stancechange.py")],
        cwd=root, capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
