#!/usr/bin/env python3
"""Scripted demo run that produces the portfolio screenshots in docs/screenshots/.

Prereqs: mock services (scripts/start_mock.sh) and n8n (scripts/start_n8n.sh) running with the offline .env.
  python scripts/demo_screenshots.py
"""
import json
import os
import sys
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
from shots import run_capture, terminal_png  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "screenshots"
N8N = os.environ.get("N8N_URL", "http://localhost:5678")
MOCK = os.environ.get("MOCK_URL", "http://127.0.0.1:8787")
EMAIL = os.environ.get("N8N_DEMO_EMAIL", "demo@example.com")
PASSWORD = os.environ.get("N8N_DEMO_PASSWORD", "DemoPass123!")
PY = sys.executable
LEADS = json.loads((ROOT / "test_leads.json").read_text())


def ensure_owner():
    r = httpx.post(f"{N8N}/rest/owner/setup", json={"email": EMAIL, "firstName": "Demo", "lastName": "User", "password": PASSWORD})
    print("owner setup:", r.status_code)


def login(page):
    page.goto(f"{N8N}/signin")
    page.wait_for_selector("input[type=password]", timeout=30000)
    page.locator("input[type=email], input[name=emailOrLdapLoginId]").first.fill(EMAIL)
    page.fill("input[type=password]", PASSWORD)
    page.keyboard.press("Enter")
    page.wait_for_timeout(3000)


def dismiss(page):
    for sel in ["button:has-text('Got it')", "button:has-text('Skip')", "[aria-label='Close']", "button:has-text('Dismiss')"]:
        try:
            if page.locator(sel).first.is_visible(timeout=500):
                page.locator(sel).first.click()
        except Exception:
            pass


def rest(page, path):
    js = """async (u) => { const r = await fetch(u, {headers: {'browser-id': localStorage.getItem('n8n-browserId') || ''}});
            return await r.json(); }"""
    return page.evaluate(js, path)


def executions(page, limit=50):
    data = rest(page, "/rest/executions?filter=" + json.dumps({"workflowId": "LeadQualifierMain01"}) + f"&limit={limit}")
    d = data.get("data", {})
    return d.get("results", d if isinstance(d, list) else [])


def fit(page):
    page.keyboard.press("Shift+1")  # zoom to fit
    page.wait_for_timeout(800)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    httpx.post(f"{MOCK}/admin/reset")
    ensure_owner()
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context(viewport={"width": 1600, "height": 900}, device_scale_factor=2)
        page = ctx.new_page()

        # 1) the lead form, filled in as a hot lead
        hot = LEADS[0]
        page.goto(f"{MOCK}/form")
        for k in ("name", "email", "company", "phone"):
            page.fill(f"input[name={k}]", hot.get(k, ""))
        page.select_option("select[name=budget]", "$5,000 – $15,000")
        page.select_option("select[name=timeline]", "ASAP / this month")
        page.fill("textarea[name=message]", hot["message"])
        page.set_viewport_size({"width": 900, "height": 900})
        page.screenshot(path=str(OUT / "01-lead-form.png"), full_page=True)
        t0 = time.time()
        form_start = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t0 - 1))
        page.click("button")
        page.wait_for_selector("#res:has-text('Thanks')", timeout=60000)
        print(f"hot lead processed end-to-end in {time.time() - t0:.1f}s")
        form_end = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() + 1))
        page.set_viewport_size({"width": 1600, "height": 900})

        # 2) replay the other 9 sample leads from the terminal
        replay_out = run_capture(f"{PY} scripts/replay_leads.py --only 2,3,4,5,6,7,8,9,10", cwd=ROOT)

        # 3) live dashboard: CRM + Telegram + email
        page.goto(MOCK)
        page.wait_for_timeout(2500)
        page.screenshot(path=str(OUT / "04-crm-and-telegram-alerts.png"))
        page.locator(".tg").screenshot(path=str(OUT / "04b-telegram-alert.png"))

        # 4) n8n canvas + a successful execution
        login(page)
        page.goto(f"{N8N}/workflow/LeadQualifierMain01")
        page.wait_for_timeout(4000)
        dismiss(page)
        fit(page)
        page.screenshot(path=str(OUT / "02-n8n-workflow.png"))
        ok = [e for e in executions(page) if e.get("status") == "success" and form_start <= e["startedAt"][:19] <= form_end]
        target = ok[0] if ok else None  # the hot lead submitted through the form
        if target:
            page.goto(f"{N8N}/workflow/LeadQualifierMain01/executions/{target['id']}")
            page.wait_for_timeout(5000)
            dismiss(page)
            page.screenshot(path=str(OUT / "03-n8n-execution-success.png"))

        # 5) break the CRM key -> error workflow alerts
        httpx.post(f"{MOCK}/admin/fail/hubspot/1")
        httpx.post(f"{N8N}/webhook/lead", json={k: v for k, v in LEADS[3].items() if k != "expect_tier"} | {"email": "new.lead@acme-industries.com", "name": "Chris Doe"}, timeout=60)
        time.sleep(4)
        httpx.post(f"{MOCK}/admin/fail/hubspot/0")
        page.goto(MOCK)
        page.wait_for_timeout(2500)
        page.screenshot(path=str(OUT / "06-error-alert.png"))
        page.goto(f"{N8N}/workflow/LeadQualifierMain01")
        page.wait_for_timeout(2000)
        err = [e for e in executions(page, 5) if e.get("status") == "error"]
        if err:
            page.goto(f"{N8N}/workflow/LeadQualifierMain01/executions/{err[0]['id']}")
            page.wait_for_timeout(5000)
            dismiss(page)
            page.screenshot(path=str(OUT / "07-n8n-execution-error.png"))
        b.close()

    terminal_png("replay_leads.py — 9 sample leads (hot / warm / spam) through the live n8n workflow", replay_out,
                 OUT / "05-replay-terminal.png", width=180)
    # 6) test suites
    node = os.environ.get("NODE_BIN", "node")
    out = run_capture(f"{node} --test tests/*.test.js 2>&1 | tail -9", cwd=ROOT)
    out += run_capture(f"{PY} -m pytest -v --color=yes tests/ 2>&1 | tail -34", cwd=ROOT)
    terminal_png("Tests: 15 JS unit tests (Code-node logic) + Python mock/workflow + live end-to-end n8n tests", out,
                 OUT / "08-tests.png", width=130)
    print("screenshots written to", OUT)


if __name__ == "__main__":
    main()
