"""Screenshot + accessibility tree -> Gemini -> DataForSEO traffic."""

import base64
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx
from playwright.sync_api import sync_playwright

from src.models import QualificationResult

ANALYSIS_PROMPT = """You are qualifying B2B SaaS company websites for sales outreach.

You receive: (1) a screenshot and (2) accessibility tree listing links and buttons.
Return JSON only:
{
  "pricing_mentioned": true/false,
  "sign_up_mentioned": true/false,
  "free_trial_mentioned": true/false,
  "book_demo_button": true/false,
  "talk_to_sales_button": true/false,
  "bot_detected": true/false
}

Use the accessibility tree first for button/link labels, then confirm with the screenshot.
Mark true if ANY matching label appears in the tree or screenshot.

- pricing_mentioned: Pricing, Plans, Plan, View pricing, See plans, Plans & pricing, dollar amounts, /month, per user, per seat, starting at $
- sign_up_mentioned: Sign up, Sign Up, Sign in, Sign In, Log in, Login, Get started, Get Started, Start for free, Start free, Create account, Create Account, Register, Join now, Start now, Try Attio, Open app, Go to app
- free_trial_mentioned: Free trial, Start free trial, Try free, Try for free, 14-day trial, 30-day trial, Start your trial, Free plan, Try it free
- book_demo_button: Book demo, Book a demo, Schedule demo, Schedule a demo, Request demo, Get a demo, See a demo, Let's talk, Let us talk, Talk to us, Request a demo, Book meeting, Schedule a call, Get a walkthrough, Watch demo
- talk_to_sales_button: Talk to sales, Talk to Sales, Contact sales, Contact Sales, Speak to sales, Speak to Sales, Sales team, Contact sales team, Enterprise sales, Book sales call
- bot_detected: true ONLY if a live chat widget or chat bubble is visible (Intercom, Drift, HubSpot messenger, Zendesk chat, Crisp, Tawk, "Chat with us", "Open chat", floating chat bubble). false for cookie banners, privacy notices, CAPTCHA pages, and pages with no chat widget.

If the page is a CAPTCHA/block screen (not the real site), set all fields to false."""

BLOCK_VISIBLE_PATTERNS = [
    "verify you are human",
    "checking your browser",
    "access denied",
    "unusual traffic",
    "please complete the security check",
]

CTA_PATTERNS: dict[str, list[str]] = {
    "pricing_mentioned": [
        "pricing", "plans", "plan & pricing", "view pricing", "see plans", "/pricing",
        "per month", "per user", "per seat", "starting at $", "/month",
    ],
    "sign_up_mentioned": [
        "sign up", "sign in", "sign-in", "log in", "login", "get started", "start for free",
        "start free", "create account", "register", "join now", "start now", "open app",
        "go to app", "try attio", "welcome/sign-in",
    ],
    "free_trial_mentioned": [
        "free trial", "start free trial", "try free", "try for free", "14-day trial",
        "30-day trial", "start your trial", "free plan", "try it free",
    ],
    "book_demo_button": [
        "book demo", "book a demo", "schedule demo", "schedule a demo", "request demo",
        "get a demo", "see a demo", "let's talk", "let us talk", "talk to us",
        "request a demo", "book meeting", "schedule a call", "get a walkthrough", "watch demo",
    ],
    "talk_to_sales_button": [
        "talk to sales", "contact sales", "speak to sales", "sales team",
        "contact sales team", "enterprise sales", "book sales call",
    ],
}

CHAT_A11Y_PATTERNS = [
    "chat with us", "open chat", "live chat", "start chat", "chat now", "message us",
    "intercom", "drift", "zendesk", "crisp", "tawk",
]

TRAFFIC_API = "https://api.dataforseo.com/v3/dataforseo_labs/google/bulk_traffic_estimation/live"


def normalize_url(url: str) -> str:
    url = url.strip()
    return url if url.startswith("http") else f"https://{url}"


def domain_from_url(url: str) -> str:
    netloc = urlparse(normalize_url(url)).netloc
    return netloc.removeprefix("www.")


def _capture_a11y(page) -> str:
    return page.locator("body").aria_snapshot()


def _detect_from_a11y(a11y: str) -> dict[str, bool]:
    text = a11y.lower()
    out = {key: any(p in text for p in patterns) for key, patterns in CTA_PATTERNS.items()}
    out["bot_detected"] = any(p in text for p in CHAT_A11Y_PATTERNS)
    return out


def _merge_bool(gemini_val: bool, a11y_val: bool) -> bool:
    return bool(gemini_val or a11y_val)


def capture(url: str) -> dict:
    url = normalize_url(url)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        response = page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(5000)
        a11y_interactive = _capture_a11y(page)
        screenshot_bytes = page.screenshot(full_page=True, type="png")
        title = page.title()
        final_url = page.url
        status = response.status if response else None
        browser.close()

    return {
        "url": url,
        "final_url": final_url,
        "screenshot_bytes": screenshot_bytes,
        "title": title,
        "status": status,
        "a11y_interactive": a11y_interactive,
    }


def is_blocked(page: dict) -> bool:
    haystack = f"{page.get('title', '')}\n{page.get('a11y_interactive', '')}".lower()
    if any(p in haystack for p in BLOCK_VISIBLE_PATTERNS):
        return True
    title = page.get("title", "").lower()
    if any(p in title for p in ("just a moment", "access denied", "attention required")):
        return True
    return page.get("status") in {403, 429, 503}


def _page_context(page: dict) -> str:
    a11y = page.get("a11y_interactive", "")
    return (
        f"URL: {page['url']}\n"
        f"Final URL: {page.get('final_url', page['url'])}\n"
        f"Title: {page.get('title', '')}\n\n"
        f"Accessibility tree (links & buttons):\n{a11y[:8000]}"
    )


def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    return json.loads(raw)


def _analyze_gemini(page: dict) -> dict:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    response = client.models.generate_content(
        model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        contents=[
            ANALYSIS_PROMPT,
            _page_context(page),
            types.Part.from_bytes(data=page["screenshot_bytes"], mime_type="image/png"),
        ],
        config=types.GenerateContentConfig(temperature=0.1, response_mime_type="application/json"),
    )
    return _parse_json(response.text or "")


def analyze_page(page: dict) -> dict:
    if not os.getenv("GEMINI_API_KEY"):
        raise RuntimeError("GEMINI_API_KEY is required — set it in .env")
    return _analyze_gemini(page)


def fetch_traffic(domains: list[str]) -> tuple[dict[str, int], str | None]:
    login = os.getenv("DATAFORSEO_LOGIN")
    password = os.getenv("DATAFORSEO_PASSWORD")
    if not login or not password:
        return {}, "DATAFORSEO_LOGIN/PASSWORD not set in .env"
    if not domains:
        return {}, None

    auth = base64.b64encode(f"{login}:{password}".encode()).decode()
    payload = [{
        "targets": list(dict.fromkeys(domains))[:1000],
        "location_code": 2840,
        "language_code": "en",
        "item_types": ["organic", "paid"],
    }]

    r = httpx.post(
        TRAFFIC_API,
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    data = r.json() if r.content else {}
    if r.status_code >= 400:
        msg = data.get("status_message") or r.text[:200]
        return {}, f"DataForSEO HTTP {r.status_code}: {msg}"

    out: dict[str, int] = {}
    items = data.get("tasks", [{}])[0].get("result", [{}])[0].get("items", [])
    for item in items:
        target = item.get("target", "")
        metrics = item.get("metrics", {})
        organic = metrics.get("organic", {}) or {}
        paid = metrics.get("paid", {}) or {}
        out[target] = int((organic.get("etv") or 0) + (paid.get("etv") or 0))
    return out, None


def qualify_one(page: dict, traffic: int | None) -> QualificationResult:
    try:
        g = analyze_page(page)
        a11y = _detect_from_a11y(page.get("a11y_interactive", ""))
        blocked = is_blocked(page)

        def field(key: str) -> bool:
            if blocked:
                return False
            return _merge_bool(bool(g.get(key)), a11y.get(key, False))

        return QualificationResult(
            url=page["url"],
            pricing_mentioned=field("pricing_mentioned"),
            sign_up_mentioned=field("sign_up_mentioned"),
            free_trial_mentioned=field("free_trial_mentioned"),
            book_demo_button=field("book_demo_button"),
            talk_to_sales_button=field("talk_to_sales_button"),
            monthly_traffic=traffic,
            bot_detected=bool(g.get("bot_detected")) if not blocked else False,
        )
    except Exception:
        return QualificationResult(url=page["url"])


def qualify_urls(urls: list[str], *, skip_traffic: bool = False) -> list[QualificationResult]:
    results: list[QualificationResult] = []
    captured_pages: list[dict] = []

    for u in urls:
        url = normalize_url(u)
        try:
            captured_pages.append(capture(url))
        except Exception:
            results.append(QualificationResult(url=url))

    traffic_map: dict[str, int] = {}
    if not skip_traffic and captured_pages:
        traffic_map, traffic_error = fetch_traffic([domain_from_url(p["url"]) for p in captured_pages])
        if traffic_error:
            print(f"Traffic lookup failed: {traffic_error}", file=sys.stderr)

    for page in captured_pages:
        domain = domain_from_url(page["url"])
        results.append(qualify_one(page, traffic_map.get(domain)))

    return results
