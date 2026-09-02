"""
harness/scenarios/sync_fuzz.py — property/fuzz testing for AnkimonDataSync's
file-comparison logic (read_configs/save_configs), the code #529/#627 fixed.

Throws MANY random (content, mtime, existence, corruption) combinations at the
REAL read_configs()/save_configs() and checks one invariant on each: the
objectively current side is always adopted, and a strictly OLDER side never
silently overwrites a genuinely different, newer one. Specifically weighted
toward the exact-mtime-tie boundary that #529's original fix missed and #627
corrected — a hand-picked example test (like the ones in test_sync_hardening.py)
proves that ONE case works; this sweeps the boundary instead of trusting it.

Only ``ankimon.db`` is ever synced in production (SYNC_FILES has one entry), so
every scenario here is a real sqlite db built via sqlite3 directly — not a
synthetic file shape nothing in the real sync path would ever see. What's
randomized per side (local vs cloud):
  * file existence (missing local vs present)
  * content: identical vs different (a real captured_pokemon-shaped table)
  * mtime relationship: large gap either direction, small gap, near-tie
    (sub-resolution), and an EXACT tie — weighted toward the boundary cases
  * occasionally corrupt the cloud copy (invalid sqlite bytes), to confirm a
    corrupt-but-"newer" file never wins over a valid current one (read_configs
    only — save_configs has no integrity gate on the push side, by design)

    python3 harness/scenarios/sync_fuzz.py 500          # 500 random iterations
    python3 harness/scenarios/sync_fuzz.py --seed 12345 # reproduce ONE case, verbose

Tier 1 (aqt/PyQt6 stubbed, no real Qt) — fast, runs anywhere. Deterministic per
seed. Companion to fuzz.py (game logic) and mega_fuzz.py (GUI) — same house
style: seed-reproducible, --seed to replay a single failure verbatim.
"""

import os
import random
import shutil
import sqlite3
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_SRC = Path(__file__).resolve().parents[2] / "src"

# Tier-1 house pattern (matches tests/test_sync_hardening.py): stub aqt/PyQt6 so
# the real ankimon_sync.py module imports Qt-free.
for _name in (
    "aqt", "aqt.qt", "aqt.utils", "aqt.gui_hooks", "aqt.operations",
    "aqt.theme", "aqt.sound", "aqt.webview", "aqt.main",
    "anki", "anki.hooks", "anki.collection", "anki.utils",
    "PyQt6", "PyQt6.QtGui", "PyQt6.QtWidgets", "PyQt6.QtCore",
    "PyQt6.QtWebChannel", "PyQt6.QtWebEngineWidgets",
):
    sys.modules.setdefault(_name, MagicMock())

for _pkg in ("Ankimon", "Ankimon.functions", "Ankimon.pyobj", "Ankimon.ankimon_items_web"):
    _existing = sys.modules.get(_pkg)
    if _existing is None or not hasattr(_existing, "__path__"):
        _mod = types.ModuleType(_pkg)
        _mod.__path__ = [str(_SRC / _pkg.replace(".", "/"))]
        _mod.__package__ = _pkg
        sys.modules[_pkg] = _mod

if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from Ankimon.pyobj.ankimon_sync import AnkimonDataSync  # noqa: E402

BASE_EPOCH = 1750000000.0  # arbitrary fixed anchor — fully deterministic, no wall clock


def _make_db(path, row_value, corrupt=False):
    if corrupt:
        path.write_bytes(b"not a real sqlite file " + str(row_value).encode() + b"\x00" * 600)
        return
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE captured_pokemon (id INTEGER, data TEXT)")
    conn.execute("INSERT INTO captured_pokemon VALUES (1, ?)", (str(row_value),))
    conn.commit()
    conn.close()


def _mtime_delta(rng):
    """A spread of mtime gaps, weighted toward the boundary cases (exact tie,
    near-tie) where #529's original fix broke — not just the 'obvious' large
    gap a hand-picked example test would use."""
    return rng.choice([
        0.0, 0.0, 0.0, 0.0,                       # exact tie — heavily weighted
        rng.uniform(-1e-4, 1e-4),                 # near-tie, sub filesystem-resolution
        rng.uniform(-5, 5),                       # small gap, either direction
        rng.uniform(-86400 * 30, 86400 * 30),     # large gap, either direction (up to 30 days)
    ])


def _new_sync(tmp, rng):
    """Build one AnkimonDataSync instance wired to a fresh local/cloud pair."""
    local_dir, cloud_dir = tmp / f"local{rng.random()}", tmp / f"cloud{rng.random()}"
    local_dir.mkdir(parents=True)
    cloud_dir.mkdir(parents=True)
    local = local_dir / "ankimon.db"
    cloud = cloud_dir / "ankimon.db"

    ds = AnkimonDataSync()
    ds._migrate_legacy_files = lambda: []
    ds._ensure_sync_folder_exists = lambda: True
    ds._get_source_path = lambda fn: local
    ds._get_media_path = lambda fn: cloud
    ds._backup_before_overwrite = lambda *a, **k: True    # backup success/failure is its own concern
    ds._close_live_db_connection = lambda f: None          # no live GUI-thread connection here
    ds._checkpoint_live_db = lambda f: None
    return ds, local, cloud


def _scenario(rng):
    """Randomize one (local, cloud) content+mtime+corruption combination."""
    local_exists = rng.random() < 0.85       # local usually exists
    same_content = rng.random() < 0.2        # occasionally identical -> should always no-op
    corrupt_cloud = rng.random() < 0.15
    return local_exists, same_content, corrupt_cloud


def _apply_scenario(local, cloud, rng, local_exists, same_content, corrupt_cloud):
    local_mtime = None
    if local_exists:
        _make_db(local, rng.randint(0, 1_000_000_000))
        local_mtime = BASE_EPOCH + rng.uniform(-1e6, 1e6)
        os.utime(local, (local_mtime, local_mtime))
        local_mtime = os.path.getmtime(local)

    if same_content and local_exists and not corrupt_cloud:
        shutil.copy2(local, cloud)
    else:
        _make_db(cloud, rng.randint(0, 1_000_000_000), corrupt=corrupt_cloud)

    cloud_mtime = (local_mtime if local_mtime is not None else BASE_EPOCH) + _mtime_delta(rng)
    os.utime(cloud, (cloud_mtime, cloud_mtime))
    cloud_mtime = os.path.getmtime(cloud)
    return local_mtime, cloud_mtime


def _check_pull_invariant(local, cloud, local_exists, corrupt_cloud,
                           content_before, local_mtime, cloud_mtime):
    content_after = local.read_bytes() if local.exists() else None
    cloud_bytes = cloud.read_bytes()

    # The integrity check (SAFETY 1) applies unconditionally, regardless of
    # whether local exists yet -- a corrupt cloud copy must never seed a fresh
    # local file either, so this is checked before the missing-local case.
    if corrupt_cloud:
        assert content_after == content_before, (
            "a corrupt cloud db must NEVER be imported, regardless of mtime "
            "or whether local exists yet"
        )
        return
    if not local_exists:
        assert content_after == cloud_bytes, "missing local should always be created from a valid cloud copy"
        return
    if content_before == cloud_bytes:
        assert content_after == content_before, "identical content must be a no-op"
        return
    if cloud_mtime < local_mtime:
        assert content_after == content_before, (
            f"cloud is strictly OLDER (cloud={cloud_mtime} < local={local_mtime}) "
            f"but local content changed — an older copy clobbered a newer one"
        )
        return
    # cloud_mtime >= local_mtime (newer-or-tied), content differs, cloud is valid
    assert content_after == cloud_bytes, (
        f"cloud is newer-or-tied (cloud={cloud_mtime} >= local={local_mtime}) and differs, "
        f"but wasn't adopted — a legitimate update was silently dropped"
    )


def _check_push_invariant(local, cloud, same_content, content_before,
                           local_mtime, cloud_mtime):
    content_after = cloud.read_bytes()
    local_bytes = local.read_bytes()

    if same_content:
        assert content_after == content_before, "identical content must be a no-op"
        return
    if local_mtime < cloud_mtime:
        assert content_after == content_before, (
            f"local is strictly OLDER (local={local_mtime} < cloud={cloud_mtime}) "
            f"but cloud content changed — an older copy clobbered a newer one"
        )
        return
    # local_mtime >= cloud_mtime (newer-or-tied) and content differs
    assert content_after == local_bytes, (
        f"local is newer-or-tied (local={local_mtime} >= cloud={cloud_mtime}) and differs, "
        f"but wasn't pushed — a legitimate update was silently dropped"
    )


def one(seed, verbose=False):
    rng = random.Random(seed)
    tmp = Path(tempfile.mkdtemp(prefix="ankimon_syncfuzz_"))
    try:
        # --- pull direction: read_configs (cloud -> local) ---
        ds, local, cloud = _new_sync(tmp, rng)
        local_exists, same_content, corrupt_cloud = _scenario(rng)
        local_mtime, cloud_mtime = _apply_scenario(
            local, cloud, rng, local_exists, same_content, corrupt_cloud
        )
        if verbose:
            print(f"seed {seed} [read_configs]\n"
                  f"  local_exists={local_exists} same_content={same_content} "
                  f"corrupt_cloud={corrupt_cloud}\n"
                  f"  local_mtime={local_mtime} cloud_mtime={cloud_mtime}")
        content_before = local.read_bytes() if local.exists() else None
        ds.read_configs(media_sync_status=False)
        _check_pull_invariant(local, cloud, local_exists, corrupt_cloud,
                               content_before, local_mtime, cloud_mtime)

        # --- push direction: save_configs (local -> cloud) ---
        ds2, local2, cloud2 = _new_sync(tmp, rng)
        same_content2 = rng.random() < 0.2
        _make_db(local2, rng.randint(0, 1_000_000_000))
        local_mtime2 = BASE_EPOCH + rng.uniform(-1e6, 1e6)
        os.utime(local2, (local_mtime2, local_mtime2))
        local_mtime2 = os.path.getmtime(local2)
        if same_content2:
            shutil.copy2(local2, cloud2)
        else:
            _make_db(cloud2, rng.randint(0, 1_000_000_000))
        cloud_mtime2 = local_mtime2 + _mtime_delta(rng)
        os.utime(cloud2, (cloud_mtime2, cloud_mtime2))
        cloud_mtime2 = os.path.getmtime(cloud2)
        if verbose:
            print(f"seed {seed} [save_configs]\n"
                  f"  same_content={same_content2}\n"
                  f"  local_mtime={local_mtime2} cloud_mtime={cloud_mtime2}")
        content_before2 = cloud2.read_bytes()
        ds2.save_configs()
        _check_push_invariant(local2, cloud2, same_content2, content_before2,
                               local_mtime2, cloud_mtime2)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run(n=300, master=20260712):
    failures = []
    for i in range(n):
        seed = master * 100000 + i
        try:
            one(seed)
        except Exception as e:
            failures.append((seed, type(e).__name__, str(e)[:200]))
        if (i + 1) % 100 == 0:
            print(f"  {i + 1}/{n} run, {len(failures)} failures so far")

    print(f"\nsync_fuzz: {n} iterations, {len(failures)} failed")
    distinct = {}
    for seed, kind, msg in failures:
        distinct.setdefault((kind, msg.split(":")[0][:70]), (seed, kind, msg))
    if distinct:
        print(f"distinct failures ({len(distinct)}) — each reproducible:")
        for (_k, _m), (seed, kind, msg) in list(distinct.items())[:25]:
            print(f"  [seed {seed}] {kind}: {msg}")
        print("\nreproduce any with:  python3 harness/scenarios/sync_fuzz.py --seed <seed>")
    else:
        print("sync_fuzz: CLEAN — no invariant violations across content/mtime/existence/corruption combos.")
    return len(failures)


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--seed" in args:
        s = int(args[args.index("--seed") + 1])
        one(s, verbose=True)
        print(f"seed {s}: completed with no detected problem")
    else:
        n = int(args[0]) if args and args[0].isdigit() else 300
        sys.exit(1 if run(n) else 0)
