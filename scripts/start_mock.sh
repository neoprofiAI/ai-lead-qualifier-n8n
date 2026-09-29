#!/usr/bin/env bash
# Start the mock services (LLM, HubSpot, Airtable, Sheets, Telegram, email) + dashboard on :8787
cd "$(dirname "$0")/.."
[ -f .env ] && { set -a; . ./.env; set +a; }
exec python -m uvicorn mock.server:app --host 0.0.0.0 --port "${MOCK_PORT:-8787}"
