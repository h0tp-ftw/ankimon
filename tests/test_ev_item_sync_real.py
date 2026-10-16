"""Run the EV/mobile race proof in a process without unit-test module mocks."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.tier2
def test_real_ev_item_preserves_background_mobile_rewards():
    result = subprocess.run(
        [sys.executable, "-m", "harness.checks.probe_real_ev_item_sync"],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True,
        text=True,
        timeout=90,
    )
    if result.returncode == 77 and "NO_QT:" in result.stdout:
        pytest.skip("requires the real-Qt Tier-2 environment")
    assert result.returncode == 0, result.stdout + result.stderr
