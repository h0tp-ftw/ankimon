"""Make the repo root importable for tests.

`harness/` lives at the repo root, but pytest's default prepend import mode
only adds the test file's directory (`tests/`) to sys.path, so tests that
import from `harness` fail at collection. Adding the repo root here fixes
that regardless of how pytest is invoked.
"""
import sys
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
