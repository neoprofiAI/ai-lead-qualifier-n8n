"""Deterministic offline stand-in for an OpenAI-compatible /chat/completions endpoint.

It scores leads with keyword heuristics that mirror the rubric in the real prompt, so the
whole n8n workflow can be demoed and tested without any API key. Swap LLM_BASE_URL to a real
provider (OpenAI, Anthropic's OpenAI-compatible endpoint, Bedrock, OpenRouter...) for real AI.
"""
from __future__ import annotations

import json
import re
import time
import uuid

SPAM = ["backlink", "seo service", "guest post", "crypto", "casino", "bitcoin", "rank your website",
        "first page of google", "forex", "loan offer", "click here"]
JOB = ["looking for a job", "hire me", "my cv", "my resume", "internship"]
NEED = ["automat", "n8n", "crm", "hubspot", "pipedrive", "airtable", "lead", "chatbot", "support bot",
        "ai agent", "workflow", "integration", "zapier", "make.com", "telegram bot", "whatsapp", "rag",
        "автоматиз", "бот", "заявк"]
URGENT = ["asap", "urgent", "this week", "next week", "2 weeks", "two weeks", "within a month",
          "this month", "срочно", "на этой неделе", "в течение месяца"]
DECISION = ["ceo", "founder", "co-founder", "owner", "head of", "director", "cto", "coo", "vp ",
            "основатель", "директор", "владелец"]
FREE = ["gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "mail.ru", "yandex.ru", "icloud.com"]


def _budget_usd(text: str) -> int:
    best = 0
    for m in re.finditer(r"\$\s?(\d[\d,\.]*)\s*(k)?|(\d[\d,\.]*)\s*(k)?\s*(usd|\$|dollars)", text, re.I):
        num = m.group(1) or m.group(3)
        k = m.group(2) or m.group(4)
        try:
            v = float(num.replace(",", ""))
        except ValueError:
            continue
        if k:
            v *= 1000
        best = max(best, int(v))
    return best


def qualify(lead: dict) -> dict:
    msg = " ".join(str(lead.get(k, "")) for k in ("message", "budget", "timeline", "company")).lower()
    email = str(lead.get("email", "")).lower()
    domain = email.split("@")[-1] if "@" in email else ""
    russian = bool(re.search(r"[а-яё]", msg))

    if any(s in msg for s in SPAM):
        return dict(score=4, tier="Cold", reason="Unsolicited SEO/marketing offer — spam, no buying intent.",
                    intent="spam", budget="", timeline="", is_spam=True,
                    suggested_reply="No reply needed (spam).")
    if any(s in msg for s in JOB):
        return dict(score=10, tier="Cold", reason="Job seeker, not a buyer — forward to hiring inbox.",
                    intent="job", budget="", timeline="", is_spam=False,
                    suggested_reply="Thanks for your interest! We're not hiring right now, but we'll keep your details on file.")

    score, reasons = 0, []
    if any(s in msg for s in NEED):
        score += 30; reasons.append("clear automation need")
    budget = _budget_usd(msg)
    if budget >= 1000:
        score += 25; reasons.append(f"budget ~${budget:,}")
    elif budget > 0:
        score += 8; reasons.append(f"small budget ~${budget:,}")
    if any(s in msg for s in URGENT):
        score += 20; reasons.append("urgent timeline")
    elif re.search(r"quarter|month|q[1-4]|месяц", msg):
        score += 8; reasons.append("timeline set")
    if domain and domain not in FREE:
        score += 10; reasons.append("company domain")
    if any(s in msg for s in DECISION):
        score += 10; reasons.append("decision maker")
    if len(msg) > 180:
        score += 5
    score = max(0, min(100, score))
    tier = "Hot" if score >= 70 else "Warm" if score >= 40 else "Cold"
    reason = (", ".join(reasons) or "vague request, little detail").capitalize()
    name = str(lead.get("name", "")).split(" ")[0] or "there"
    if russian:
        reply = (f"Здравствуйте, {name}! Спасибо за заявку — задача понятна, мы делали похожие автоматизации. "
                 "Предлагаю короткий созвон на 15 минут, чтобы уточнить детали. Когда вам удобно?")
    elif tier == "Hot":
        reply = (f"Hi {name}, thanks for reaching out! We've built very similar automations and can start this week. "
                 "Could we do a 15-minute call tomorrow to confirm your tools and success criteria? I'll send a 1-page plan right after.")
    elif tier == "Warm":
        reply = (f"Hi {name}, thanks for the details! To suggest the best approach, could you share which tools you use today "
                 "and roughly how many requests you handle per week?")
    else:
        reply = f"Hi {name}, thanks for your message! Could you tell us a bit more about what you'd like to automate?"
    return dict(score=score, tier=tier, reason=reason[:160], intent="buy" if score >= 40 else "research",
                budget=f"${budget:,}" if budget else str(lead.get("budget", "")), timeline=str(lead.get("timeline", "")),
                is_spam=False, suggested_reply=reply)


def chat_completion(body: dict) -> dict:
    msgs = body.get("messages", [])
    user = next((m.get("content", "") for m in reversed(msgs) if m.get("role") == "user"), "")
    strict = "STRICT MODE" in user
    m = re.search(r"<lead>\s*(\{.*\})\s*</lead>", user, re.S)
    lead = json.loads(m.group(1)) if m else {}
    text = json.dumps(lead).lower()
    if "[simulate-llm-garbage]" in text or ("[simulate-invalid-json]" in text and not strict):
        content = "Sure! This lead looks promising, I would say score: high, tier hot."  # not JSON -> exercises retry/fallback
    else:
        content = json.dumps(qualify(lead), ensure_ascii=False)
    prompt_chars = sum(len(str(m.get("content", ""))) for m in msgs)
    return {
        "id": "chatcmpl-mock-" + uuid.uuid4().hex[:12],
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "mock-llm-offline",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": prompt_chars // 4, "completion_tokens": len(content) // 4,
                  "total_tokens": prompt_chars // 4 + len(content) // 4},
    }
