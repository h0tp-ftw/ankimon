"""
harness/fetch_sprites.py — download the REAL Ankimon sprite set (sudo-free).

Mirrors the add-on's own downloader (src/Ankimon/pyobj/download_sprites.py): pulls
``sprites.zip`` (~600 MB: all gens, shiny, animated GIFs) from the same mirrors
and extracts it into a cache dir. Tier-2 sessions then symlink their profile's
sprite dir to this cache, so the real windows render the real Pokemon art instead
of the placeholder.

Uses only stdlib (urllib + zipfile), so it runs under plain python3 — no venv,
no requests. Idempotent: skips if the cache's completion flag exists.

    python3 harness/fetch_sprites.py [dest_dir]    # default: .tier2/sprites-cache
"""

from __future__ import annotations

import sys
import pathlib
import zipfile
import urllib.request

# Same mirrors the add-on uses (HuggingFace first, GitHub release as fallback).
MIRRORS = [
    "https://huggingface.co/datasets/h0tp/ankimon-sprites/resolve/main/sprites.zip",
    "https://github.com/h0tp-ftw/ankimon-sprites/releases/download/latest/sprites.zip",
]

REPO = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_CACHE = REPO / ".tier2" / "sprites-cache"
FLAG_NAME = "download_complete.flag"


def _download(url: str, out: pathlib.Path, chunk: int = 1 << 20) -> None:
    with urllib.request.urlopen(url, timeout=60) as r:
        total = int(r.headers.get("content-length", 0))
        got = 0
        next_pct = 0
        with open(out, "wb") as f:
            while True:
                block = r.read(chunk)
                if not block:
                    break
                f.write(block)
                got += len(block)
                if total:
                    pct = int(got / total * 100)
                    if pct >= next_pct:
                        print(f"  {pct:3d}%  ({got >> 20} / {total >> 20} MB)", flush=True)
                        next_pct = pct + 10


def fetch(dest=None) -> pathlib.Path:
    dest = pathlib.Path(dest) if dest else DEFAULT_CACHE
    dest.mkdir(parents=True, exist_ok=True)
    flag = dest / FLAG_NAME
    if flag.exists():
        print(f"sprites already present at {dest}")
        return dest

    zip_path = dest / "sprites.zip"
    last_err = None
    for url in MIRRORS:
        try:
            print(f"downloading {url}")
            _download(url, zip_path)
            last_err = None
            break
        except Exception as e:  # try the next mirror
            last_err = e
            print(f"  mirror failed: {type(e).__name__}: {e}")
    if last_err is not None:
        raise RuntimeError(f"all sprite mirrors failed: {last_err}")

    print("extracting (this takes a moment — thousands of files)...")
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(dest)
    zip_path.unlink(missing_ok=True)
    flag.write_text("ok")
    print(f"sprites ready at {dest}")
    return dest


if __name__ == "__main__":
    fetch(sys.argv[1] if len(sys.argv) > 1 else None)
