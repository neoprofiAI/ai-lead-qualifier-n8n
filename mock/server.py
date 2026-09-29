"""Local mock of every external service the workflow talks to.

Implements the *real* request/response shapes of:
  * OpenAI-compatible LLM   POST /llm/v1/chat/completions
  * HubSpot CRM             POST /hubspot/crm/v3/objects/contacts/batch/upsert
  * Airtable                PATCH /airtable/v0/{base}/{table}   (performUpsert)
  * Google Sheets (Apps Script web app, see apps-script/Code.gs)  POST /sheets/exec
  * Telegram Bot API        POST /telegram/bot{token}/sendMessage
  * Resend-style email API  POST /email/emails
plus a live dashboard (/), a demo lead form (/form) and admin endpoints to simulate outages.

Run:  uvicorn mock.server:app --port 8787
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from mock.mock_llm import chat_completion

ROOT = Path(__file__).resolve().parent
N8N_WEBHOOK_URL = os.environ.get("N8N_WEBHOOK_URL", "http://localhost:5678/webhook/lead")

app = FastAPI(title="Lead Qualifier — mock services")
_lock = threading.Lock()


def _empty_state() -> dict:
    return {"hubspot": {}, "airtable": {}, "sheets": {"Leads": [], "Errors": []}, "telegram": [], "emails": [],
            "llm_calls": 0, "fail": {}}


STATE = _empty_state()


def _check_fail(service: str):
    if STATE["fail"].get(service):
        msgs = {
            "hubspot": {"status": "error", "category": "INVALID_AUTHENTICATION",
                        "message": "Authentication credentials not found. The private app token is invalid or revoked."},
            "airtable": {"error": {"type": "AUTHENTICATION_REQUIRED", "message": "Invalid personal access token"}},
            "llm": {"error": {"message": "Incorrect API key provided", "type": "invalid_request_error", "code": "invalid_api_key"}},
            "sheets": {"ok": False, "error": "Unauthorized: bad token"},
        }
        raise HTTPException(status_code=401, detail=msgs.get(service, {"error": "unauthorized"}))


def _bearer(req: Request) -> str:
    auth = req.headers.get("authorization", "")
    if not auth.lower().startswith("bearer ") or len(auth.strip()) <= 7:
        raise HTTPException(status_code=401, detail={"error": "missing bearer token"})
    return auth[7:]


# ---------------- LLM ----------------
@app.post("/llm/v1/chat/completions")
async def llm(req: Request):
    _bearer(req)
    _check_fail("llm")
    body = await req.json()
    with _lock:
        STATE["llm_calls"] += 1
    return chat_completion(body)


# ---------------- HubSpot ----------------
@app.post("/hubspot/crm/v3/objects/contacts/batch/upsert")
async def hubspot_upsert(req: Request):
    _bearer(req)
    _check_fail("hubspot")
    body = await req.json()
    results = []
    with _lock:
        for inp in body.get("inputs", []):
            if inp.get("idProperty") != "email" or not inp.get("id"):
                raise HTTPException(400, {"status": "error", "message": "idProperty=email and id required"})
            key = inp["id"].lower()
            existing = STATE["hubspot"].get(key)
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            if existing:
                existing["properties"].update(inp.get("properties", {}))
                existing["updatedAt"] = now
                results.append({"id": existing["id"], "properties": existing["properties"], "new": False, "updatedAt": now})
            else:
                rec = {"id": str(1000 + len(STATE["hubspot"]) + 1), "properties": dict(inp.get("properties", {})),
                       "createdAt": now, "updatedAt": now}
                STATE["hubspot"][key] = rec
                results.append({"id": rec["id"], "properties": rec["properties"], "new": True, "createdAt": now})
    return {"status": "COMPLETE", "results": results}


# ---------------- Airtable ----------------
@app.patch("/airtable/v0/{base_id}/{table}")
async def airtable_upsert(base_id: str, table: str, req: Request):
    _bearer(req)
    _check_fail("airtable")
    body = await req.json()
    merge_on = body.get("performUpsert", {}).get("fieldsToMergeOn") or []
    if not merge_on:
        raise HTTPException(422, {"error": {"type": "INVALID_REQUEST", "message": "performUpsert required in this mock"}})
    created, updated, out = [], [], []
    with _lock:
        tbl = STATE["airtable"].setdefault(f"{base_id}/{table}", [])
        for r in body.get("records", []):
            f = r.get("fields", {})
            key = tuple(str(f.get(k, "")).lower() for k in merge_on)
            match = next((x for x in tbl if tuple(str(x["fields"].get(k, "")).lower() for k in merge_on) == key), None)
            if match:
                match["fields"].update(f)
                updated.append(match["id"]); out.append(match)
            else:
                rec = {"id": "rec" + uuid.uuid4().hex[:14], "createdTime": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
                       "fields": dict(f)}
                tbl.append(rec); created.append(rec["id"]); out.append(rec)
    return {"records": out, "createdRecords": created, "updatedRecords": updated}


# ---------------- Google Sheets (Apps Script web app) ----------------
@app.post("/sheets/exec")
async def sheets_exec(req: Request):
    _check_fail("sheets")
    body = await req.json()
    expected = os.environ.get("SHEETS_WEBHOOK_TOKEN", "")
    if expected and body.get("token") != expected:
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    action = body.get("action")
    sheet = body.get("sheet", "Leads")
    with _lock:
        rows = STATE["sheets"].setdefault(sheet, [])
        if action == "upsert":
            key = body.get("key", "Email")
            rec = body.get("record", {})
            for i, row in enumerate(rows):
                if str(row.get(key, "")).lower() == str(rec.get(key, "")).lower():
                    row.update(rec)
                    return {"ok": True, "action": "updated", "row": i + 2}
            rows.append(dict(rec))
            return {"ok": True, "action": "created", "row": len(rows) + 1}
        if action == "log_error":
            rows.append(dict(body.get("record", {})))
            return {"ok": True, "action": "logged", "row": len(rows) + 1}
    return JSONResponse({"ok": False, "error": f"unknown action {action}"}, status_code=400)


# ---------------- Telegram ----------------
@app.post("/telegram/bot{token}/sendMessage")
async def telegram_send(token: str, req: Request):
    if not token or token == "undefined":
        return JSONResponse({"ok": False, "error_code": 401, "description": "Unauthorized"}, status_code=401)
    body = await req.json()
    with _lock:
        msg = {"message_id": len(STATE["telegram"]) + 1, "date": int(time.time()), "chat": {"id": body.get("chat_id")},
               "text": body.get("text", ""), "parse_mode": body.get("parse_mode")}
        STATE["telegram"].append(msg)
    return {"ok": True, "result": msg}


# ---------------- Email ----------------
@app.post("/email/emails")
async def email_send(req: Request):
    _bearer(req)
    body = await req.json()
    with _lock:
        eid = uuid.uuid4().hex
        STATE["emails"].append({"id": eid, "time": int(time.time()), **body})
    return {"id": eid}


# ---------------- Admin / dashboard ----------------
@app.get("/api/state")
def state():
    return STATE


@app.post("/admin/reset")
def reset():
    with _lock:
        STATE.clear(); STATE.update(_empty_state())
    return {"ok": True}


@app.post("/admin/fail/{service}/{enabled}")
def set_fail(service: str, enabled: int):
    with _lock:
        STATE["fail"][service] = bool(enabled)
    return {"ok": True, "fail": STATE["fail"]}


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return (ROOT / "dashboard.html").read_text()


@app.get("/form", response_class=HTMLResponse)
def form():
    return (ROOT / "form.html").read_text()


@app.post("/form/submit")
async def form_submit(req: Request):
    """Form backend: forwards the submission to the n8n webhook (what Tally/Typeform do)."""
    body = await req.json()
    async with httpx.AsyncClient(timeout=90) as c:
        r = await c.post(N8N_WEBHOOK_URL, json=body)
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text}
    return JSONResponse(data, status_code=r.status_code)
