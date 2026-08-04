#!/usr/bin/env bash
# Start the draft app: engine API on :8000, interface on :5173.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d web/node_modules ]; then
  echo "installing front-end deps (first run only)…"
  (cd web && npm install)
fi

cleanup() { kill 0 2>/dev/null || true; }
trap cleanup EXIT INT TERM

.venv/bin/python -m uvicorn server.app:app --host 127.0.0.1 --port 8000 --log-level warning &
(cd web && npm run dev) &

echo
echo "  FantasyEdge draft  ->  http://localhost:5173"
echo "  engine API         ->  http://localhost:8000/docs"
echo "  ctrl-c to stop"
echo
wait
