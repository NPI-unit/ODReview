#!/bin/bash
# ErysReview — macOS launcher.
# Double-click to start the app. Run install.command first to set things up.
cd "$(dirname "$0")"

# Find conda
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
        if [ -x "$p" ]; then CONDA="$p"; break; fi
    done
fi

if [ -z "$CONDA" ]; then
    echo "[!] conda not found. Run install.command first."
    read -p "Press Enter to close ..."
    exit 1
fi

CONDA_BASE="$($CONDA info --base)"
# shellcheck disable=SC1091
source "$CONDA_BASE/etc/profile.d/conda.sh"
conda activate erys || {
    echo "[!] Could not activate the erys env. Run install.command first."
    read -p "Press Enter to close ..."
    exit 1
}

python -u -m erysreview
