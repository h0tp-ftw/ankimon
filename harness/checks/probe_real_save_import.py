"""Tier-2 probe: a staged save import survives a real restart of the real add-on.

The unit tests around ``save_import`` drive the module with a faked Anki host and
a database nobody else has open. The guarantee users actually depend on spans two
processes and the real boot order, so this probe plays it out for real:

  1. boot the genuine add-on, mark the live save, stage an import of a different
     save while the runtime holds that database open, then KEEP EDITING -- the
     case the dialog explicitly invites, and the one where a wrongly reported
     abort would cost progress;
  2. exit, and boot the add-on again on the same user path, exactly as a user
     restarting Anki does.

Then it checks what the dialog promised: the imported save is what the runtime is
now playing, the final progress from step 1 (post-staging edits included) is in the
retained recovery copy, and nothing is left staged to install a second time.

Run (after sourcing the Tier-2 env file):
    python -m harness.checks.probe_real_save_import
"""

import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

# Config keys this probe writes. Real keys are avoided deliberately: the add-on
# rewrites its own settings during boot, and a marker it owns could be restored
# from somewhere else and pass the check for the wrong reason.
BEFORE = "ankimon.probe.save_import.before"
AFTER = "ankimon.probe.save_import.after"
INCOMING = "ankimon.probe.save_import.incoming"

HANDOFF = "probe_save_import_handoff.json"


def _boot(user_path):
    from harness.real_env import start_real_session

    return start_real_session(user_path=str(user_path))


def _phase_one(user_path: pathlib.Path) -> int:
    """Mark the save, stage an import over it, then keep playing."""
    session = _boot(user_path)
    db = session.services.db
    target = pathlib.Path(db.db_path)
    db.set_config_value(BEFORE, "progress-before-the-import")

    # The incoming save is a copy of the live one carrying a different marker, so
    # it passes the importer's validity checks the way a real exported save does.
    incoming = user_path / "incoming-save.db"
    source = sqlite3.connect(target.as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        destination = sqlite3.connect(str(incoming), timeout=30)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()
    stamp = sqlite3.connect(str(incoming), timeout=30)
    try:
        stamp.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
                      (INCOMING, "the-imported-save"))
        stamp.commit()
    finally:
        stamp.close()

    from Ankimon.save_import import stage_import

    pending = stage_import(incoming, target)

    # "If you keep editing, this import stays pending" -- so edit. This is the
    # progress the recovery copy has to hold after the restart.
    db.set_config_value(AFTER, "progress-after-staging")

    # The import must be PENDING, not applied: the live save carries on unchanged
    # until the next full start. Without this the restart check below could pass
    # for the wrong reason, on an import that never waited at all.
    from Ankimon.save_import import pending_import_info

    assert db.get_config_value(INCOMING) is None, "the import replaced the live save in place"
    assert db.get_config_value(AFTER) == "progress-after-staging"
    assert pending_import_info(target) is not None, "nothing was left staged to install"

    (user_path / HANDOFF).write_text(json.dumps({
        "target": str(target),
        "recovery_path": str(pending["recovery_path"]),
        "token": pending["token"],
    }), encoding="utf-8")
    print(f"  phase 1: staged {pending['token'][:12]} over {target.name}, kept editing")
    return 0


def _config(path: pathlib.Path, key: str):
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
        return None if row is None else row[0]
    finally:
        conn.close()


def _phase_two(user_path: pathlib.Path) -> int:
    """Restart, and check the promise the import dialog made."""
    handoff = json.loads((user_path / HANDOFF).read_text(encoding="utf-8"))
    target = pathlib.Path(handoff["target"])
    recovery = pathlib.Path(handoff["recovery_path"])

    session = _boot(user_path)
    db = session.services.db

    # 1. The runtime is playing the imported save, read through the add-on's own
    #    database object rather than the file, because that is what the game uses.
    assert db.get_config_value(INCOMING) == "the-imported-save", (
        "the staged import did not install on restart")
    assert db.get_config_value(AFTER) is None, (
        "the runtime is still on the pre-import save")

    # 2. The final pre-import progress is retained, including what was played
    #    after staging. Losing this is the failure the recovery copy exists for.
    assert recovery.is_file(), f"no recovery copy at {recovery}"
    assert _config(recovery, BEFORE) == "progress-before-the-import", (
        "the recovery copy is missing progress from before staging")
    assert _config(recovery, AFTER) == "progress-after-staging", (
        "the recovery copy predates the play that followed staging")

    # 3. Nothing is left armed to install a second time over the new save.
    from Ankimon.save_import import pending_import_info

    assert pending_import_info(target) is None, "an import is still pending after install"
    staging = target.parent / f".ankimon-import-{target.name}"
    leftovers = sorted(p.name for p in staging.glob("*")) if staging.is_dir() else []
    assert not leftovers, f"staging directory still holds {leftovers}"

    print(f"  phase 2: installed, recovery verified at {recovery.name}")
    return 0


def main() -> int:
    phase = None
    user_path = None
    for argument in sys.argv[1:]:
        if argument.startswith("--phase="):
            phase = argument.split("=", 1)[1]
        elif argument.startswith("--user="):
            user_path = pathlib.Path(argument.split("=", 1)[1])

    if phase == "1":
        return _phase_one(user_path)
    if phase == "2":
        return _phase_two(user_path)

    # Orchestrator. Each phase is a separate process because that is the thing
    # being tested: the install runs before any runtime exists, and the module
    # singletons an in-process reload would keep are exactly what it refuses.
    user_path = pathlib.Path(tempfile.mkdtemp(prefix="ankimon_import_probe_"))
    environment = dict(os.environ)
    environment.setdefault("QT_QPA_PLATFORM", "offscreen")
    for step in ("1", "2"):
        result = subprocess.run(
            [sys.executable, "-m", "harness.checks.probe_real_save_import",
             f"--phase={step}", f"--user={user_path}"],
            cwd=str(pathlib.Path(__file__).resolve().parents[2]),
            env=environment,
        )
        if result.returncode != 0:
            print(f"probe_real_save_import: phase {step} FAILED", file=sys.stderr)
            return result.returncode
    shutil.rmtree(user_path, ignore_errors=True)
    print("probe_real_save_import: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
