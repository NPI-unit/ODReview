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

# ─── 4. Build a Desktop .app bundle (proper Dock icon + name) ──────────────
echo "[4/4] Building ErysReview.app on your Desktop ..."
chmod +x "$(pwd)/run.command"
ROOT="$(pwd)"
APP="$HOME/Desktop/ErysReview.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# Launcher: run THIS repo's venv python (it has the macOS-26-safe Tk — never
# the system python). Wrapped in a bundle so the Dock shows our icon + name
# instead of the generic "Python" rocket. $ROOT is baked in at install time.
cat > "$APP/Contents/MacOS/ErysReview" <<LAUNCHER
#!/bin/bash
cd "$ROOT" || exit 1
exec "$ROOT/.venv/bin/python" -m erysreview
LAUNCHER
chmod +x "$APP/Contents/MacOS/ErysReview"

# Dock icon (optional — the bundle still launches without it).
if [ -f "$ROOT/assets/icon.icns" ]; then
    cp "$ROOT/assets/icon.icns" "$APP/Contents/Resources/icon.icns"
fi

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>               <string>ErysReview</string>
    <key>CFBundleDisplayName</key>        <string>ErysReview</string>
    <key>CFBundleExecutable</key>         <string>ErysReview</string>
    <key>CFBundleIdentifier</key>         <string>de.erys.review</string>
    <key>CFBundleIconFile</key>           <string>icon</string>
    <key>CFBundlePackageType</key>        <string>APPL</string>
    <key>CFBundleShortVersionString</key> <string>1.0</string>
    <key>NSHighResolutionCapable</key>    <true/>
</dict>
</plist>
PLIST

# Nudge LaunchServices so the new icon/name register immediately.
touch "$APP" 2>/dev/null || true

echo ""
echo "============================================"
echo " Done. ErysReview is installed.   (Tk $TKVER)"
echo "============================================"
echo ""
echo "Start the app by double-clicking ErysReview on your Desktop"
echo "(or run.command inside this folder)."
echo ""
read -r -p "Press Enter to close ..."
