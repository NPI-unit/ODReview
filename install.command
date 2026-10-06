#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────
#  ErysReview — macOS installer
#
#  Double-click this file inside the ErysReview folder. It
#    1. finds Python 3.12,
#    2. creates the local .venv,
#    3. keeps every package in vendor/wheels/ (downloads what is missing),
#    4. installs the exact tested versions (requirements-lock-macos.txt),
#    5. keeps the Cellpose model in vendor/models/ and installs it,
#    6. runs the self-test and puts ErysReview.app on the Desktop.
#
#  Offline: copy the whole folder including vendor/ (e.g. on a stick) to the
#  next Mac — it then installs without internet (Python 3.12 must be there).
#
#  Output: "[n/6] …" per step, "##DONE" or "##FAIL <reason>" at the end
#  (read by the installer window). --yes: no questions, no "press Enter".
#  ERYS_INSTALL_NO_APP=1 skips the Desktop app (tests, installer window option).
#  ERYS_INSTALL_DROP_VENDOR=1 removes vendor/ again after a good install.
#  Everything is also written to install.log.
# ─────────────────────────────────────────────────────────────────────────
set -u
cd "$(dirname "$0")" || exit 1
ROOT="$(pwd)"
LOCK="$ROOT/requirements-lock-macos.txt"
VENDOR="$ROOT/vendor"
WHEELS="$VENDOR/wheels"
MODELS="$VENDOR/models"
LOG="$ROOT/install.log"
ASK=1
[ "${1:-}" = "--yes" ] && ASK=0
N=6
export PIP_DISABLE_PIP_VERSION_CHECK=1     # no "new pip available" noise

exec > >(tee -a "$LOG") 2>&1
echo ""
echo "============================================"
echo " ErysReview installer (macOS) — $(date '+%Y-%m-%d %H:%M')"
echo "============================================"

pause() { if [ "$ASK" = 1 ]; then read -r -p "Enter zum Schließen ..."; fi; }
step()  { echo ""; echo "[$1/$N] $2"; }
fail()  { echo ""; echo "[!] $1"; echo "##FAIL $1"; pause; exit 1; }
online() { curl -s -m 6 -o /dev/null -I https://pypi.org/simple/ ; }

# A folder downloaded from the internet carries macOS' quarantine flag — every
# script and app in it would warn again. Running this installer is consent.
xattr -dr com.apple.quarantine "$ROOT" 2>/dev/null || true

# ─── 1. Python 3.12 ──────────────────────────────────────────────────────
step 1 "Python 3.12 suchen"
PY=""
for p in \
    "$VENDOR/python/bin/python3.12" \
    "/opt/homebrew/bin/python3.12" \
    "/usr/local/bin/python3.12" \
    "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12" \
    "$(command -v python3.12 2>/dev/null)"
do
    if [ -n "$p" ] && [ -x "$p" ] && "$p" -c 'import sys; sys.exit(sys.version_info[:2] != (3, 12))' 2>/dev/null; then
        PY="$p"; break
    fi
done
[ -n "$PY" ] || fail "Python 3.12 nicht gefunden. Bitte installieren: python.org (macOS-Installer 3.12) oder 'brew install python@3.12' — dann erneut starten."
echo "      $PY ($("$PY" --version 2>&1))"

# ─── 2. .venv ────────────────────────────────────────────────────────────
step 2 "Eigene Python-Umgebung (.venv)"
if [ -x "./.venv/bin/python" ] && ./.venv/bin/python -c 'import sys; sys.exit(sys.version_info[:2] != (3, 12))' 2>/dev/null; then
    echo "      vorhanden, wird weiterverwendet."
else
    rm -rf ./.venv
    "$PY" -m venv .venv || fail ".venv konnte nicht angelegt werden."
    echo "      angelegt."
fi
VPY="$ROOT/.venv/bin/python"

# ─── 3. Packages kept in vendor/wheels ───────────────────────────────────
step 3 "Pakete im Ordner bereitstellen (vendor/wheels)"
mkdir -p "$WHEELS"
complete() { "$VPY" -m pip install --dry-run --quiet --no-index --find-links "$WHEELS" -r "$LOCK" >/dev/null 2>&1; }
if complete; then
    echo "      alle $(grep -c '==' "$LOCK") Pakete liegen schon im Ordner — keine Downloads nötig."
elif online; then
    echo "      lade fehlende Pakete (einmalig, ~0,6 GB) ..."
    "$VPY" -m pip download --quiet --only-binary=:all: -d "$WHEELS" -r "$LOCK" \
        || fail "Download der Pakete fehlgeschlagen (Internet?)."
    complete || fail "Pakete unvollständig — bitte erneut starten."
    echo "      gespeichert in vendor/wheels ($(du -sh "$WHEELS" | cut -f1))."
else
    fail "Kein Internet und vendor/wheels unvollständig. Den Ordner von einem Mac kopieren, auf dem install.command schon lief."
fi

# ─── 4. Install the exact tested versions ────────────────────────────────
step 4 "Pakete installieren (getestete Versionen)"
"$VPY" -m pip install --quiet --no-index --find-links "$WHEELS" -r "$LOCK" \
    || fail "Installation der Pakete fehlgeschlagen — Details in install.log."
# The full PySide6 package (≈ 900 MB of Qt add-ons) is not needed any more.
for extra in PySide6 PySide6-Addons; do
    if "$VPY" -m pip show "$extra" >/dev/null 2>&1; then
        "$VPY" -m pip uninstall --quiet -y "$extra" && echo "      $extra entfernt (nicht mehr nötig)."
    fi
done
echo "      fertig."

# ─── 5. Cellpose model (cpsam, ~1.2 GB) ──────────────────────────────────
step 5 "Cellpose-Modell (cpsam)"
HOME_MODEL="$HOME/.cellpose/models/cpsam"
mkdir -p "$MODELS" "$HOME/.cellpose/models"
big() { [ -f "$1" ] && [ "$(stat -f %z "$1")" -gt 100000000 ]; }
if ! big "$HOME_MODEL"; then
    if big "$MODELS/cpsam"; then
        cp -c "$MODELS/cpsam" "$HOME_MODEL" 2>/dev/null || cp "$MODELS/cpsam" "$HOME_MODEL"
        echo "      aus vendor/models installiert."
    elif online; then
        echo "      lade Modell (einmalig, ~1,2 GB) ..."
        "$VPY" -c "from cellpose import models; models.CellposeModel(gpu=False, pretrained_model='cpsam')" >/dev/null 2>&1 \
            || echo "      [!] Download fehlgeschlagen — wird beim ersten Rechnen erneut versucht."
    else
        echo "      [!] Kein Modell und kein Internet — es wird beim ersten Rechnen geladen."
    fi
else
    echo "      vorhanden."
fi
# keep a copy in the folder for the next Mac (APFS clone: no extra space)
if big "$HOME_MODEL" && ! big "$MODELS/cpsam"; then
    cp -c "$HOME_MODEL" "$MODELS/cpsam" 2>/dev/null || cp "$HOME_MODEL" "$MODELS/cpsam"
    echo "      Kopie in vendor/models gespeichert."
fi

# ─── 6. Self-test + Desktop app ──────────────────────────────────────────
step 6 "Selbsttest und ErysReview.app"
"$VPY" tools/check_install.py || fail "Selbsttest fehlgeschlagen — siehe die Zeilen 'CHECK fail' oben."

VERSION="$("$VPY" -c 'import erysreview; print(erysreview.__version__)' 2>/dev/null || echo 1.0)"
if [ "${ERYS_INSTALL_NO_APP:-0}" != "1" ]; then
    chmod +x "$ROOT/run.command" 2>/dev/null
    APP="$HOME/Desktop/ErysReview.app"
    rm -rf "$APP"
    mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
    # The bundle runs THIS folder's .venv — the Dock shows our icon and name
    # instead of Python's. $ROOT is fixed at install time.
    cat > "$APP/Contents/MacOS/ErysReview" <<LAUNCHER
#!/bin/bash
cd "$ROOT" || exit 1
exec "$ROOT/.venv/bin/python" -m erysreview
LAUNCHER
    chmod +x "$APP/Contents/MacOS/ErysReview"
    if [ -f "$ROOT/assets/icon.icns" ]; then cp "$ROOT/assets/icon.icns" "$APP/Contents/Resources/icon.icns"; fi
    cat > "$APP/Contents/Info.plist" <<PLIST
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
    <key>CFBundleShortVersionString</key> <string>$VERSION</string>
    <key>NSHighResolutionCapable</key>    <true/>
</dict>
</plist>
PLIST
    touch "$APP" 2>/dev/null || true
    echo "      ErysReview.app auf dem Schreibtisch angelegt."
fi

if [ "${ERYS_INSTALL_DROP_VENDOR:-0}" = "1" ]; then
    rm -rf "$WHEELS" "$MODELS"
    echo "      Pakete im Ordner entfernt (wie gewählt)."
fi

echo ""
echo "============================================"
echo " Fertig. ErysReview $VERSION ist installiert."
echo "============================================"
echo "Start: ErysReview auf dem Schreibtisch (oder run.command in diesem Ordner)."
echo "##DONE"
pause
exit 0
