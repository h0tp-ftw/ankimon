#!/usr/bin/env python3
"""
Ankimon cash / trainer recovery tool.

Read-only by default: scans EVERY trainer-data source in your Ankimon
user_files folder -- the live ankimon.db, the archived json/config.obf, and
every Syncthing/Dropbox/iCloud `.sync-conflict-*.obf` copy -- and prints the
trainer.* values (cash, level, xp) found in each. This lets a user whose cash
was wiped (commonly by a multi-device file-sync conflict on the binary DB)
find their real value across the surviving snapshots.

Usage:
  python3 ankimon_cash_recovery.py [USER_FILES]            # diagnose (read-only)
  python3 ankimon_cash_recovery.py [USER_FILES] --set-cash 8000

If USER_FILES is omitted, it auto-discovers Anki addon folders on macOS.
`--set-cash N` makes a timestamped backup of ankimon.db first, then sets
trainer.cash to N in the DB config table. Nothing is written without --set-cash.
"""
import argparse, base64, datetime, glob, json, os, shutil, sqlite3, sys
from pathlib import Path

KEY = "H0tP-!s-N0t-4-C@tG!rL_v2".encode()
FIELDS = ("trainer.name", "trainer.cash", "trainer.level", "trainer.xp")


def deobfuscate(text: str) -> dict:
    if "---DATA_START---" in text:
        text = text.split("---DATA_START---")[1]
    elif "\n---" in text:
        text = text.split("\n---")[1]
    raw = base64.b64decode(text)
    return json.loads(bytes(b ^ KEY[i % len(KEY)] for i, b in enumerate(raw)).decode("utf-8"))


def trainer_fields(d: dict) -> dict:
    return {k: d.get(k) for k in FIELDS}


def diagnose(uf: Path) -> None:
    print(f"\n=== user_files: {uf} ===")
    found_any = False
    for p in sorted(uf.rglob("*.obf")):
        found_any = True
        try:
            print(f"  OBF {trainer_fields(deobfuscate(p.read_text(encoding='utf-8')))}  <- {p.relative_to(uf)}")
        except Exception as e:
            print(f"  OBF UNREADABLE ({e}) <- {p.relative_to(uf)}")
    for p in sorted(uf.rglob("ankimon.db")):
        found_any = True
        try:
            c = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True)
            c.row_factory = sqlite3.Row
            rows = {r["key"]: r["value"] for r in
                    c.execute("SELECT key, value FROM config WHERE key LIKE 'trainer.%'")}
            c.close()
            print(f"  DB  {rows or 'no trainer.* rows'}  <- {p.relative_to(uf)}")
        except Exception as e:
            print(f"  DB  ERROR ({e}) <- {p.relative_to(uf)}")
    if not found_any:
        print("  (no .obf or ankimon.db found here)")


def set_cash(uf: Path, amount: int) -> None:
    db = uf / "ankimon.db"
    if not db.is_file():
        print(f"  !! no ankimon.db in {uf}; cannot set cash")
        return
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = db.with_name(f"ankimon.db.bak-{stamp}")
    shutil.copy2(db, backup)
    print(f"  backed up {db.name} -> {backup.name}")
    conn = sqlite3.connect(str(db))
    cur = conn.cursor()
    cur.execute("SELECT value FROM config WHERE key = 'trainer.cash'")
    row = cur.fetchone()
    before = row[0] if row else "(absent)"
    cur.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('trainer.cash', ?)", (str(amount),))
    conn.commit()
    conn.close()
    print(f"  trainer.cash: {before} -> {amount}  (in {db})")


def main() -> None:
    ap = argparse.ArgumentParser(description="Ankimon cash recovery (read-only unless --set-cash).")
    ap.add_argument("user_files", nargs="?", help="path to an Ankimon user_files dir")
    ap.add_argument("--set-cash", type=int, metavar="N",
                    help="back up ankimon.db, then set trainer.cash to N in the DB")
    args = ap.parse_args()

    if args.user_files:
        paths = [Path(args.user_files)]
    else:
        paths = [Path(p) for p in glob.glob(os.path.expanduser(
            "~/Library/Application Support/Anki2/addons21/*/user_files"))]
        paths = [p for p in paths if (p / "ankimon.db").exists() or any(p.rglob("*.obf"))]

    if not paths:
        sys.exit("No Ankimon user_files found. Pass the path explicitly.")
    if args.set_cash is not None and len(paths) != 1:
        sys.exit(f"--set-cash needs exactly one user_files dir; found {len(paths)}. Pass the path explicitly.")

    for uf in paths:
        if not uf.is_dir():
            print(f"skip (not a dir): {uf}")
            continue
        diagnose(uf)
        if args.set_cash is not None:
            set_cash(uf, args.set_cash)


if __name__ == "__main__":
    main()
