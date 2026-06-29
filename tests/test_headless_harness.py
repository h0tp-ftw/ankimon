"""
Regression test: the headless agent harness can boot and play Ankimon.

This is the high-value end-to-end test the whole refactor exists to enable — it
runs the *real* battle loop / encounter logic against a throwaway profile with no
Anki and no Qt, and asserts the core loop produces the right observable events
with zero errors.

Isolation: these tests need the GENUINE ``Ankimon`` package booted fresh. The rest
of the unit suite (and this repo's conftest.py) stub ``aqt`` / ``Ankimon.*`` in
sys.modules at import time, which makes an in-process real boot unreliable when
this file runs after them. So — exactly like ``harness/check.py`` — each test runs
its scenario in a CLEAN child interpreter and asserts on a JSON result. That makes
the suite robust (no in-process pollution) without any mocking.

Runs under pytest (CI) or as a plain script:  python3 tests/test_headless_harness.py
"""

import json
import pathlib
import subprocess
import sys

_repo = pathlib.Path(__file__).resolve().parents[1]
_MARKER = "HARNESS_RESULT:"


# Force the Tier-1 contract in the child: NO Qt. This unit suite (integrity_tests)
# installs aqt+PyQt6 (requirements.txt) and runs under xvfb, so an Ankimon leaf
# module's "Qt present" path would construct a QWidget at import with no
# QApplication and SIGABRT the child. The dedicated harness CI (harness.yml) runs
# Tier-1 with no Qt deps at all; we reproduce that by making aqt/PyQt6 unimportable
# in the child, so the guarded modules take their headless no-Qt path.
_BLOCK_QT = (
    "import sys\n"
    "class _NoQt:\n"
    "    _b = ('aqt', 'PyQt6', 'PyQt5')\n"
    "    def find_spec(self, name, path=None, target=None):\n"
    "        if name.split('.')[0] in self._b:\n"
    "            raise ModuleNotFoundError(name + ' blocked: harness Tier-1 is Qt-free')\n"
    "        return None\n"
    "sys.meta_path.insert(0, _NoQt())\n"
)


def _subrun(snippet):
    """Run a harness snippet in a fresh, Qt-free interpreter; return its JSON result.

    The snippet must print ``HARNESS_RESULT:<json>`` once. We isolate in a child
    process so the in-process sys.modules stubs other test files install can't
    break the real Ankimon boot (the same reason check.py shells out per probe),
    and we block Qt so the child runs the genuine Tier-1 (no-Anki/no-Qt) path."""
    code = _BLOCK_QT + "import json\nsys.path.insert(0, %r)\n%s" % (str(_repo), snippet)
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, (
        "harness subprocess failed (rc=%d):\n--- stdout ---\n%s\n--- stderr ---\n%s"
        % (proc.returncode, proc.stdout, proc.stderr)
    )
    for line in reversed(proc.stdout.splitlines()):
        if line.startswith(_MARKER):
            return json.loads(line[len(_MARKER):])
    raise AssertionError("no %s in harness output:\n%s\n%s" % (_MARKER, proc.stdout, proc.stderr))


def test_play_session_runs_without_errors():
    summary = _subrun(
        "from harness.scenarios import smoke_play\n"
        "print(%r + json.dumps(smoke_play.run(verbose=False)))" % _MARKER
    )
    assert summary["caught"] >= 1
    assert summary["defeated"] >= 1
    assert summary["event_counts"].get("battle", 0) > 0
    assert summary["event_counts"].get("encounter", 0) > 0
    assert summary["event_counts"].get("faint", 0) > 0
    assert "error" not in summary["event_counts"], "play produced error events"
    assert summary["collection"] >= 1


def test_state_snapshot_and_single_answer():
    result = _subrun(
        "from harness.driver import Driver\n"
        "d = Driver(settings_overrides={'battle.cards_per_round': 1})\n"
        "st = d.get_state()\n"
        "events = d.answer('good')\n"
        "print(%r + json.dumps({\n"
        "    'state_keys': list(st.keys()),\n"
        "    'max_hp': st['main']['max_hp'],\n"
        "    'enemy_attacks_is_list': isinstance(st['enemy']['attacks'], list),\n"
        "    'has_battle': any(e['type'] == 'battle' for e in events),\n"
        "    'has_error': any(e['type'] == 'error' for e in events),\n"
        "}))" % _MARKER
    )
    for key in ("main", "enemy", "tracker", "collection", "trainer"):
        assert key in result["state_keys"], f"missing state key: {key}"
    assert result["max_hp"] >= 1
    assert result["enemy_attacks_is_list"]
    assert result["has_battle"], "answering produced no battle"
    assert not result["has_error"], "answering produced an error"


def test_auto_battle_mode_cycles():
    result = _subrun(
        "from harness.scenarios import auto_battle\n"
        "r = auto_battle.run(mode=2, answers=30, verbose=False)\n"
        "print(%r + json.dumps(r['event_counts']))" % _MARKER
    )
    assert result.get("encounter", 0) >= 1
    assert "error" not in result


if __name__ == "__main__":
    test_play_session_runs_without_errors()
    test_state_snapshot_and_single_answer()
    test_auto_battle_mode_cycles()
    print("headless harness tests: OK")
