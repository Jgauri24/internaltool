"""Screenshot -> Groq vision -> DataForSEO traffic."""

import base64
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
from groq import Groq
from playwright.sync_api import sync_playwright

from src.models import QualificationResult

ANALYSIS_PROMPT = """Analyze this SaaS website screenshot. Return JSON only:
{
  "pricing_mentioned": true/false,
  "sign_up_mentioned": true/false,
  "free_trial_mentioned": true/false,
  "book_demo_button": true/false,
  "talk_to_sales_button": true/false
}

True if visible: Pricing/plans, Sign Up/Get Started, Free Trial, Book/Schedule Demo, Talk/Contact Sales."""

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
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2000)
        page.screenshot(path=str(screenshot), full_page=True)
        browser.close()

    return {"url": url, "screenshot": screenshot}


def analyze_screenshot(page: dict) -> dict:
    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    image_b64 = base64.b64encode(Path(page["screenshot"]).read_bytes()).decode()

    response = client.chat.completions.create(
        model=os.getenv("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct"),
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": ANALYSIS_PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
            ],
        }],
        temperature=0.1,
        response_format={"type": "json_object"},
    )
    raw = (response.choices[0].message.content or "").strip()
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
    try:
        g = analyze_screenshot(page)
        return QualificationResult(
            url=page["url"],
            pricing_mentioned=bool(g.get("pricing_mentioned")),
            sign_up_mentioned=bool(g.get("sign_up_mentioned")),
            free_trial_mentioned=bool(g.get("free_trial_mentioned")),
            book_demo_button=bool(g.get("book_demo_button")),
            talk_to_sales_button=bool(g.get("talk_to_sales_button")),
            monthly_traffic=traffic,
        )
    except Exception:
        return QualificationResult(url=page["url"])


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
