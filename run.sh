#!/bin/sh
# Start jobradar on this machine, against the one database in ./data.
# Runs on the host on purpose: scoring uses your logged-in `claude` CLI, which
# only exists here. Docker is for when you'd rather pay per token — see README.
set -e
cd "$(dirname "$0")"
if command -v docker >/dev/null 2>&1; then
  docker compose stop >/dev/null 2>&1 || true   # never two writers on one DB
fi
exec python3 -m uvicorn app:app --host 127.0.0.1 --port 8420 "$@"
