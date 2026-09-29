/**
 * Google Sheets "CRM" endpoint for the AI Lead Qualifier (deploy as a Web App).
 *
 * 1. Create a Google Sheet with tabs "Leads" and "Errors" (headers are created automatically).
 * 2. Extensions → Apps Script → paste this file → set SHARED_TOKEN in Project Settings → Script properties.
 * 3. Deploy → New deployment → Web app → Execute as: Me, Who has access: Anyone → copy the URL
 *    into SHEETS_WEBHOOK_URL (and the token into SHEETS_WEBHOOK_TOKEN).
 *
 * Actions: {action:"upsert", sheet:"Leads", key:"Email", record:{...}}  → dedup by key column
 *          {action:"log_error", sheet:"Errors", record:{...}}
 */
function doPost(e) {
  const body = JSON.parse(e.postData.contents || '{}');
  const token = PropertiesService.getScriptProperties().getProperty('SHARED_TOKEN');
  if (token && body.token !== token) return json_({ ok: false, error: 'Unauthorized' });

  const lock = LockService.getScriptLock();
  lock.waitLock(20000); // serialize writes so concurrent leads can't create duplicates
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const sheet = ss.getSheetByName(body.sheet || 'Leads') || ss.insertSheet(body.sheet || 'Leads');
    const record = body.record || {};
    const headers = ensureHeaders_(sheet, Object.keys(record));
    const row = headers.map(function (h) { return record[h] !== undefined ? record[h] : ''; });

    if (body.action === 'log_error') {
      sheet.appendRow(row);
      return json_({ ok: true, action: 'logged', row: sheet.getLastRow() });
    }
    if (body.action === 'upsert') {
      const keyCol = headers.indexOf(body.key || 'Email');
      const last = sheet.getLastRow();
      if (keyCol >= 0 && last > 1) {
        const keys = sheet.getRange(2, keyCol + 1, last - 1, 1).getValues();
        const needle = String(record[body.key || 'Email'] || '').toLowerCase();
        for (var i = 0; i < keys.length; i++) {
          if (String(keys[i][0]).toLowerCase() === needle) {
            sheet.getRange(i + 2, 1, 1, row.length).setValues([row]);
            return json_({ ok: true, action: 'updated', row: i + 2 });
          }
        }
      }
      sheet.appendRow(row);
      return json_({ ok: true, action: 'created', row: sheet.getLastRow() });
    }
    return json_({ ok: false, error: 'unknown action' });
  } finally {
    lock.releaseLock();
  }
}

function ensureHeaders_(sheet, keys) {
  const lastCol = Math.max(sheet.getLastColumn(), 1);
  var headers = sheet.getLastRow() ? sheet.getRange(1, 1, 1, lastCol).getValues()[0].filter(String) : [];
  const missing = keys.filter(function (k) { return headers.indexOf(k) < 0; });
  if (missing.length) {
    headers = headers.concat(missing);
    sheet.getRange(1, 1, 1, headers.length).setValues([headers]).setFontWeight('bold');
    sheet.setFrozenRows(1);
  }
  return headers;
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
