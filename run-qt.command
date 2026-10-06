#!/bin/bash
# ErysReview — Qt-Version (im Aufbau, siehe docs/qt-umbauplan.md).
# Doppelklick startet sie; die Tk-Version bleibt run.command.
cd "$(dirname "$0")" || exit 1
exec ./.venv/bin/python -u -m erysreview.qt
