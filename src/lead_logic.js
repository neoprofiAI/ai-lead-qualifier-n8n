// Pure business logic used inside the n8n Code nodes.
// scripts/build_workflows.py inlines this file into every Code node, so the exact
// same code is unit-tested with `node --test` and executed by n8n.

const FREE_EMAIL_DOMAINS = ['gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com', 'mail.ru', 'yandex.ru', 'icloud.com', 'proton.me', 'protonmail.com'];
const TIERS = ['Hot', 'Warm', 'Cold', 'Needs review'];
const INTENTS = ['buy', 'research', 'partnership', 'job', 'spam', 'other'];

function clean(v, max = 2000) {
  if (v === undefined || v === null) return '';
  return String(v).replace(/\s+/g, ' ').trim().slice(0, max);
}

function hashId(s) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return (h >>> 0).toString(16).padStart(8, '0');
}

// Accepts payloads from web forms (Tally/Typeform/HTML) or parsed emails and maps them to one shape.
function normalizeLead(raw, nowIso) {
  const body = raw && raw.body ? raw.body : (raw || {});
  const email = clean(body.email || body.Email || body.from_email || body.from, 254).toLowerCase();
  const name = clean(body.name || body.full_name || [body.first_name, body.last_name].filter(Boolean).join(' '), 120);
  const domain = email.includes('@') ? email.split('@')[1] : '';
  const errors = [];
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) errors.push('invalid_email');
  const lead = {
    lead_id: 'L-' + hashId(email || name || nowIso),
    name: name || 'Unknown',
    email,
    company: clean(body.company || body.organization, 120) || (domain && !FREE_EMAIL_DOMAINS.includes(domain) ? domain : ''),
    domain,
    free_email: FREE_EMAIL_DOMAINS.includes(domain),
    phone: clean(body.phone, 40),
    website: clean(body.website || (domain && !FREE_EMAIL_DOMAINS.includes(domain) ? 'https://' + domain : ''), 200),
    budget: clean(body.budget, 80),
    timeline: clean(body.timeline, 80),
    message: clean(body.message || body.text || body.body_text || body.subject, 3000),
    source: clean(body.source || 'web_form', 40),
    received_at: nowIso,
  };
  return { lead, errors };
}

function buildPrompt(lead, { strict = false, model = 'gpt-4o-mini', maxTokens = 1200 } = {}) {
  const system = [
    'You are a B2B sales-development assistant for an AI automation agency.',
    'Qualify the inbound lead and answer ONLY with a JSON object, no markdown, with keys:',
    '{"score": integer 0-100, "tier": "Hot"|"Warm"|"Cold", "reason": string (max 160 chars),',
    ' "intent": "buy"|"research"|"partnership"|"job"|"spam"|"other", "budget": string, "timeline": string,',
    ' "is_spam": boolean, "suggested_reply": string (2-4 sentences, friendly, in the lead\'s language)}.',
    'Scoring rubric: clear business need for automation/AI (+30), budget stated >= $1,000 (+25),',
    'timeline within 1 month (+20), company email/domain (+10), decision maker (+10), detail (+5).',
    'Spam, SEO/link offers, crypto, job seekers or vague one-liners score below 20.',
    'Tier: Hot >= 70, Warm 40-69, Cold < 40.',
  ].join('\n');
  const user = (strict ? 'STRICT MODE: your previous answer was not valid JSON. Return ONLY the JSON object.\n' : '') +
    'Lead:\n<lead>\n' + JSON.stringify(lead) + '\n</lead>';
  return {
    model,
    max_tokens: maxTokens,
    temperature: 0.2,
    response_format: { type: 'json_object' },
    messages: [{ role: 'system', content: system }, { role: 'user', content: user }],
  };
}

function extractJson(text) {
  if (typeof text !== 'string') return null;
  const t = text.replace(/```(?:json)?/gi, '').trim();
  const start = t.indexOf('{');
  const end = t.lastIndexOf('}');
  if (start < 0 || end <= start) return null;
  try { return JSON.parse(t.slice(start, end + 1)); } catch (e) { return null; }
}

function tierFromScore(score, hot = 70) {
  if (score >= hot) return 'Hot';
  if (score >= 40) return 'Warm';
  return 'Cold';
}

// Validates the LLM output against the JSON schema (hand-rolled, no deps inside n8n).
function validateQualification(obj, hot = 70) {
  const errors = [];
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return { valid: false, errors: ['not_an_object'], data: null };
  const score = Number(obj.score);
  if (!Number.isInteger(score) || score < 0 || score > 100) errors.push('score must be an integer 0-100');
  if (!['Hot', 'Warm', 'Cold'].includes(obj.tier)) errors.push('tier must be Hot|Warm|Cold');
  if (typeof obj.reason !== 'string' || !obj.reason.trim()) errors.push('reason required');
  if (typeof obj.suggested_reply !== 'string' || !obj.suggested_reply.trim()) errors.push('suggested_reply required');
  if (obj.intent !== undefined && !INTENTS.includes(obj.intent)) errors.push('intent invalid');
  if (errors.length) return { valid: false, errors, data: null };
  const data = {
    score,
    // The tier is derived from the score so routing is deterministic even if the model disagrees with itself.
    tier: tierFromScore(score, hot),
    reason: obj.reason.trim().slice(0, 200),
    intent: obj.intent || 'other',
    budget: clean(obj.budget, 80),
    timeline: clean(obj.timeline, 80),
    is_spam: Boolean(obj.is_spam),
    suggested_reply: obj.suggested_reply.trim().slice(0, 1200),
  };
  return { valid: true, errors: [], data };
}

function parseLlmResponse(resp, hot = 70) {
  const content = resp && resp.choices && resp.choices[0] && resp.choices[0].message ? resp.choices[0].message.content : null;
  const usage = (resp && resp.usage) || {};
  const v = validateQualification(extractJson(content), hot);
  return { ...v, usage: { prompt_tokens: usage.prompt_tokens || 0, completion_tokens: usage.completion_tokens || 0 }, model: (resp && resp.model) || '' };
}

function fallbackQualification(errors) {
  return {
    score: 0, tier: 'Needs review', reason: 'AI output invalid after retry: ' + (errors || []).join('; ').slice(0, 150),
    intent: 'other', budget: '', timeline: '', is_spam: false,
    suggested_reply: '',
  };
}

function computeCost(usages, priceInPerM, priceOutPerM) {
  let pin = 0, pout = 0;
  for (const u of usages) { pin += u.prompt_tokens || 0; pout += u.completion_tokens || 0; }
  const cost = (pin * Number(priceInPerM || 0) + pout * Number(priceOutPerM || 0)) / 1e6;
  return { prompt_tokens: pin, completion_tokens: pout, cost_usd: Math.round(cost * 1e6) / 1e6 };
}

// Running average kept in n8n workflow static data (persisted for production executions).
function updateCostStats(stats, costUsd) {
  const s = { leads: (stats.leads || 0) + 1, total_cost_usd: (stats.total_cost_usd || 0) + costUsd };
  s.avg_cost_usd = Math.round((s.total_cost_usd / s.leads) * 1e6) / 1e6;
  return s;
}

function buildCrmRecord(lead, q, meta) {
  return {
    lead_id: lead.lead_id,
    name: lead.name,
    email: lead.email,
    company: lead.company,
    phone: lead.phone,
    source: lead.source,
    message: lead.message,
    score: q.score,
    tier: q.tier,
    reason: q.reason,
    intent: q.intent,
    budget: q.budget || lead.budget,
    timeline: q.timeline || lead.timeline,
    suggested_reply: q.suggested_reply,
    status: q.tier === 'Needs review' ? 'Needs review' : (q.tier === 'Hot' ? 'Contact now' : 'New'),
    ai_model: meta.model,
    ai_attempts: meta.attempts,
    ai_tokens: meta.cost.prompt_tokens + meta.cost.completion_tokens,
    ai_cost_usd: meta.cost.cost_usd,
    avg_ai_cost_per_lead_usd: meta.stats.avg_cost_usd,
    received_at: lead.received_at,
    processed_at: meta.now,
  };
}

function splitName(name) {
  const parts = String(name || '').trim().split(/\s+/);
  return { first: parts[0] || '', last: parts.slice(1).join(' ') };
}

// --- CRM adapters (request bodies for the real public APIs) ---
function hubspotUpsertBody(r) {
  const n = splitName(r.name);
  return {
    inputs: [{
      idProperty: 'email', id: r.email,
      properties: {
        email: r.email, firstname: n.first, lastname: n.last, company: r.company, phone: r.phone,
        hs_lead_status: r.tier === 'Hot' ? 'OPEN' : 'NEW', lifecyclestage: 'lead',
        ai_score: r.score, ai_tier: r.tier, ai_reason: r.reason, ai_intent: r.intent,
        ai_budget: r.budget, ai_timeline: r.timeline, ai_suggested_reply: r.suggested_reply,
        ai_cost_usd: r.ai_cost_usd, ai_avg_cost_per_lead_usd: r.avg_ai_cost_per_lead_usd,
        lead_source_detail: r.source, lead_message: r.message,
      },
    }],
  };
}

const AIRTABLE_FIELDS = {
  lead_id: 'Lead ID', name: 'Name', email: 'Email', company: 'Company', phone: 'Phone', source: 'Source',
  message: 'Message', score: 'Score', tier: 'Tier', reason: 'Reason', intent: 'Intent', budget: 'Budget',
  timeline: 'Timeline', suggested_reply: 'Suggested Reply', status: 'Status', ai_model: 'AI Model',
  ai_attempts: 'AI Attempts', ai_tokens: 'AI Tokens', ai_cost_usd: 'AI Cost USD',
  avg_ai_cost_per_lead_usd: 'Avg AI Cost per Lead USD', received_at: 'Received At', processed_at: 'Processed At',
};

function toLabeledFields(r) {
  const f = {};
  for (const [k, label] of Object.entries(AIRTABLE_FIELDS)) f[label] = r[k] === undefined ? '' : r[k];
  return f;
}

function airtableUpsertBody(r) {
  return { performUpsert: { fieldsToMergeOn: ['Email'] }, typecast: true, records: [{ fields: toLabeledFields(r) }] };
}

function sheetsUpsertBody(r, token) {
  return { token: token || '', action: 'upsert', sheet: 'Leads', key: 'Email', record: toLabeledFields(r) };
}

function parseCrmResult(target, resp) {
  resp = resp || {};
  if (target === 'hubspot') {
    const res = (resp.results || [])[0] || {};
    return { crm: 'hubspot', crm_record_id: String(res.id || ''), action: res.new ? 'created' : 'updated' };
  }
  if (target === 'airtable') {
    const rec = (resp.records || [])[0] || {};
    const created = (resp.createdRecords || []).includes(rec.id);
    return { crm: 'airtable', crm_record_id: rec.id || '', action: created ? 'created' : 'updated' };
  }
  return { crm: 'sheets', crm_record_id: resp.row ? 'row-' + resp.row : '', action: resp.action || 'unknown' };
}

// --- Notifications ---
function esc(s) { return String(s || '').replace(/[<>&]/g, (c) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;' }[c])); }

function telegramAlert(r, chatId) {
  const text = [
    `🔥 <b>Hot lead — ${r.score}/100</b>`,
    `<b>${esc(r.name)}</b> · ${esc(r.company || '—')}`,
    `✉️ ${esc(r.email)}${r.phone ? ' · 📞 ' + esc(r.phone) : ''}`,
    `💡 ${esc(r.reason)}`,
    r.budget || r.timeline ? `💰 ${esc(r.budget || '?')} · ⏱ ${esc(r.timeline || '?')}` : '',
    '',
    `<b>Suggested reply:</b>`,
    `<i>${esc(r.suggested_reply)}</i>`,
  ].filter((l) => l !== null).join('\n');
  return { chat_id: chatId, text, parse_mode: 'HTML', disable_web_page_preview: true };
}

function emailAlert(r, from, to) {
  return {
    from, to: [to],
    subject: `🔥 Hot lead: ${r.name} (${r.company || r.email}) — score ${r.score}`,
    html: `<h2>Hot lead — ${r.score}/100</h2><p><b>${esc(r.name)}</b>, ${esc(r.company)}<br>${esc(r.email)} ${esc(r.phone)}</p>` +
      `<p><b>Why:</b> ${esc(r.reason)}</p><p><b>Budget:</b> ${esc(r.budget)} · <b>Timeline:</b> ${esc(r.timeline)}</p>` +
      `<p><b>Message:</b> ${esc(r.message)}</p><hr><p><b>Suggested reply:</b><br>${esc(r.suggested_reply)}</p>`,
  };
}

function formatErrorAlert(errData, nowIso) {
  const ex = (errData && errData.execution) || {};
  const wf = (errData && errData.workflow) || {};
  const err = ex.error || {};
  const record = {
    time: nowIso,
    workflow: wf.name || '',
    execution_id: String(ex.id || ''),
    failed_node: ex.lastNodeExecuted || (err.node && err.node.name) || '',
    error: clean(err.message || err.description || 'Unknown error', 500),
    execution_url: ex.url || '',
  };
  const text = `🚨 <b>Workflow failed</b>: ${esc(record.workflow)}\nNode: <b>${esc(record.failed_node)}</b>\nError: <code>${esc(record.error)}</code>\nExecution: ${esc(record.execution_id)} ${esc(record.execution_url)}`;
  return { record, text };
}

if (typeof module !== 'undefined') {
  module.exports = {
    FREE_EMAIL_DOMAINS, TIERS, normalizeLead, buildPrompt, extractJson, tierFromScore, validateQualification,
    parseLlmResponse, fallbackQualification, computeCost, updateCostStats, buildCrmRecord, hubspotUpsertBody,
    airtableUpsertBody, sheetsUpsertBody, parseCrmResult, telegramAlert, emailAlert, formatErrorAlert, toLabeledFields,
  };
}
