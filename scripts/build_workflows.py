#!/usr/bin/env python3
"""Generate the importable n8n workflow JSON files from src/lead_logic.js.

The business logic lives in one tested JS file; this script inlines it into each Code node so the
workflows in /workflows are always in sync with the unit-tested code. Re-run after editing src/.
"""
import json
import re
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOGIC = (ROOT / "src" / "lead_logic.js").read_text()
LOGIC = re.sub(r"\nif \(typeof module !== 'undefined'\) \{.*\}\s*$", "\n", LOGIC, flags=re.S)
MAIN_ID = "LeadQualifierMain01"
ERROR_ID = "LeadQualifierErr001"


def nid(name):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "lead-qualifier/" + name))


def code(name, pos, body, notes=""):
    return {
        "parameters": {"jsCode": "// Shared logic from src/lead_logic.js (generated — edit src/, then run scripts/build_workflows.py)\n"
                       + LOGIC + "\n// ---- node logic ----\n" + body.strip() + "\n"},
        "id": nid(name), "name": name, "type": "n8n-nodes-base.code", "typeVersion": 2, "position": pos,
        **({"notes": notes, "notesInFlow": True} if notes else {}),
    }


def http(name, pos, method, url, body_expr=None, auth_expr=None, retry=True, on_error=None, notes="", tries=3, wait_ms=1500):
    p = {"method": method, "url": url, "options": {"timeout": 60000}}
    if auth_expr:
        p["sendHeaders"] = True
        p["headerParameters"] = {"parameters": [{"name": "Authorization", "value": auth_expr}]}
    if body_expr:
        p["sendBody"] = True
        p["specifyBody"] = "json"
        p["jsonBody"] = body_expr
    n = {"parameters": p, "id": nid(name), "name": name, "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": pos}
    if retry:
        n.update({"retryOnFail": True, "maxTries": tries, "waitBetweenTries": wait_ms})
    if on_error:
        n["onError"] = on_error
    if notes:
        n.update({"notes": notes, "notesInFlow": True})
    return n


_cond_counter = [0]


def cond(left, op_type, operation, right=None):
    _cond_counter[0] += 1
    c = {"id": nid(f"cond-{_cond_counter[0]}"), "leftValue": left, "operator": {"type": op_type, "operation": operation}}
    if right is None:
        c["rightValue"] = ""
        c["operator"]["singleValue"] = True
    else:
        c["rightValue"] = right
    return {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": 2},
            "conditions": [c], "combinator": "and"}


def if_node(name, pos, conditions):
    return {"parameters": {"conditions": conditions, "options": {}}, "id": nid(name), "name": name,
            "type": "n8n-nodes-base.if", "typeVersion": 2.2, "position": pos}


def sticky(name, pos, content, w, h, color=7):
    return {"parameters": {"content": content, "width": w, "height": h, "color": color}, "id": nid(name), "name": name,
            "type": "n8n-nodes-base.stickyNote", "typeVersion": 1, "position": pos}


def link(conns, a, b, out=0):
    m = conns.setdefault(a, {"main": []})["main"]
    while len(m) <= out:
        m.append([])
    m[out].append({"node": b, "type": "main", "index": 0})


HOT = "Number($env.HOT_THRESHOLD || 70)"

nodes = [
    sticky("Note: Overview", [-80, -420], "## 🧲 AI Lead Qualifier\nWeb form / email lead → normalize → **LLM score (JSON-schema validated, 1 retry, fallback)** → CRM upsert (dedup by email) → hot lead alerts.\n\nConfig via env: `CRM_TARGET` = hubspot | airtable | sheets. Failures → *Lead Qualifier – Error Alerts* workflow.", 620, 200, 5),
    sticky("Note: AI", [520, -150], "### 🤖 AI qualification\nOpenAI-compatible endpoint (`LLM_BASE_URL`) — OpenAI, Claude, Bedrock, or the offline mock. `max_tokens` capped, cost per lead tracked.", 700, 460, 6),
    sticky("Note: CRM", [1460, -150], "### 🗂 CRM upsert (one record per email)\nHubSpot batch upsert · Airtable performUpsert · Google Sheets (Apps Script)", 520, 620, 4),
    sticky("Note: Alerts", [2200, -150], "### 🔔 Hot lead alerts (score ≥ 70)\nTelegram + email with reason & suggested reply", 640, 460, 3),
    {"parameters": {"httpMethod": "POST", "path": "lead", "responseMode": "responseNode", "options": {}},
     "id": nid("Lead Webhook"), "name": "Lead Webhook", "type": "n8n-nodes-base.webhook", "typeVersion": 2,
     "position": [0, 0], "webhookId": "5b2f7a52-9d7e-4c1a-9a53-1ad1ea5e0c01"},
    code("Normalize Lead", [220, 0], """
const now = new Date().toISOString();
const { lead, errors } = normalizeLead($input.first().json, now);
if (errors.length) throw new Error('Invalid lead payload: ' + errors.join(', '));
const cfg = { model: $env.LLM_MODEL || 'gpt-4o-mini', maxTokens: Number($env.LLM_MAX_TOKENS || 1200) };
return [{ json: { lead, llm_request: buildPrompt(lead, cfg), llm_request_strict: buildPrompt(lead, { ...cfg, strict: true }) } }];
""", "Map form/email fields, validate email"),
    http("LLM: Qualify Lead", [580, 0], "POST", "={{ $env.LLM_BASE_URL }}/chat/completions",
         "={{ JSON.stringify($json.llm_request) }}", "=Bearer {{ $env.LLM_API_KEY }}", notes="score, tier, reason, reply", tries=5, wait_ms=5000),  # rides out 429 throttling
    code("Validate AI Output", [800, 0], f"""
const r = parseLlmResponse($input.first().json, {HOT});
return [{{ json: {{ ...r, attempt: 1 }} }}];
""", "JSON schema check"),
    if_node("Valid JSON?", [1020, 0], cond("={{ $json.valid }}", "boolean", "true")),
    http("LLM: Retry (strict)", [800, 200], "POST", "={{ $env.LLM_BASE_URL }}/chat/completions",
         "={{ JSON.stringify($('Normalize Lead').first().json.llm_request_strict) }}", "=Bearer {{ $env.LLM_API_KEY }}", tries=5, wait_ms=5000),
    code("Validate Retry / Fallback", [1020, 200], f"""
const r = parseLlmResponse($input.first().json, {HOT});
const first = $('Validate AI Output').first().json;
if (!r.valid) {{
  return [{{ json: {{ valid: false, data: fallbackQualification(r.errors), usage: r.usage, prev_usage: first.usage, model: r.model, attempt: 2 }} }}];
}}
return [{{ json: {{ ...r, prev_usage: first.usage, attempt: 2 }} }}];
""", "invalid twice → 'Needs review'"),
    code("Build CRM Record", [1260, 0], """
const lead = $('Normalize Lead').first().json.lead;
const q = $input.first().json;
const usages = [q.usage].concat(q.prev_usage ? [q.prev_usage] : []);
const cost = computeCost(usages, $env.LLM_PRICE_INPUT_PER_M, $env.LLM_PRICE_OUTPUT_PER_M);
const sd = $getWorkflowStaticData('global');
const stats = updateCostStats(sd.costStats || {}, cost.cost_usd);
sd.costStats = stats;
const record = buildCrmRecord(lead, q.data, { model: q.model || $env.LLM_MODEL, attempts: q.attempt, cost, stats, now: new Date().toISOString() });
const target = String($env.CRM_TARGET || 'sheets').toLowerCase();
return [{ json: { target, record, hubspot_body: hubspotUpsertBody(record), airtable_body: airtableUpsertBody(record), sheets_body: sheetsUpsertBody(record, $env.SHEETS_WEBHOOK_TOKEN) } }];
""", "cost per lead + running avg"),
    {"parameters": {"rules": {"values": [
        {"conditions": cond("={{ $json.target }}", "string", "equals", "hubspot"), "renameOutput": True, "outputKey": "HubSpot"},
        {"conditions": cond("={{ $json.target }}", "string", "equals", "airtable"), "renameOutput": True, "outputKey": "Airtable"},
        {"conditions": cond("={{ $json.target }}", "string", "equals", "sheets"), "renameOutput": True, "outputKey": "Google Sheets"},
    ]}, "options": {}}, "id": nid("CRM Target"), "name": "CRM Target", "type": "n8n-nodes-base.switch", "typeVersion": 3.2,
     "position": [1520, 0]},
    http("HubSpot: Upsert Contact", [1760, -60], "POST", "={{ $env.HUBSPOT_BASE_URL || 'https://api.hubapi.com' }}/crm/v3/objects/contacts/batch/upsert",
         "={{ JSON.stringify($json.hubspot_body) }}", "=Bearer {{ $env.HUBSPOT_TOKEN }}"),
    http("Airtable: Upsert Record", [1760, 120], "PATCH", "={{ $env.AIRTABLE_BASE_URL || 'https://api.airtable.com' }}/v0/{{ $env.AIRTABLE_BASE_ID }}/{{ encodeURIComponent($env.AIRTABLE_TABLE || 'Leads') }}",
         "={{ JSON.stringify($json.airtable_body) }}", "=Bearer {{ $env.AIRTABLE_TOKEN }}"),
    http("Google Sheets: Upsert Row", [1760, 300], "POST", "={{ $env.SHEETS_WEBHOOK_URL }}", "={{ JSON.stringify($json.sheets_body) }}"),
    code("Merge CRM Result", [2020, 120], """
const b = $('Build CRM Record').first().json;
const crm = parseCrmResult(b.target, $input.first().json);
const r = b.record;
return [{ json: { record: r, crm, hot: r.tier === 'Hot',
  telegram_body: telegramAlert(r, $env.TELEGRAM_CHAT_ID),
  email_body: emailAlert(r, $env.EMAIL_FROM || 'alerts@example.com', $env.SALES_EMAIL || 'sales@example.com') } }];
"""),
    if_node("Hot lead?", [2260, 120], cond("={{ $json.hot }}", "boolean", "true")),
    http("Telegram: Alert Sales", [2440, 20], "POST", "={{ $env.TELEGRAM_API_BASE || 'https://api.telegram.org' }}/bot{{ $env.TELEGRAM_BOT_TOKEN }}/sendMessage",
         "={{ JSON.stringify($json.telegram_body) }}"),
    http("Email: Alert Sales", [2660, 20], "POST", "={{ $env.EMAIL_API_URL || 'https://api.resend.com/emails' }}",
         "={{ JSON.stringify($('Merge CRM Result').first().json.email_body) }}", "=Bearer {{ $env.EMAIL_API_KEY }}"),
    code("Build Response", [2900, 120], """
const m = $('Merge CRM Result').first().json;
const r = m.record;
return [{ json: { ok: true, lead_id: r.lead_id, email: r.email, score: r.score, tier: r.tier, reason: r.reason,
  crm: m.crm.crm, crm_action: m.crm.action, crm_record_id: m.crm.crm_record_id, alerted: m.hot,
  ai_attempts: r.ai_attempts, ai_cost_usd: r.ai_cost_usd, avg_ai_cost_per_lead_usd: r.avg_ai_cost_per_lead_usd } }];
"""),
    {"parameters": {"respondWith": "firstIncomingItem", "options": {}}, "id": nid("Respond"), "name": "Respond to Form",
     "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.1, "position": [3120, 120]},
]

conns = {}
link(conns, "Lead Webhook", "Normalize Lead")
link(conns, "Normalize Lead", "LLM: Qualify Lead")
link(conns, "LLM: Qualify Lead", "Validate AI Output")
link(conns, "Validate AI Output", "Valid JSON?")
link(conns, "Valid JSON?", "Build CRM Record", 0)
link(conns, "Valid JSON?", "LLM: Retry (strict)", 1)
link(conns, "LLM: Retry (strict)", "Validate Retry / Fallback")
link(conns, "Validate Retry / Fallback", "Build CRM Record")
link(conns, "Build CRM Record", "CRM Target")
link(conns, "CRM Target", "HubSpot: Upsert Contact", 0)
link(conns, "CRM Target", "Airtable: Upsert Record", 1)
link(conns, "CRM Target", "Google Sheets: Upsert Row", 2)
for n in ("HubSpot: Upsert Contact", "Airtable: Upsert Record", "Google Sheets: Upsert Row"):
    link(conns, n, "Merge CRM Result")
link(conns, "Merge CRM Result", "Hot lead?")
link(conns, "Hot lead?", "Telegram: Alert Sales", 0)
link(conns, "Hot lead?", "Build Response", 1)
link(conns, "Telegram: Alert Sales", "Email: Alert Sales")
link(conns, "Email: Alert Sales", "Build Response")
link(conns, "Build Response", "Respond to Form")

main = {"id": MAIN_ID, "name": "Lead Qualifier – Main", "active": False, "nodes": nodes, "connections": conns,
        "settings": {"executionOrder": "v1", "errorWorkflow": ERROR_ID, "saveManualExecutions": True},
        "pinData": {}, "tags": []}

enodes = [
    sticky("Note: Errors", [-60, -220], "## 🚨 Error workflow\nCatches **any** failed node in *Lead Qualifier – Main* → Telegram alert + row in the *Errors* sheet. No silent failures.", 560, 160, 2),
    {"parameters": {}, "id": nid("Error Trigger"), "name": "Error Trigger", "type": "n8n-nodes-base.errorTrigger", "typeVersion": 1, "position": [0, 0]},
    code("Format Error", [240, 0], """
const { record, text } = formatErrorAlert($input.first().json, new Date().toISOString());
return [{ json: { record, telegram_body: { chat_id: $env.TELEGRAM_CHAT_ID, text, parse_mode: 'HTML' },
  log_body: { token: $env.SHEETS_WEBHOOK_TOKEN || '', action: 'log_error', sheet: 'Errors', record } } }];
"""),
    http("Telegram: Error Alert", [480, 0], "POST", "={{ $env.TELEGRAM_API_BASE || 'https://api.telegram.org' }}/bot{{ $env.TELEGRAM_BOT_TOKEN }}/sendMessage",
         "={{ JSON.stringify($json.telegram_body) }}", on_error="continueRegularOutput"),
    http("Log to Errors Sheet", [720, 0], "POST", "={{ $env.ERROR_LOG_URL || $env.SHEETS_WEBHOOK_URL }}",
         "={{ JSON.stringify($('Format Error').first().json.log_body) }}", on_error="continueRegularOutput"),
]
econns = {}
link(econns, "Error Trigger", "Format Error")
link(econns, "Format Error", "Telegram: Error Alert")
link(econns, "Telegram: Error Alert", "Log to Errors Sheet")
err = {"id": ERROR_ID, "name": "Lead Qualifier – Error Alerts", "active": False, "nodes": enodes, "connections": econns,
       "settings": {"executionOrder": "v1"}, "pinData": {}, "tags": []}

(ROOT / "workflows").mkdir(exist_ok=True)
(ROOT / "workflows" / "lead-qualifier-main.json").write_text(json.dumps(main, indent=2, ensure_ascii=False))
(ROOT / "workflows" / "lead-qualifier-error-alerts.json").write_text(json.dumps(err, indent=2, ensure_ascii=False))
print("wrote workflows/lead-qualifier-main.json and workflows/lead-qualifier-error-alerts.json")
