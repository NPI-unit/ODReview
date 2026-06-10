#!/bin/bash
# EryReview starten mit dem venv (Homebrew-Python, Tk 9.0.3) statt conda.
# Behebt den Tk-8.6.13-Segfault auf macOS 26 (Tahoe).
cd "$(dirname "$0")" || exit 1
exec ./.venv/bin/python -u -m erysreview
