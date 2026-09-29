#!/usr/bin/env python3
"""Replay test_leads.json against the n8n webhook and print a results table.

Usage: python scripts/replay_leads.py [--url http://localhost:5678/webhook/lead] [--only 1,3] [--json out.json]
"""
import argparse
import json
import sys
import time
from pathlib import Path

import httpx
from rich.console import Console
from rich import box
from rich.table import Table

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:5678/webhook/lead")
    ap.add_argument("--file", default=str(ROOT / "test_leads.json"))
    ap.add_argument("--only", default="")
    ap.add_argument("--json", default="")
    ap.add_argument("--record", default="", help="save an SVG/HTML terminal recording here")
    a = ap.parse_args()
    leads = json.loads(Path(a.file).read_text())
    idx = [int(i) for i in a.only.split(",") if i] or list(range(1, len(leads) + 1))
    con = Console(record=True, width=176, height=60, safe_box=False)
    con.print(f"[bold cyan]▶ Replaying {len(idx)} leads →[/] {a.url}")
    t = Table(title="AI Lead Qualifier — replay results", box=box.ROUNDED, show_lines=True, header_style="bold magenta")
    for c in ("#", "Lead", "Expected", "Tier", "Score", "CRM", "Alert", "AI tries", "Cost $", "Latency", "Reason"):
        t.add_column(c, overflow="fold", max_width=64 if c == "Reason" else None)
    results, ok = [], 0
    with httpx.Client(timeout=120) as c:
        for i in idx:
            lead = {k: v for k, v in leads[i - 1].items() if k != "expect_tier"}
            t0 = time.time()
            try:
                r = c.post(a.url, json=lead)
                data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"error": r.text[:200]}
                if r.status_code >= 400:
                    data = {"error": f"HTTP {r.status_code}: {str(data)[:150]}"}
            except Exception as e:  # noqa: BLE001
                data = {"error": str(e)}
            dt = time.time() - t0
            exp = leads[i - 1].get("expect_tier", "")
            tier = data.get("tier", "ERROR")
            match = tier == exp
            ok += match
            color = {"Hot": "red", "Warm": "yellow", "Cold": "blue"}.get(tier, "magenta")
            results.append({"n": i, "expected": exp, "latency_s": round(dt, 2), **data})
            t.add_row(str(i), f"{lead['name']}\n[dim]{lead['email']}[/]", exp, f"[bold {color}]{tier}[/]", str(data.get("score", "-")),
                      f"{data.get('crm', '-')}:{data.get('crm_action', '-')}", "🔔" if data.get("alerted") else "—",
                      str(data.get("ai_attempts", "-")), f"{data.get('ai_cost_usd', 0):.5f}" if "ai_cost_usd" in data else "-",
                      f"{dt:.1f}s", data.get("reason", data.get("error", ""))[:140])
    con.print(t)
    avg = [r["ai_cost_usd"] for r in results if "ai_cost_usd" in r]
    con.print(f"[bold green]✔ {ok}/{len(idx)} tiers matched expectations[/]  ·  avg AI cost/lead: "
              f"${(sum(avg) / len(avg) if avg else 0):.5f}  ·  max latency: {max(r['latency_s'] for r in results):.1f}s")
    if a.json:
        Path(a.json).write_text(json.dumps(results, indent=2, ensure_ascii=False))
    if a.record:
        con.save_html(a.record)
    return 0 if all("error" not in r for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
