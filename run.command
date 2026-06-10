#!/bin/bash
# ErysReview — macOS launcher.
# Double-click to start the app.
#
# Uses the local .venv (Homebrew-Python, Tk 9.0.3). This avoids the Tk 8.6.13
# segfault that conda's Tk triggers on macOS 26 (Tahoe) when opening modal
# dialogs (e.g. the ⚙ "counted images" picker).
cd "$(dirname "$0")" || exit 1

# Preferred: the local venv with a macOS-26-compatible Tk.
if [ -x "./.venv/bin/python" ]; then
    exec ./.venv/bin/python -u -m erysreview
fi

# Fallback: (re)create the venv from Homebrew Python if it's missing.
HBPY="/opt/homebrew/bin/python3.12"
if [ -x "$HBPY" ]; then
    echo "[i] .venv fehlt — wird neu erstellt ..."
    "$HBPY" -m venv .venv \
      && ./.venv/bin/python -m pip install --upgrade pip \
      && ./.venv/bin/pip install -r requirements.txt \
      && exec ./.venv/bin/python -u -m erysreview
fi

echo "[!] Kein .venv und kein Homebrew-Python (/opt/homebrew/bin/python3.12) gefunden."
echo "    Bitte einmal:  /opt/homebrew/bin/python3.12 -m venv .venv"
echo "                   ./.venv/bin/pip install -r requirements.txt"
read -r -p "Enter zum Schließen ..."
exit 1
