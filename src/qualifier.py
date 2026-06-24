"""Screenshot -> bot check -> Gemini -> DataForSEO traffic."""

import base64
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
from google import genai
from google.genai import types
from playwright.sync_api import sync_playwright

from src.models import QualificationResult

GEMINI_PROMPT = """Analyze this SaaS website screenshot. Return JSON only:
{
  "pricing_mentioned": true/false,
  "sign_up_mentioned": true/false,
  "free_trial_mentioned": true/false,
  "book_demo_button": true/false,
  "talk_to_sales_button": true/false,
  "notes": "brief findings"
}

True if visible: Pricing/plans, Sign Up/Get Started, Free Trial, Book/Schedule Demo, Talk/Contact Sales."""

BOT_SIGNALS = [
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
        page.wait_for_timeout(2000)
        page.screenshot(path=str(screenshot), full_page=True)
        text = page.inner_text("body")[:12000]
        html = page.content()[:15000]
        title = page.title()
        status = response.status if response else None
        browser.close()

    return {
        "url": url,
        "screenshot": screenshot,
        "text": text,
        "html": html,
        "title": title,
        "status": status,
    }


def detect_bot(page: dict) -> tuple[bool, str | None]:
    haystack = f"{page['title']}\n{page['text']}\n{page['html']}".lower()
    hits = [s for s in BOT_SIGNALS if s in haystack]
    if page["status"] in {403, 429, 503}:
        hits.append(f"http_{page['status']}")
    if not hits:
        return False, None
    return True, ", ".join(hits)


def analyze_gemini(page: dict) -> dict:
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    image = Path(page["screenshot"]).read_bytes()

    response = client.models.generate_content(
        model=os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
        contents=[GEMINI_PROMPT, types.Part.from_bytes(data=image, mime_type="image/png")],
        config=types.GenerateContentConfig(temperature=0.1, response_mime_type="application/json"),
    )
    raw = (response.text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    return json.loads(raw)


def fetch_traffic(domains: list[str]) -> dict[str, int]:
    """Bulk monthly traffic via DataForSEO Labs API."""
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
        total = int((organic.get("etv") or 0) + (paid.get("etv") or 0))
        out[target] = total
    return out


def qualify_one(page: dict, traffic: int | None) -> QualificationResult:
    bot, bot_details = detect_bot(page)
    try:
        g = analyze_gemini(page)
        return QualificationResult(
            url=page["url"],
            pricing_mentioned=bool(g.get("pricing_mentioned")),
            sign_up_mentioned=bool(g.get("sign_up_mentioned")),
            free_trial_mentioned=bool(g.get("free_trial_mentioned")),
            book_demo_button=bool(g.get("book_demo_button")),
            talk_to_sales_button=bool(g.get("talk_to_sales_button")),
            monthly_traffic=traffic,
            bot_detected=bot,
            bot_details=bot_details,
            notes=g.get("notes"),
        )
    except Exception as e:
        return QualificationResult(
            url=page["url"], bot_detected=bot, bot_details=bot_details, error=str(e),
        )


def qualify_urls(urls: list[str], output_dir: Path, *, skip_traffic: bool = False) -> list[QualificationResult]:
    pages = [capture(normalize_url(u), output_dir) for u in urls]

    traffic_map: dict[str, int] = {}
    if not skip_traffic:
        try:
            traffic_map = fetch_traffic([domain_from_url(p["url"]) for p in pages])
        except Exception:
            pass

    results = []
    for page in pages:
        domain = domain_from_url(page["url"])
        traffic = traffic_map.get(domain)
        results.append(qualify_one(page, traffic))
    return results
