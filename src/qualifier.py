"""Screenshot -> Gemini/Groq vision -> DataForSEO traffic."""

import base64
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
from playwright.sync_api import sync_playwright

from src.models import QualificationResult

ANALYSIS_PROMPT = """You are qualifying B2B SaaS company websites for sales outreach.

Use BOTH the screenshot and the page text provided. Return JSON only:
{
  "pricing_mentioned": true/false,
  "sign_up_mentioned": true/false,
  "free_trial_mentioned": true/false,
  "book_demo_button": true/false,
  "talk_to_sales_button": true/false,
  "bot_detected": true/false
}

Criteria (must be clearly present in screenshot OR page text):
- pricing_mentioned: Pricing link, plans/tiers, or dollar amounts
- sign_up_mentioned: Sign Up, Get Started, Create Account, or Register
- free_trial_mentioned: Start Free Trial, Try Free, or Free Trial
- book_demo_button: Book Demo, Schedule Demo, or Request Demo
- talk_to_sales_button: Talk to Sales, Contact Sales, or Speak to Sales
- bot_detected: true ONLY if a live chat widget or chat bubble is visibly present (Intercom, Drift, HubSpot messenger, Zendesk chat, "Chat with us" bubble in corner). false for cookie banners, privacy notices, CAPTCHA pages, and pages with no chat widget. Do NOT guess.

If the page is a CAPTCHA/block screen (not the real site), set all fields to false."""

BLOCK_PATTERNS = [
    "cf-challenge", "turnstile", "recaptcha", "hcaptcha", "datadome",
    "verify you are human", "access denied", "unusual traffic",
]

TRAFFIC_API = "https://api.dataforseo.com/v3/dataforseo_labs/google/bulk_traffic_estimation/live"


def normalize_url(url: str) -> str:
    url = url.strip()
    return url if url.startswith("http") else f"https://{url}"


def domain_from_url(url: str) -> str:
    netloc = urlparse(normalize_url(url)).netloc
    return netloc.removeprefix("www.")


def capture(url: str, output_dir: Path) -> dict:
    url = normalize_url(url)
    output_dir.mkdir(parents=True, exist_ok=True)
    screenshot = output_dir / f"{domain_from_url(url).replace('.', '_')}.png"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        response = page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3000)
        page.screenshot(path=str(screenshot), full_page=True)
        text = page.inner_text("body")[:12000]
        html = page.content()[:30000].lower()
        title = page.title()
        final_url = page.url
        status = response.status if response else None
        browser.close()

    return {
        "url": url,
        "final_url": final_url,
        "screenshot": screenshot,
        "text": text,
        "html": html,
        "title": title,
        "status": status,
    }


def is_blocked(page: dict) -> bool:
    haystack = f"{page.get('text', '')}\n{page.get('html', '')}".lower()
    if any(p in haystack for p in BLOCK_PATTERNS):
        return True
    return page.get("status") in {403, 429, 503}


def _page_context(page: dict) -> str:
    return (
        f"URL: {page['url']}\n"
        f"Final URL: {page.get('final_url', page['url'])}\n"
        f"Title: {page.get('title', '')}\n\n"
        f"Page text:\n{page.get('text', '')[:6000]}"
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
    image = Path(page["screenshot"]).read_bytes()
    response = client.models.generate_content(
        model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        contents=[
            ANALYSIS_PROMPT,
            _page_context(page),
            types.Part.from_bytes(data=image, mime_type="image/png"),
        ],
        config=types.GenerateContentConfig(temperature=0.1, response_mime_type="application/json"),
    )
    return _parse_json(response.text or "")


def _analyze_groq(page: dict) -> dict:
    from groq import Groq

    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    image_b64 = base64.b64encode(Path(page["screenshot"]).read_bytes()).decode()
    response = client.chat.completions.create(
        model=os.getenv("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct"),
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": f"{ANALYSIS_PROMPT}\n\n{_page_context(page)}"},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
            ],
        }],
        temperature=0.1,
        response_format={"type": "json_object"},
    )
    return _parse_json(response.choices[0].message.content or "")


def analyze_screenshot(page: dict) -> dict:
    if os.getenv("GEMINI_API_KEY"):
        return _analyze_gemini(page)
    return _analyze_groq(page)


def fetch_traffic(domains: list[str]) -> dict[str, int]:
    login = os.getenv("DATAFORSEO_LOGIN")
    password = os.getenv("DATAFORSEO_PASSWORD")
    if not login or not password or not domains:
        return {}

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
    r.raise_for_status()
    data = r.json()

    out: dict[str, int] = {}
    items = data.get("tasks", [{}])[0].get("result", [{}])[0].get("items", [])
    for item in items:
        target = item.get("target", "")
        metrics = item.get("metrics", {})
        organic = metrics.get("organic", {}) or {}
        paid = metrics.get("paid", {}) or {}
        out[target] = int((organic.get("etv") or 0) + (paid.get("etv") or 0))
    return out


def qualify_one(page: dict, traffic: int | None) -> QualificationResult:
    try:
        g = analyze_screenshot(page)
        blocked = is_blocked(page)
        return QualificationResult(
            url=page["url"],
            pricing_mentioned=False if blocked else bool(g.get("pricing_mentioned")),
            sign_up_mentioned=False if blocked else bool(g.get("sign_up_mentioned")),
            free_trial_mentioned=False if blocked else bool(g.get("free_trial_mentioned")),
            book_demo_button=False if blocked else bool(g.get("book_demo_button")),
            talk_to_sales_button=False if blocked else bool(g.get("talk_to_sales_button")),
            monthly_traffic=traffic,
            bot_detected=bool(g.get("bot_detected")),
        )
    except Exception:
        return QualificationResult(url=page["url"])


def qualify_urls(urls: list[str], output_dir: Path, *, skip_traffic: bool = False) -> list[QualificationResult]:
    results: list[QualificationResult] = []
    captured_pages: list[dict] = []

    for u in urls:
        url = normalize_url(u)
        try:
            page = capture(url, output_dir)
            captured_pages.append(page)
        except Exception:
            results.append(QualificationResult(url=url))

    traffic_map: dict[str, int] = {}
    if not skip_traffic and captured_pages:
        try:
            traffic_map = fetch_traffic([domain_from_url(p["url"]) for p in captured_pages])
        except Exception:
            pass

    for page in captured_pages:
        results.append(qualify_one(page, traffic_map.get(domain_from_url(page["url"]))))

    return results
