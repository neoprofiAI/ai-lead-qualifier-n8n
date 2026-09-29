"""End-to-end tests against a running n8n + mock services (skipped if they are not running).

Start them with scripts/start_mock.sh and scripts/start_n8n.sh (offline .env), then: pytest tests/test_e2e_n8n.py
"""
import json
import os
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
N8N = os.environ.get("N8N_WEBHOOK_URL", "http://127.0.0.1:5678/webhook/lead")
MOCK = os.environ.get("MOCK_URL", "http://127.0.0.1:8787")
TARGET = os.environ.get("CRM_TARGET", "hubspot")
LEADS = [{k: v for k, v in l.items()} for l in json.loads((ROOT / "test_leads.json").read_text())]


def _up():
    try:
        httpx.get(MOCK + "/api/state", timeout=2)
        httpx.get(N8N.split("/webhook")[0] + "/healthz", timeout=2)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _up(), reason="n8n and mock services are not running")


def post(lead):
    body = {k: v for k, v in lead.items() if k != "expect_tier"}
    return httpx.post(N8N, json=body, timeout=120)


def crm_count(state):
    return {"hubspot": len(state["hubspot"]), "airtable": sum(len(v) for v in state["airtable"].values()),
            "sheets": len(state["sheets"]["Leads"])}[TARGET]


@pytest.fixture(autouse=True)
def reset():
    httpx.post(MOCK + "/admin/reset")
    yield
    httpx.post(MOCK + "/admin/fail/hubspot/0"); httpx.post(MOCK + "/admin/fail/airtable/0"); httpx.post(MOCK + "/admin/fail/sheets/0")


def test_all_sample_leads_qualified_within_30s_and_deduplicated():
    for lead in LEADS:
        t0 = time.time()
        r = post(lead)
        assert r.status_code == 200, r.text
        data = r.json()
        assert time.time() - t0 < 30
        assert data["tier"] == lead["expect_tier"], (lead["email"], data)
        assert data["crm"] == TARGET
    state = httpx.get(MOCK + "/api/state").json()
    assert crm_count(state) == len({l["email"] for l in LEADS})
    # replaying the same lead updates, never duplicates
    again = post(LEADS[0]).json()
    assert again["crm_action"] == "updated"
    assert crm_count(httpx.get(MOCK + "/api/state").json()) == len(LEADS)


def test_hot_leads_alert_on_telegram_and_email_only():
    hot = [l for l in LEADS if l["expect_tier"] == "Hot"]
    for l in LEADS:
        post(l)
    state = httpx.get(MOCK + "/api/state").json()
    assert len(state["telegram"]) == len(hot)
    assert len(state["emails"]) == len(hot)
    msg = state["telegram"][0]["text"]
    for part in ("Maria Lopez", "BrightPath Realty", "/100", "Suggested reply"):
        assert part in msg


def test_invalid_llm_json_is_retried_once():
    lead = next(l for l in LEADS if "[simulate-invalid-json]" in l["message"])
    data = post(lead).json()
    assert data["ai_attempts"] == 2 and data["tier"] == "Hot"


def test_invalid_twice_falls_back_to_needs_review():
    data = post({"name": "Broken", "email": "broken@example.org", "message": "[simulate-llm-garbage] test"}).json()
    assert data["tier"] == "Needs review" and data["alerted"] is False


def test_cost_is_logged_per_lead():
    data = post(LEADS[0]).json()
    assert data["ai_cost_usd"] > 0 and data["avg_ai_cost_per_lead_usd"] > 0


def test_crm_failure_triggers_error_workflow():
    httpx.post(MOCK + f"/admin/fail/{TARGET}/1")
    r = post(LEADS[1])
    assert r.status_code >= 400
    for _ in range(30):
        state = httpx.get(MOCK + "/api/state").json()
        if any(m["text"].startswith("🚨") for m in state["telegram"]):
            break
        time.sleep(1)
    assert any(m["text"].startswith("🚨") for m in state["telegram"]), "no Telegram error alert"
    if TARGET != "sheets":  # with the Sheets outage the Errors sheet itself is unreachable — Telegram still alerts
        assert state["sheets"]["Errors"], "error workflow did not log"
