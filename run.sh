#!/usr/bin/env bash
# Launcher for macOS / Linux: creates a virtual environment, installs
# dependencies, and starts the Audiobook Creator web UI.
set -e
cd "$(dirname "$0")"

# --- Find a Python 3 interpreter -------------------------------------------
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "Python 3 was not found. Install it from https://www.python.org/downloads/"
  echo "or your package manager (e.g. 'sudo apt install python3 python3-venv')."
  exit 1
fi

# --- Create the virtual environment on first run ---------------------------
if [ ! -x ".venv/bin/python" ]; then
  echo "Creating virtual environment..."
  "$PY" -m venv .venv
fi
VENV_PY=".venv/bin/python"

# --- Install / update dependencies -----------------------------------------
echo "Installing dependencies (first run may take a minute)..."
"$VENV_PY" -m pip install --quiet --upgrade pip
"$VENV_PY" -m pip install --quiet -r requirements.txt

# The offline (pyttsx3) engine speaks through 'espeak' on Linux.
if [ "$(uname -s)" = "Linux" ] && ! command -v espeak-ng >/dev/null 2>&1 && ! command -v espeak >/dev/null 2>&1; then
  echo "Note: the Offline engine on Linux needs espeak — install with 'sudo apt install espeak-ng'."
  echo "      (The Online engine works without it, as long as you have internet.)"
fi

# --- Launch -----------------------------------------------------------------
echo "Opening http://127.0.0.1:5000 ..."
( sleep 1
  if command -v xdg-open >/dev/null 2>&1; then xdg-open http://127.0.0.1:5000
  elif command -v open >/dev/null 2>&1; then open http://127.0.0.1:5000
  fi ) >/dev/null 2>&1 &

exec "$VENV_PY" app.py
