#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────
#  ErysReview — macOS installer  (venv / Homebrew-Python)
#
#  Double-click this file from inside the ErysReview folder.
#
#  Uses a local .venv built from Homebrew's Python 3.12, which ships a
#  macOS-26-compatible Tk (9.0.3). This avoids the Tk 8.6.13 segfault that
#  conda's Tk triggers on macOS 26 (Tahoe) when opening modal dialogs
#  (e.g. the ⚙ "counted images" picker).
# ─────────────────────────────────────────────────────────────────────────
set -u
cd "$(dirname "$0")" || exit 1

echo ""
echo "============================================"
echo " ErysReview installer (macOS)"
echo "============================================"
echo ""

# ─── 1. Locate a suitable Homebrew Python 3.12 ─────────────────────────────
PY=""
for p in \
    "/opt/homebrew/bin/python3.12" \
    "/usr/local/bin/python3.12" \
    "$(command -v python3.12 2>/dev/null)"
do
    if [ -n "$p" ] && [ -x "$p" ]; then PY="$p"; break; fi
done

if [ -z "$PY" ]; then
    echo "[!] Homebrew Python 3.12 was not found."
    echo ""
    echo "Install Homebrew first (https://brew.sh), then:"
    echo "    brew install python@3.12 python-tk@3.12"
    echo ""
    echo "Re-run install.command afterwards."
    echo ""
    read -r -p "Press Enter to close ..."
    exit 1
fi
echo "[1/4] Found Python: $PY"

# ─── 2. Verify this Python has a macOS-26-safe Tk ──────────────────────────
TKVER="$("$PY" -c 'import tkinter; r=tkinter.Tk(); print(r.tk.call("info","patchlevel")); r.destroy()' 2>/dev/null)"
if [ -z "$TKVER" ]; then
    echo "[!] This Python has no working Tkinter. Install it with:"
    echo "    brew install python-tk@3.12"
    echo ""
    read -r -p "Press Enter to close ..."
    exit 1
fi
echo "      Tk version: $TKVER"
if [ "$TKVER" = "8.6.13" ]; then
    echo ""
    echo "[!] WARNING: Tk 8.6.13 crashes on macOS 26 (Tahoe) in modal dialogs."
    echo "    Update Homebrew's Tk, then re-run this installer:"
    echo "        brew update && brew upgrade python-tk@3.12 tcl-tk"
    echo ""
    read -r -p "Continue anyway? [y/N] " ans
    case "$ans" in y|Y) ;; *) exit 1 ;; esac
fi

# ─── 3. Create the venv + install dependencies ─────────────────────────────
echo "[2/4] Creating local .venv ..."
if [ ! -x "./.venv/bin/python" ]; then
    "$PY" -m venv .venv || {
        echo "[!] venv creation failed."
        read -r -p "Press Enter to close ..."
        exit 1
    }
else
    echo "      .venv already exists, reusing."
fi

echo "[3/4] Installing dependencies from requirements.txt ..."
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.txt || {
    echo "[!] pip install failed. Check your internet connection."
    read -r -p "Press Enter to close ..."
    exit 1
}

# ─── 4. Make launcher executable + copy shortcut to Desktop ────────────────
echo "[4/4] Setting up Desktop launcher ..."
chmod +x "$(pwd)/run.command"

DESKTOP_SHORTCUT="$HOME/Desktop/ErysReview.command"
cp "$(pwd)/run.command" "$DESKTOP_SHORTCUT"
chmod +x "$DESKTOP_SHORTCUT"
# Embed the absolute path to the repo so the desktop copy knows where to cd to.
ROOT="$(pwd)"
"$PY" - <<PY
from pathlib import Path
p = Path("$DESKTOP_SHORTCUT")
text = p.read_text()
text = text.replace('cd "\$(dirname "\$0")" || exit 1', 'cd "$ROOT" || exit 1')
p.write_text(text)
PY

echo ""
echo "============================================"
echo " Done. ErysReview is installed.   (Tk $TKVER)"
echo "============================================"
echo ""
echo "You now have two ways to start the app:"
echo "  1. Double-click \"ErysReview\" on your Desktop"
echo "  2. Double-click run.command inside this folder"
echo ""
read -r -p "Press Enter to close ..."
