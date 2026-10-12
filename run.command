#!/bin/bash
# NKReview — macOS launcher. Double-click to start the app (Qt).
#
# Uses the local .venv next to this file. If it is missing, run
# install.command once (it creates the venv and installs everything).
cd "$(dirname "$0")" || exit 1

if [ -x "./.venv/bin/python" ]; then
    exec ./.venv/bin/python -u -m nkreview
fi

echo "[!] Die App ist noch nicht installiert (.venv fehlt)."
echo "    Bitte einmal install.command doppelklicken."
read -r -p "Enter zum Schließen ..."
exit 1
