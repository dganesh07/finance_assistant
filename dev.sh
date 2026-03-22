#!/bin/bash
# dev.sh — Start the Finance Agent backend and frontend in one command.
#
# Usage:
#   chmod +x dev.sh   (first time only)
#   ./dev.sh
#
# Stops both servers with Ctrl+C.

set -e

# Activate venv if present
if [ -f ".venv/bin/activate" ]; then
  source .venv/bin/activate
fi

echo ""
echo "  Starting Finance Agent…"
echo "  API  →  http://localhost:8000"
echo "  UI   →  http://localhost:5173"
echo "  Docs →  http://localhost:8000/docs"
echo ""

# Start FastAPI in background
uvicorn api:app --reload --port 8000 &
API_PID=$!

# Start Vite dev server
cd frontend && npm run dev &
VITE_PID=$!

# On Ctrl+C, kill both
trap "kill $API_PID $VITE_PID 2>/dev/null; echo 'Stopped.'" INT TERM

wait
