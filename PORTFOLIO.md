# Upwork portfolio item — AI Lead Qualifier

**Title:** AI Lead Qualifier — n8n + LLM + HubSpot/Airtable/Google Sheets

**Write-up (paste into Upwork "Project description"):**
Every new lead from a web form is captured, deduplicated by email, scored by an LLM (0–100 with a written reason,
budget/timeline/intent) and written to the CRM — and if it's hot, the sales team gets a Telegram + email alert with a
ready-to-send first reply, all in seconds. The AI output is validated against a JSON schema (one strict retry, then a
safe "Needs review" fallback), a separate error workflow alerts on any failure (no silent failures), and the average
AI cost per lead is logged. One config switch targets HubSpot, Airtable or Google Sheets. Works with Claude, OpenAI or
any OpenAI-compatible model. Stack: n8n, Claude/OpenAI (JSON output), HubSpot / Airtable / Google Sheets, Telegram,
email. Result: no lead waits hours for a reply, and reps only spend time on qualified prospects.

**Skills to tag:** n8n · AI Automation · Workflow Automation · HubSpot · Airtable · Google Sheets · OpenAI API · Claude · Telegram Bot · API Integration

**Images (upload in this order):**
1. `docs/screenshots/02-n8n-workflow.png` (cover)
2. `docs/screenshots/03-n8n-execution-success.png`
3. `docs/screenshots/04-crm-and-telegram-alerts.png`
4. `docs/screenshots/06-error-alert.png`
5. `docs/screenshots/05-replay-terminal.png`

**Demo video script (60–90 s, Loom; record with `./scripts/start_mock.sh` + `./scripts/start_n8n.sh`):**
- 0–10 s — Title card: "Leads wait 4 hours for a reply. Here they get one in seconds." Show the form (`http://127.0.0.1:8787/form`).
- 10–30 s — Fill the form as a hot lead (budget, timeline, clear need) → switch to n8n Executions, open the running/finished execution.
- 30–50 s — Mock CRM dashboard (`http://127.0.0.1:8787/`): record with score, tier, reason; Telegram alert card with the suggested reply.
- 50–70 s — Submit a spam lead → tagged Cold, no alert. Then `curl -X POST 127.0.0.1:8787/admin/fail/hubspot/1` (or airtable/sheets/llm) ("break the API key"), submit again → error alert in Telegram + Errors sheet.
- 70–90 s — "Works with HubSpot, Airtable or Google Sheets — one config switch. Setup for your stack in about a day."
