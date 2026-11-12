#!/usr/bin/env bash
# Start the web application.
#
# Settings come from .env (see .env.example). Fixture mode is the default, so
# the app starts without an Oracle database or an API key. The server binds to
# 127.0.0.1 unless .env or the flags below say otherwise.
#
# Usage:
#   ./start.sh                          start from .venv
#   ./start.sh --setup                  create .venv and install dependencies first
#   ./start.sh --reload                 restart when Python code or a template changes
#   ./start.sh --host 0.0.0.0 --port 8080
#
# Ctrl+C stops the server.

set -euo pipefail

cd "$(dirname "$0")"

VENV=".venv"
PYTHON="$VENV/bin/python"
SETUP=0
RELOAD=0
HOST=""
PORT=""

usage() {
  cat <<'TEXT'
Usage: ./start.sh [--setup] [--reload] [--host ADDRESS] [--port PORT]

  --setup     Create .venv and install dependencies before starting.
  --reload    Restart the server when Python code or templates change.
  --host      Address to bind. Overrides OIA_HOST in .env.
  --port      Port to bind. Overrides OIA_PORT in .env.
TEXT
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --setup) SETUP=1; shift ;;
    --reload) RELOAD=1; shift ;;
    --host)
      [[ $# -ge 2 ]] || { echo "--host needs a value" >&2; exit 2; }
      HOST="$2"; shift 2 ;;
    --port)
      [[ $# -ge 2 ]] || { echo "--port needs a value" >&2; exit 2; }
      PORT="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "$SETUP" == 1 ]]; then
  if [[ ! -x "$PYTHON" ]]; then
    echo "Creating $VENV ..."
    python3 -m venv "$VENV"
  fi
  "$PYTHON" -m pip install --quiet --upgrade pip
  "$PYTHON" -m pip install --quiet -e ".[dev]"
fi

if [[ ! -x "$PYTHON" ]]; then
  echo "No virtual environment at $VENV. Run: ./start.sh --setup" >&2
  exit 1
fi

if ! "$PYTHON" -c "import uvicorn" 2>/dev/null; then
  echo "uvicorn is not installed in $VENV. Run: ./start.sh --setup" >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example. Change OIA_APP_ACCESS_TOKEN and OIA_SECRET_KEY before shared use."
fi

# Read the effective address from the same Settings object the app uses, so a
# value in .env is honored and a malformed .env fails here instead of mid-start.
if ! CONFIG="$("$PYTHON" - <<'PY'
from oracle_impact.config import Settings

settings = Settings()
print(settings.host, settings.port)
PY
)"; then
  echo "Could not read the settings. Check the OIA_ values in .env against .env.example." >&2
  exit 1
fi

read -r DEFAULT_HOST DEFAULT_PORT <<< "$CONFIG"
HOST="${HOST:-$DEFAULT_HOST}"
PORT="${PORT:-$DEFAULT_PORT}"

# The app reads its address from the environment too, so the two always agree.
export OIA_HOST="$HOST"
export OIA_PORT="$PORT"

SHOWN_HOST="$HOST"
[[ "$SHOWN_HOST" == "0.0.0.0" || "$SHOWN_HOST" == "::" ]] && SHOWN_HOST="127.0.0.1"

echo "Starting at http://$SHOWN_HOST:$PORT (Ctrl+C stops it)."
echo "Sign in with the OIA_APP_ACCESS_TOKEN in .env."

if [[ "$RELOAD" == 1 ]]; then
  exec "$PYTHON" -m uvicorn oracle_impact.main:app --host "$HOST" --port "$PORT" --reload
fi

exec "$PYTHON" -m uvicorn oracle_impact.main:app --host "$HOST" --port "$PORT"
