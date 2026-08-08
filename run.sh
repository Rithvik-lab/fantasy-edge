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

# An already-running engine keeps the port and quietly serves whatever code it
# started with. That is how you end up staring at "not found" on a feature you
# just built, so say something rather than fail to bind.
if curl -s -o /dev/null --max-time 1 http://127.0.0.1:8000/api/health 2>/dev/null; then
  echo "  ! an engine is already listening on :8000 — it may be running old code."
  echo "    stop it first if you have changed anything since it started."
  echo
fi

.venv/bin/python -m uvicorn server.app:app --host 127.0.0.1 --port 8000 --log-level warning &
(cd web && npm run dev) &

echo
echo "  FantasyEdge draft  ->  http://localhost:5173"
echo "  engine API         ->  http://localhost:8000/docs"
echo "  ctrl-c to stop"
echo
wait
