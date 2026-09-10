"""Run bundled-data checks outside the suite's Pokédex/resource mocks."""

import subprocess
import sys
from pathlib import Path


def test_bundled_tm_form_resolution():
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, str(root / "harness/checks/probe_tm_learnsets.py")],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
