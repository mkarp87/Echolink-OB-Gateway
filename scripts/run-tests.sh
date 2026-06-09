#!/usr/bin/env bash
set -euo pipefail
APP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$APP_ROOT/app:${PYTHONPATH:-}"
if [ -x "$APP_ROOT/venv/bin/python" ]; then
  exec "$APP_ROOT/venv/bin/python" -m pytest -q "$@"
fi
if command -v python3.12 >/dev/null 2>&1; then
  exec python3.12 -m pytest -q "$@"
fi
if command -v python3 >/dev/null 2>&1; then
  exec python3 -m pytest -q "$@"
fi
echo "ERROR: Python 3.12+ or project virtualenv not found." >&2
exit 1
