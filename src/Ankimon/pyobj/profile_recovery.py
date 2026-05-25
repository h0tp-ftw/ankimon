"""
Profile recovery + file-sync conflict detection.

Background
----------
Ankimon moved trainer data (cash/level/xp/name) from per-file ``config.obf``
into a single binary ``ankimon.db``. Users who keep their Anki folder in a
file-sync service (Syncthing / Dropbox / iCloud / OneDrive) and run Ankimon on
more than one device can have that binary DB clobbered or rolled back, because
those tools cannot merge a SQLite file -- one device's copy simply overwrites
the other's, wiping cash and progress.

The old ``config.obf`` files survive these events as recoverable text snapshots
(including the ``*.sync-conflict-*`` copies the sync tools leave behind), so the
pre-wipe value is almost always still on disk. This module:

1. ``recover_wiped_trainer_data`` -- one-time, heavily-guarded restore of the
   trainer block from the best surviving ``config.obf`` snapshot, but only when
   the live DB looks freshly reset to zero.
2. ``warn_if_synced_folder`` -- one-time warning when sync-conflict files are
   found, so users can exclude the folder from sync before it happens again.

Both are best-effort and must never break startup, so every path is wrapped.
"""

import base64
import datetime
import json
import shutil
from pathlib import Path

# Matches AnkimonDataSync._OBFUSCATION_KEY (config.obf XOR key).
_OBFUSCATION_KEY = "H0tP-!s-N0t-4-C@tG!rL_v2".encode("utf-8")

# Restored together so a recovered profile is internally consistent.
_TRAINER_KEYS = (
    "trainer.name", "trainer.sprite", "trainer.id",
    "trainer.cash", "trainer.level", "trainer.xp",
)

_REPAIR_FLAG = "trainer_cash_repair_v1"
_SYNC_WARN_FLAG = "sync_conflict_warning_v1"


def _deobfuscate(text: str) -> dict:
    """Reverse of AnkimonDataSync._obfuscate_data, tolerant of the saved header."""
    if "---DATA_START---" in text:
        text = text.split("---DATA_START---")[1]
    elif "\n---" in text:
        text = text.split("\n---")[1]
    raw = base64.b64decode(text)
    decoded = bytes(b ^ _OBFUSCATION_KEY[i % len(_OBFUSCATION_KEY)] for i, b in enumerate(raw))
    return json.loads(decoded.decode("utf-8"))


def _to_int(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _scan_dirs(user_path: Path):
    """Folders that may hold config snapshots / conflict files.

    Deliberately scoped (top level + json/ archive + backups/) so we never walk
    the huge sprites/ tree that lives under user_files.
    """
    dirs = [user_path, user_path / "json"]
    backups = user_path / "backups"
    if backups.is_dir():
        dirs += [d for d in backups.iterdir() if d.is_dir()]
    return [d for d in dirs if d.is_dir()]


def _candidate_snapshots(user_path: Path):
    """All readable config.obf snapshots with trainer.cash > 0, newest first.

    Catches the live config.obf, the archived json/config.obf, and every
    ``config.sync-conflict-*.obf`` copy left by a sync tool.
    """
    seen = set()
    candidates = []
    for d in _scan_dirs(user_path):
        for p in d.glob("*.obf"):
            if p in seen:
                continue
            seen.add(p)
            try:
                data = _deobfuscate(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(data, dict) and _to_int(data.get("trainer.cash")) > 0:
                try:
                    mtime = p.stat().st_mtime
                except OSError:
                    mtime = 0
                candidates.append((mtime, p, data))
    candidates.sort(key=lambda c: c[0], reverse=True)
    return candidates


def recover_wiped_trainer_data(db, settings_obj, user_path, logger=None) -> bool:
    """Restore the trainer block once if the DB looks wiped and a snapshot exists.

    Guardrails:
      * runs at most once per actual repair -- the metadata flag is set after a
        restore, or when a wipe is seen but no usable snapshot exists; a healthy
        profile is left unflagged so a *later* wipe is still recoverable;
      * only acts when the DB shows a *full reset* (cash, level and xp all 0) --
        this distinguishes a wipe from a player who merely spent down to 0;
      * only when a surviving snapshot has cash > 0;
      * backs up ankimon.db before writing.

    Returns True if a restore was performed.
    """
    try:
        if db is None or settings_obj is None:
            return False
        if db.get_metadata(_REPAIR_FLAG):
            return False

        cash = _to_int(db.get_config_value("trainer.cash", 0))
        level = _to_int(db.get_config_value("trainer.level", 0))
        xp = _to_int(db.get_config_value("trainer.xp", 0))
        if not (cash == 0 and level == 0 and xp == 0):
            # Healthy or legitimately-spent profile -- leave it alone, but do NOT
            # set the repair flag: a *later* sync wipe must still be recoverable.
            return False

        user_path = Path(user_path)
        candidates = _candidate_snapshots(user_path)
        if not candidates:
            db.set_metadata(_REPAIR_FLAG, "true")
            return False

        _, src_path, data = candidates[0]  # newest snapshot with cash > 0

        # Back up the DB before touching it.
        db_path = user_path / db.DB_FILENAME
        if db_path.is_file():
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            shutil.copy2(db_path, db_path.with_name(f"{db.DB_FILENAME}.bak-{stamp}"))

        restored = {k: data[k] for k in _TRAINER_KEYS if k in data}
        db.save_all_config(restored)  # one transaction -> never a partial restore
        for k, v in restored.items():
            settings_obj.config[k] = v
        try:
            settings_obj.compute_gui_config()
        except Exception:
            pass
        db.set_metadata(_REPAIR_FLAG, "true")

        if logger is not None:
            logger.log_and_showinfo(
                "info",
                "Ankimon restored your trainer data, which had been reset to "
                f"zero (cash: {restored.get('trainer.cash', 0)}). Recovered from "
                f"'{src_path.name}'. A backup of the database was saved next to it. "
                "Please restart Anki to fully apply the restore. If the recovered "
                "value is wrong, tools/ankimon_cash_recovery.py lets you pick a "
                "different snapshot.",
            )
        return True
    except Exception as e:
        if logger is not None:
            try:
                logger.log("error", f"Ankimon trainer-data recovery failed: {e}")
            except Exception:
                pass
        return False


def _has_sync_conflicts(user_path: Path) -> bool:
    """True if any file-sync conflict file is present in the data folders."""
    for d in _scan_dirs(user_path):
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for p in entries:
            n = p.name.lower()
            if "sync-conflict" in n or "conflicted copy" in n:
                return True
    return False


def warn_if_synced_folder(db, user_path, logger=None) -> bool:
    """Warn once if file-sync conflict files are present in the data folder.

    Their presence means the Anki folder is being synced across devices, which
    is the main cause of the binary-DB wipe. Returns True if a warning was shown.
    """
    try:
        if db is None or db.get_metadata(_SYNC_WARN_FLAG):
            return False

        if not _has_sync_conflicts(Path(user_path)):
            # Don't set the flag -- we want to warn later if conflicts appear.
            return False

        from aqt import mw
        from aqt.utils import showWarning
        showWarning(
            "Ankimon detected file-sync conflict files in its data folder, which "
            "means your Anki folder is being synced across devices (Syncthing, "
            "Dropbox, iCloud or OneDrive).\n\n"
            "Ankimon now stores your progress in a single database file that these "
            "tools cannot merge, so syncing can corrupt or wipe your cash and "
            "Pokémon.\n\n"
            "Recommended: don't run Ankimon on two synced devices at once, and "
            "exclude the Ankimon addon folder from your sync tool.",
            title="Ankimon: data-sync risk detected",
            parent=mw,
        )
        # Only suppress future warnings once the dialog has actually shown -- if the
        # import or showWarning above raises, we fall through and retry next start.
        db.set_metadata(_SYNC_WARN_FLAG, "true")
        return True
    except Exception as e:
        if logger is not None:
            try:
                logger.log("error", f"Ankimon sync-conflict check failed: {e}")
            except Exception:
                pass
        return False
