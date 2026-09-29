// Unit tests for the logic that runs inside the n8n Code nodes.  Run: node --test tests/
const test = require('node:test');
const assert = require('node:assert/strict');
const L = require('../src/lead_logic.js');

const NOW = '2026-09-29T10:00:00.000Z';
const good = { score: 82, tier: 'Hot', reason: 'Budget and urgent need', intent: 'buy', budget: '$8k', timeline: '2 weeks', is_spam: false, suggested_reply: 'Hi! Let us talk.' };

test('normalizeLead maps webhook body, lowercases email, derives company from domain', () => {
  const { lead, errors } = L.normalizeLead({ body: { name: ' Ann  Lee ', email: 'Ann@Acme.IO', message: 'hi' } }, NOW);
  assert.deepEqual(errors, []);
  assert.equal(lead.email, 'ann@acme.io');
  assert.equal(lead.name, 'Ann Lee');
  assert.equal(lead.company, 'acme.io');
  assert.equal(lead.free_email, false);
  assert.match(lead.lead_id, /^L-[0-9a-f]{8}$/);
});

test('normalizeLead flags invalid email and free email domains', () => {
  assert.deepEqual(L.normalizeLead({ email: 'not-an-email' }, NOW).errors, ['invalid_email']);
  const { lead } = L.normalizeLead({ email: 'x@gmail.com', first_name: 'A', last_name: 'B' }, NOW);
  assert.equal(lead.free_email, true);
  assert.equal(lead.company, '');
  assert.equal(lead.name, 'A B');
});

test('same email always yields same lead_id (idempotent)', () => {
  const a = L.normalizeLead({ email: 'a@b.co' }, NOW).lead.lead_id;
  const b = L.normalizeLead({ email: 'A@B.co' }, '2030-01-01').lead.lead_id;
  assert.equal(a, b);
});

test('buildPrompt caps tokens, asks for JSON and embeds the lead', () => {
  const p = L.buildPrompt({ name: 'X', email: 'x@y.z' }, { model: 'm', maxTokens: 500 });
  assert.equal(p.max_tokens, 500);
  assert.equal(p.response_format.type, 'json_object');
  assert.match(p.messages[1].content, /<lead>/);
  assert.match(L.buildPrompt({}, { strict: true }).messages[1].content, /STRICT MODE/);
});

test('extractJson handles fenced and chatty output', () => {
  assert.deepEqual(L.extractJson('```json\n{"a":1}\n```'), { a: 1 });
  assert.deepEqual(L.extractJson('Sure! {"a":2} hope it helps'), { a: 2 });
  assert.equal(L.extractJson('no json here'), null);
  assert.equal(L.extractJson(null), null);
});

test('validateQualification accepts valid output and derives tier from score', () => {
  const v = L.validateQualification({ ...good, score: 65, tier: 'Hot' });
  assert.equal(v.valid, true);
  assert.equal(v.data.tier, 'Warm');
});

test('validateQualification rejects schema violations', () => {
  for (const bad of [{ ...good, score: 101 }, { ...good, score: 'high' }, { ...good, tier: 'Lukewarm' }, { ...good, reason: '' }, { ...good, suggested_reply: 5 }, [], null]) {
    assert.equal(L.validateQualification(bad).valid, false, JSON.stringify(bad));
  }
});

test('tierFromScore honours thresholds', () => {
  assert.equal(L.tierFromScore(70), 'Hot');
  assert.equal(L.tierFromScore(69), 'Warm');
  assert.equal(L.tierFromScore(40), 'Warm');
  assert.equal(L.tierFromScore(39), 'Cold');
  assert.equal(L.tierFromScore(60, 60), 'Hot');
});

test('parseLlmResponse reads OpenAI-style responses incl. usage', () => {
  const r = L.parseLlmResponse({ model: 'gpt', choices: [{ message: { content: JSON.stringify(good) } }], usage: { prompt_tokens: 100, completion_tokens: 50 } });
  assert.equal(r.valid, true);
  assert.deepEqual(r.usage, { prompt_tokens: 100, completion_tokens: 50 });
  assert.equal(L.parseLlmResponse({ choices: [{ message: { content: 'oops' } }] }).valid, false);
});

test('fallbackQualification routes to Needs review', () => {
  const f = L.fallbackQualification(['score must be an integer 0-100']);
  assert.equal(f.tier, 'Needs review');
  assert.match(f.reason, /invalid/);
});

test('computeCost sums attempts and updateCostStats keeps a running average', () => {
  const c = L.computeCost([{ prompt_tokens: 1000, completion_tokens: 500 }, { prompt_tokens: 1000, completion_tokens: 0 }], 0.6, 2.5);
  assert.equal(c.cost_usd, 0.002450);
  let s = L.updateCostStats({}, 0.002);
  s = L.updateCostStats(s, 0.004);
  assert.equal(s.leads, 2);
  assert.equal(s.avg_cost_usd, 0.003);
});

test('CRM adapters produce upsert bodies keyed by email', () => {
  const { lead } = L.normalizeLead({ name: 'Ann Lee', email: 'ann@acme.io' }, NOW);
  const rec = L.buildCrmRecord(lead, L.validateQualification(good).data, { model: 'm', attempts: 1, cost: { prompt_tokens: 1, completion_tokens: 1, cost_usd: 0.1 }, stats: { avg_cost_usd: 0.1 }, now: NOW });
  assert.equal(rec.status, 'Contact now');
  const hs = L.hubspotUpsertBody(rec);
  assert.equal(hs.inputs[0].idProperty, 'email');
  assert.equal(hs.inputs[0].properties.firstname, 'Ann');
  assert.equal(hs.inputs[0].properties.ai_score, 82);
  const at = L.airtableUpsertBody(rec);
  assert.deepEqual(at.performUpsert.fieldsToMergeOn, ['Email']);
  assert.equal(at.records[0].fields['Score'], 82);
  const sh = L.sheetsUpsertBody(rec, 'tok');
  assert.equal(sh.key, 'Email');
  assert.equal(sh.token, 'tok');
});

test('parseCrmResult understands HubSpot, Airtable and Sheets responses', () => {
  assert.deepEqual(L.parseCrmResult('hubspot', { results: [{ id: '7', new: true }] }), { crm: 'hubspot', crm_record_id: '7', action: 'created' });
  assert.equal(L.parseCrmResult('airtable', { records: [{ id: 'rec1' }], createdRecords: [], updatedRecords: ['rec1'] }).action, 'updated');
  assert.deepEqual(L.parseCrmResult('sheets', { action: 'created', row: 3 }), { crm: 'sheets', crm_record_id: 'row-3', action: 'created' });
});

test('telegram and email alerts contain name, company, score, reason and suggested reply (HTML-escaped)', () => {
  const r = { name: 'Bob <script>', company: 'Acme', email: 'b@acme.io', score: 91, reason: 'Big budget', suggested_reply: 'Hello Bob', budget: '$9k', timeline: 'now' };
  const t = L.telegramAlert(r, '-100');
  assert.equal(t.parse_mode, 'HTML');
  for (const s of ['Bob &lt;script&gt;', 'Acme', '91/100', 'Big budget', 'Hello Bob']) assert.ok(t.text.includes(s), s);
  const e = L.emailAlert(r, 'a@x', 's@x');
  assert.match(e.subject, /score 91/);
  assert.ok(e.html.includes('Hello Bob'));
});

test('formatErrorAlert extracts failed node and message from Error Trigger payload', () => {
  const { record, text } = L.formatErrorAlert({ workflow: { name: 'WF' }, execution: { id: 12, lastNodeExecuted: 'HubSpot', error: { message: '401 Unauthorized' }, url: 'http://x' } }, NOW);
  assert.equal(record.failed_node, 'HubSpot');
  assert.equal(record.error, '401 Unauthorized');
  assert.match(text, /Workflow failed/);
});
