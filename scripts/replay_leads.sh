#!/usr/bin/env bash
# Replay every lead in test_leads.json with plain curl (no Python needed).
URL="${1:-http://localhost:5678/webhook/lead}"
jq -c '.[] | del(.expect_tier)' "$(dirname "$0")/../test_leads.json" | while read -r lead; do
  curl -s -X POST "$URL" -H 'content-type: application/json' -d "$lead" | jq -c '{tier, score, crm_action, alerted, reason}'
done
