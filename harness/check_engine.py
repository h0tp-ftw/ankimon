#!/usr/bin/env python3
"""Run the pinned engine's item/suppression regressions without importing Anki.

Requires pytest, requests and an initialized poke_engine submodule. The explicit module
list keeps unrelated legacy engine failures outside this focused gate and makes
missing regression files fail loudly. Add new item regression modules here.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "src" / "Ankimon" / "poke_engine"
TEST_MODULES = (
    "test_after_move_items.py",
    "test_after_move_items_battles.py",
    "test_effect_suppression_abilities.py",
    "test_effect_suppression_adversarial.py",
    "test_effect_suppression_battle.py",
    "test_effect_suppression_instructions.py",
    "test_effect_suppression_items.py",
    "test_effect_suppression_lifecycle.py",
)


def main():
    required = [ENGINE / "__init__.py"]
    required.extend(ENGINE / "tests" / name for name in TEST_MODULES)
    missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
    if missing:
        print(
            "Missing pinned engine regression files:",
            *missing,
            sep="\n",
            file=sys.stderr,
        )
        print("Run: git submodule update --init --recursive", file=sys.stderr)
        return 2

    # Collect the engine as its own package. Collecting below src/Ankimon would
    # also import the addon's __init__ and require a live Anki/Qt environment.
    with tempfile.TemporaryDirectory(prefix="ankimon-engine-check-") as temporary:
        package = Path(temporary) / "poke_engine"
        package.symlink_to(ENGINE, target_is_directory=True)
        env = os.environ.copy()
        env["PYTHONPATH"] = temporary
        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "--import-mode=importlib",
                "-q",
                *(str(package / "tests" / name) for name in TEST_MODULES),
            ],
            cwd=temporary,
            env=env,
        )
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
