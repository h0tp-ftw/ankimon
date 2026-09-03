#!/usr/bin/env bash
# harness/setup_webengine.sh — fetch extra QtWebEngine native deps, sudo-free.
#
# setup_tier2.sh creates the base Qt venv. This script adds the separate
# PyQt6-WebEngine wheel plus Chromium's native system libraries. Common x86 CI
# images already have most native libs, while minimal/aarch64 machines may not;
# missing .debs are downloaded WITHOUT sudo and extracted for LD_LIBRARY_PATH.
#
#   bash harness/setup_webengine.sh
#   source .tier2/env.sh
#   export LD_LIBRARY_PATH="$PWD/.tier2/we-libs/extract/usr/lib/$(uname -m)-linux-gnu:$LD_LIBRARY_PATH"
#   export QTWEBENGINE_DISABLE_SANDBOX=1
#   export QTWEBENGINE_CHROMIUM_FLAGS="--no-sandbox --disable-gpu --disable-dev-shm-usage --in-process-gpu --single-process"
#   python3 -m harness.checks.probe_real_webengine
#   python3 harness/scenarios/hud_render.py 150
#
# (Headless Chromium needs --no-sandbox + software GL; the flags above are the set
# that gets it rendering offscreen on this Pi.)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/.tier2/venv"
WE="$ROOT/.tier2/we-libs"
ARCH_DIR="$WE/extract/usr/lib/$(uname -m)-linux-gnu"

if [ ! -x "$VENV/bin/python" ]; then
  echo "Tier-2 venv missing. Run: bash harness/setup_tier2.sh" >&2
  exit 1
fi

"$VENV/bin/pip" install --quiet --upgrade -r "$ROOT/harness/requirements-webengine.txt"
mkdir -p "$WE/debs" "$WE/extract"
cd "$WE/debs"

# libs reported missing by `ldd libQt6WebEngineCore.so` (+ their transitive deps).
PKGS="libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libxtst6 liblcms2-2 \
      libopus0 libsnappy1v5 libwebp7 libwebpdemux2 libwebpmux3 libxcb-dri3-0 \
      libxkbfile1 libasound2t64 libminizip1t64 libxrender1 libsharpyuv0 \
      libnspr4 libnss3"

for p in $PKGS; do
  apt-get download "$p" 2>/dev/null || echo "warn: could not fetch $p (name may differ on your release)"
done

n=0
for d in *.deb; do
  [ -f "$d" ] && dpkg-deb -x "$d" "$WE/extract" && n=$((n + 1))
done
echo "extracted $n debs -> $WE/extract"
echo "libs at: $ARCH_DIR"
echo "Re-run ldd on libQt6WebEngineCore.so to check for any further 'not found' deps."
