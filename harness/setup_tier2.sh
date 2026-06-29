#!/usr/bin/env bash
# setup_tier2.sh — create the sudo-free Tier-2 environment for the Ankimon harness.
#
# Tier 2 runs the REAL add-on under real PyQt6 (offscreen). This needs PyQt6 +
# the native Qt libraries. We get both WITHOUT touching the system:
#   * a venv with pip bootstrapped via get-pip.py (no python3-venv/apt needed)
#   * PyQt6/requests/markdown installed into that venv
#   * the native Qt .debs DOWNLOADED (not installed) and extracted into a local
#     dir, reached via LD_LIBRARY_PATH
#
# Everything lands under <repo>/.tier2 and is removed with `rm -rf .tier2`.
# Re-run any time; it's idempotent-ish (skips the venv if present).
#
# Usage:
#   bash harness/setup_tier2.sh
#   source .tier2/env.sh
#   python -m harness.checks.probe_real_boot
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
T2="$REPO/.tier2"
VENV="$T2/venv"
QTLIBS="$T2/qtlibs"
DEBS="$T2/debs"
ARCH_DIR="$QTLIBS/usr/lib/$(uname -m)-linux-gnu"

mkdir -p "$T2" "$DEBS" "$QTLIBS"

echo "==> [1/4] venv + pip (no sudo)"
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv --without-pip "$VENV"
fi
if ! "$VENV/bin/python" -m pip --version >/dev/null 2>&1; then
  python3 -c "import urllib.request; urllib.request.urlretrieve('https://bootstrap.pypa.io/get-pip.py','$T2/get-pip.py')"
  "$VENV/bin/python" "$T2/get-pip.py"
fi

echo "==> [2/4] PyQt6 + requests + markdown into the venv"
"$VENV/bin/pip" install --quiet --upgrade PyQt6 requests markdown

echo "==> [3/4] download + extract native Qt libs locally (no install)"
# Core EGL/GL/font/dbus/glib stack the offscreen Qt platform needs. Downloaded
# per-package (a bad version in a batch aborts the whole batch).
PKGS="libegl1 libgl1 libglx0 libopengl0 libglvnd0 libglx-mesa0 libgles2 \
      libglapi-mesa libgbm1 libdrm2 libxkbcommon0 libfontconfig1 \
      libglib2.0-0t64 libharfbuzz0b libgraphite2-3 libpng16-16t64 \
      libfreetype6 libbrotli1 libpcre2-8-0"
( cd "$DEBS"
  for p in $PKGS; do
    apt-get download "$p" 2>/dev/null && echo "    got $p" || echo "    skip $p (already present / unavailable)"
  done
  for d in *.deb; do [ -e "$d" ] && dpkg-deb -x "$d" "$QTLIBS"; done
)

echo "==> [4/4] write env file"
cat > "$T2/env.sh" <<EOF
# Source this before running Tier-2 harness commands.
export LD_LIBRARY_PATH="$ARCH_DIR\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}"
export QT_QPA_PLATFORM=offscreen
export PATH="$VENV/bin:\$PATH"
# Quiet a harmless fontconfig warning under offscreen Qt.
export FONTCONFIG_PATH="\${FONTCONFIG_PATH:-/etc/fonts}"
EOF

echo
echo "Tier-2 env ready at $T2"
echo "Next:"
echo "  source .tier2/env.sh"
echo "  python -m harness.checks.probe_real_boot"
echo "  python -m harness.checks.probe_real_play"
echo
echo "Optional — real Pokemon sprites (pixel-accurate, ~600MB, sudo-free):"
echo "  python3 harness/fetch_sprites.py"
