#!/usr/bin/env bash
# harness/setup_webengine.sh — fetch QtWebEngine's native deps, sudo-free.
#
# QtWebEngine (the reviewer HUD + the Pokedex/help windows) needs a pile of
# Chromium native libs. On a box that has them (x86 CI), nothing to do. On a
# minimal/aarch64 box (e.g. this Pi) they're missing, so — exactly like
# setup_tier2.sh does for the base Qt libs — this downloads the .debs WITHOUT
# sudo and extracts them locally; you reach them via LD_LIBRARY_PATH.
#
#   bash harness/setup_webengine.sh
#   source .tier2/env.sh
#   export LD_LIBRARY_PATH="$PWD/.tier2/we-libs/extract/usr/lib/aarch64-linux-gnu:$LD_LIBRARY_PATH"
#   export QTWEBENGINE_DISABLE_SANDBOX=1
#   export QTWEBENGINE_CHROMIUM_FLAGS="--no-sandbox --disable-gpu --disable-dev-shm-usage --in-process-gpu --single-process"
#   python3 harness/scenarios/hud_render.py 150
#
# (Headless Chromium needs --no-sandbox + software GL; the flags above are the set
# that gets it rendering offscreen on this Pi.)
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WE="$ROOT/.tier2/we-libs"
mkdir -p "$WE/debs" "$WE/extract"
cd "$WE/debs" || exit 1

# libs reported missing by `ldd libQt6WebEngineCore.so` (+ their transitive deps).
PKGS="libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libxtst6 liblcms2-2 \
      libopus0 libsnappy1v5 libwebp7 libwebpdemux2 libwebpmux3 libxcb-dri3-0 \
      libxkbfile1 libasound2t64 libminizip1t64 libxrender1 libsharpyuv0"

for p in $PKGS; do
  apt-get download "$p" 2>/dev/null || echo "warn: could not fetch $p (name may differ on your release)"
done

n=0
for d in *.deb; do
  [ -f "$d" ] && dpkg-deb -x "$d" "$WE/extract" && n=$((n + 1))
done
echo "extracted $n debs -> $WE/extract"
echo "libs at: $WE/extract/usr/lib/aarch64-linux-gnu"
echo "Re-run ldd on libQt6WebEngineCore.so to check for any further 'not found' deps."
