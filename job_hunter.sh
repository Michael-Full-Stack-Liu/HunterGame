#!/bin/bash
# Job Hunter v2 Launcher Script

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

mkdir -p data/sandbox
mkdir -p data/logs

export PYTHONPATH="${PYTHONPATH:-}:$SCRIPT_DIR"

PYTHON_BIN="$SCRIPT_DIR/venv/bin/python"

echo "--------------------------------------------------------"
echo "  Starting Job Hunter Agent (v2)"
echo "  Project Dir: $SCRIPT_DIR"
echo "--------------------------------------------------------"

if [ ! -x "$PYTHON_BIN" ]; then
  echo "Error: virtualenv python not found at $PYTHON_BIN"
  echo "Please create the venv first:"
  echo "  python3 -m venv venv"
  echo "  source venv/bin/activate"
  echo "  pip install -r requirements.txt"
  read -r -p "Press Enter to close..."
  exit 1
fi

"$PYTHON_BIN" harness_engine/main.py
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
  echo
  echo "Job Hunter exited with code $EXIT_CODE"
  echo "Check logs: data/logs/engine_$(date +%Y_%m_%d).log"
  read -r -p "Press Enter to close..."
fi

exit $EXIT_CODE
