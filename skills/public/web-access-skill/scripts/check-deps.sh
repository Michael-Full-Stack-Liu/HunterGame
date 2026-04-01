#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
cd "$ROOT_DIR"

echo "Checking web-access-skill prerequisites..."

if ! command -v curl >/dev/null 2>&1; then
  echo "Missing dependency: curl"
  exit 1
fi
echo "OK: curl found"

if [ ! -x "venv/bin/python" ]; then
  echo "Missing virtualenv interpreter at venv/bin/python"
  exit 1
fi
echo "OK: venv/bin/python found"

if ! venv/bin/python -c "import playwright" >/dev/null 2>&1; then
  echo "Missing Python dependency: playwright"
  exit 1
fi
echo "OK: Python playwright import works"

if curl -fsS "http://127.0.0.1:9222/json/version" >/dev/null 2>&1; then
  echo "OK: Chrome remote debugging reachable on 127.0.0.1:9222"
else
  echo "Chrome remote debugging not reachable on 127.0.0.1:9222"
  echo "Start Chrome with --remote-debugging-port=9222 or enable it in chrome://inspect/#remote-debugging"
  exit 1
fi

echo "web-access-skill prerequisites look good"
