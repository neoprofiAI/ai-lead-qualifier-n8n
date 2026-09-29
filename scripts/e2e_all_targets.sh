#!/usr/bin/env bash
# Runs the end-to-end suite once per CRM target (hubspot, airtable, sheets), restarting n8n each time.
# Requires the mock services on :8787 (scripts/start_mock.sh) and port 5678 free.
set -uo pipefail
cd "$(dirname "$0")/.."
rc=0
for t in hubspot airtable sheets; do
  echo "=== CRM_TARGET=$t ==="
  CRM_TARGET=$t ./scripts/start_n8n.sh > "/tmp/n8n-$t.log" 2>&1 &
  pid=$!
  for i in $(seq 1 90); do curl -sf http://127.0.0.1:5678/healthz >/dev/null && break; sleep 2; done
  sleep 5
  CRM_TARGET=$t python -m pytest -q tests/test_e2e_n8n.py || rc=1
  pkill -P $pid 2>/dev/null; kill $pid 2>/dev/null; sleep 1
  pkill -f "$PWD/.n8n-data" 2>/dev/null
  for i in $(seq 1 30); do curl -sf http://127.0.0.1:5678/healthz >/dev/null || break; sleep 1; done
done
exit $rc
