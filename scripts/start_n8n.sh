#!/usr/bin/env bash
# Import + publish both workflows and start n8n with the variables from .env
# Requires Node 24+ (n8n 2.x). Uses a local n8n install if present, else `npx n8n`.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || cp .env.example .env
# Variables already set in the shell win over .env, e.g. CRM_TARGET=airtable ./scripts/start_n8n.sh
_OVR=$(export -p | grep -E ' (CRM_TARGET|LLM_[A-Z_]+)=' || true)
set -a; . ./.env; set +a
eval "$_OVR"
export N8N_USER_FOLDER="${N8N_USER_FOLDER:-$PWD/.n8n-data}"
export N8N_DIAGNOSTICS_ENABLED=false N8N_PERSONALIZATION_ENABLED=false N8N_SECURE_COOKIE=false
N8N="${N8N_BIN:-npx -y n8n@2}"
$N8N import:workflow --input=workflows/lead-qualifier-error-alerts.json
$N8N import:workflow --input=workflows/lead-qualifier-main.json
$N8N publish:workflow --id=LeadQualifierErr001 || true
$N8N publish:workflow --id=LeadQualifierMain01
exec $N8N start
