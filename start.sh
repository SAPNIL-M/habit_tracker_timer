#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
  echo "[stopwatch] First run: creating virtual environment..."
  python3 -m venv .venv
  echo "[stopwatch] Installing dependencies..."
  .venv/bin/python -m pip install -r requirements.txt
fi

nohup .venv/bin/python server/main.py >/tmp/stopwatch.log 2>&1 &
echo "[stopwatch] Server started:  http://127.0.0.1:8765"
echo "[stopwatch] Open that URL in your personal Chrome profile and pin the tab."
