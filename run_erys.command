#!/bin/zsh

cd "$(dirname "$0")" || exit 1

if [[ -x ".venv/bin/python3" ]]; then
  exec ./.venv/bin/python3 ery_app_v9.py
fi

exec python3 ery_app_v9.py
