#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────
#  ErysReview — macOS installer
#
#  Double-click this file from inside the ErysReview folder.
#  It checks for Miniforge / conda, creates an "erys" env, installs deps,
#  makes the launcher executable, and copies a Desktop shortcut.
# ─────────────────────────────────────────────────────────────────────────
set -u
cd "$(dirname "$0")"

echo ""
echo "============================================"
echo " ErysReview installer (macOS)"
echo "============================================"
echo ""

# ─── 1. Locate conda ───────────────────────────────────────────────────
CONDA=""
if command -v conda >/dev/null 2>&1; then
    CONDA="$(command -v conda)"
else
    for p in \
        "$HOME/miniforge3/bin/conda" \
        "$HOME/miniconda3/bin/conda" \
        "$HOME/anaconda3/bin/conda" \
        "/opt/homebrew/Caskroom/miniforge/base/bin/conda" \
        "/opt/miniforge3/bin/conda"
    do
        if [ -x "$p" ]; then
            CONDA="$p"
            break
        fi
    done
fi

if [ -z "$CONDA" ]; then
    echo "[!] Miniforge / conda was not found."
    echo ""
    echo "Install Miniforge first:"
    echo "  https://github.com/conda-forge/miniforge#download"
    echo ""
    echo "On Apple Silicon (M1/M2/M3): pick Miniforge3-MacOSX-arm64.sh"
    echo "On Intel Macs:               pick Miniforge3-MacOSX-x86_64.sh"
    echo "Then run:"
    echo "  bash Miniforge3-MacOSX-arm64.sh"
    echo ""
    echo "Re-run install.command afterwards."
    echo ""
    read -p "Press Enter to close ..."
    exit 1
fi
echo "[1/4] Found conda: $CONDA"

# Conda env activation requires sourcing — use the conda hook
CONDA_BASE="$($CONDA info --base)"
# shellcheck disable=SC1091
source "$CONDA_BASE/etc/profile.d/conda.sh"

# ─── 2. Create or update the env ───────────────────────────────────────
echo "[2/4] Creating / updating conda env \"erys\" (Python 3.12) ..."
if conda env list | awk '{print $1}' | grep -qx erys; then
    echo "    Env \"erys\" already exists, reusing."
else
    "$CONDA" create -n erys python=3.12 -y || {
        echo "[!] conda create failed."
        read -p "Press Enter to close ..."
        exit 1
    }
fi

conda activate erys || {
    echo "[!] Failed to activate erys env."
    read -p "Press Enter to close ..."
    exit 1
}

# ─── 3. Install / upgrade dependencies ─────────────────────────────────
echo "[3/4] Installing dependencies from requirements.txt ..."
python -m pip install --upgrade pip
python -m pip install -r requirements.txt || {
    echo "[!] pip install failed. Check your internet."
    read -p "Press Enter to close ..."
    exit 1
}

# ─── 4. Make launcher executable + copy shortcut to Desktop ────────────
echo "[4/4] Setting up Desktop launcher ..."
chmod +x "$(pwd)/run.command"

DESKTOP_SHORTCUT="$HOME/Desktop/ErysReview.command"
cp "$(pwd)/run.command" "$DESKTOP_SHORTCUT"
chmod +x "$DESKTOP_SHORTCUT"
# Embed the absolute path to the repo so the desktop copy knows where to cd to
ROOT="$(pwd)"
python3 - <<PY
import re
from pathlib import Path
p = Path("$DESKTOP_SHORTCUT")
text = p.read_text()
text = text.replace('cd "\$(dirname "\$0")"', 'cd "$ROOT"')
p.write_text(text)
PY

echo ""
echo "============================================"
echo " Done. ErysReview is installed."
echo "============================================"
echo ""
echo "You now have two ways to start the app:"
echo "  1. Double-click \"ErysReview\" on your Desktop"
echo "  2. Double-click run.command inside this folder"
echo ""
read -p "Press Enter to close ..."
