"""Tests for the mock services, the offline scorer and the generated workflow JSON."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mock import server
from mock.mock_llm import chat_completion, qualify

ROOT = Path(__file__).resolve().parents[1]
LEADS = json.loads((ROOT / "test_leads.json").read_text())
H = {"Authorization": "Bearer t"}


@pytest.fixture()
def client():
    c = TestClient(server.app)
    c.post("/admin/reset")
    return c


@pytest.mark.parametrize("lead", LEADS, ids=[l["email"] for l in LEADS])
def test_offline_scorer_matches_expected_tier(lead):
    assert qualify(lead)["tier"] == lead["expect_tier"]


def test_mock_llm_invalid_then_valid_on_strict_retry():
    lead = {"message": "[simulate-invalid-json] need n8n automation"}
    msg = lambda strict: {"messages": [{"role": "user", "content": ("STRICT MODE\n" if strict else "") + "<lead>\n" + json.dumps(lead) + "\n</lead>"}]}
    first = chat_completion(msg(False))["choices"][0]["message"]["content"]
    with pytest.raises(json.JSONDecodeError):
        json.loads(first)
    assert "score" in json.loads(chat_completion(msg(True))["choices"][0]["message"]["content"])


def test_llm_requires_bearer(client):
    assert client.post("/llm/v1/chat/completions", json={"messages": []}).status_code == 401


def test_hubspot_upsert_dedups_by_email(client):
    body = {"inputs": [{"idProperty": "email", "id": "A@x.io", "properties": {"email": "a@x.io", "ai_score": 10}}]}
    r1 = client.post("/hubspot/crm/v3/objects/contacts/batch/upsert", json=body, headers=H).json()
    body["inputs"][0]["id"] = "a@x.io"; body["inputs"][0]["properties"]["ai_score"] = 90
    r2 = client.post("/hubspot/crm/v3/objects/contacts/batch/upsert", json=body, headers=H).json()
    assert r1["results"][0]["new"] is True and r2["results"][0]["new"] is False
    assert r1["results"][0]["id"] == r2["results"][0]["id"]
    st = client.get("/api/state").json()
    assert len(st["hubspot"]) == 1 and st["hubspot"]["a@x.io"]["properties"]["ai_score"] == 90


def test_airtable_perform_upsert(client):
    body = {"performUpsert": {"fieldsToMergeOn": ["Email"]}, "records": [{"fields": {"Email": "b@x.io", "Score": 1}}]}
    r1 = client.patch("/airtable/v0/app1/Leads", json=body, headers=H).json()
    r2 = client.patch("/airtable/v0/app1/Leads", json=body, headers=H).json()
    assert r1["createdRecords"] and r2["updatedRecords"] == r1["createdRecords"]


def test_sheets_upsert_and_error_log(client):
    rec = {"Email": "c@x.io", "Score": 5}
    assert client.post("/sheets/exec", json={"action": "upsert", "record": rec}).json()["action"] == "created"
    assert client.post("/sheets/exec", json={"action": "upsert", "record": rec}).json()["action"] == "updated"
    assert client.post("/sheets/exec", json={"action": "log_error", "sheet": "Errors", "record": {"error": "x"}}).json()["ok"]


def test_outage_simulation_returns_401(client):
    client.post("/admin/fail/hubspot/1")
    r = client.post("/hubspot/crm/v3/objects/contacts/batch/upsert", json={"inputs": []}, headers=H)
    assert r.status_code == 401


def test_telegram_and_email_capture(client):
    assert client.post("/telegram/bot1:abc/sendMessage", json={"chat_id": 1, "text": "hi"}).json()["ok"]
    assert "id" in client.post("/email/emails", json={"subject": "s"}, headers=H).json()
    st = client.get("/api/state").json()
    assert len(st["telegram"]) == 1 and len(st["emails"]) == 1


def test_generated_workflows_are_in_sync_and_well_formed(tmp_path):
    before = {p.name: p.read_text() for p in (ROOT / "workflows").glob("*.json")}
    subprocess.run([sys.executable, str(ROOT / "scripts" / "build_workflows.py")], check=True, capture_output=True)
    after = {p.name: p.read_text() for p in (ROOT / "workflows").glob("*.json")}
    assert before == after, "workflows/ is stale — run scripts/build_workflows.py"
    main = json.loads(after["lead-qualifier-main.json"])
    names = {n["name"] for n in main["nodes"]}
    for src, c in main["connections"].items():
        assert src in names
        for outs in c["main"]:
            for o in outs:
                assert o["node"] in names
    assert main["settings"]["errorWorkflow"] == "LeadQualifierErr001"
    http = [n for n in main["nodes"] if n["type"] == "n8n-nodes-base.httpRequest"]
    assert all(n.get("retryOnFail") for n in http)
    assert not any("sk-" in json.dumps(n) for n in main["nodes"]), "no secrets in workflow JSON"
