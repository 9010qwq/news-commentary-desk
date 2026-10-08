#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -f .venv/bin/python ]; then
  printf 'First run downloads Python dependencies and Chromium. Continue? [y/N] '
  read answer
  case "$answer" in y|Y) ;; *) exit 0;; esac
  python3 -m venv .venv
fi
if [ ! -f .venv/newsdesk-ready ]; then
  .venv/bin/python -m pip install -r requirements.txt
  .venv/bin/python -m playwright install chromium
  touch .venv/newsdesk-ready
fi
exec .venv/bin/python launch.py "$@"
