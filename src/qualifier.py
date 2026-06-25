"""Page text -> LLM CTA analysis + screenshot vision bot detection -> DataForSEO traffic."""

import base64
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx  # pyright: ignore[reportMissingImports]
from playwright.sync_api import sync_playwright  # pyright: ignore[reportMissingImports]

from src.models import QualificationResult
from src.token_log import log_token_usage, usage_from_gemini, usage_from_groq

CTA_ANALYSIS_PROMPT = """You are qualifying B2B SaaS landing pages for sales outreach.

You receive page text and a list of interactive elements (links/buttons with text, aria-label, or title).
Use ONLY this text data — do not infer from images.

Return JSON only — no markdown, no explanation:
{
  "pricing_mentioned": true/false,
  "sign_up_mentioned": true/false,
  "free_trial_mentioned": true/false,
  "book_demo_button": true/false,
  "talk_to_sales_button": true/false
}

## How to decide

1. Check interactive elements first (nav links, buttons, aria-labels — catches icon-only CTAs).
2. Cross-check page text for headings, hero copy, and plan cards.
3. Set true only when evidence is explicit. When unsure, use false.
4. Multiple fields may be true on the same page.

## Field definitions

### pricing_mentioned
true if ANY appear as nav link, footer link, heading, plan card, or CTA:
- Pricing, Plans, Plan, Packages, Compare Plans, See Plans, View Pricing, Plans & Pricing
- Tier names with prices: Starter / Pro / Enterprise / Basic / Business
- Price signals: $, €, £, /mo, /month, per user, per seat, billed annually
false for: generic "Buy now" with no plan context, blog/investor copy about pricing

### sign_up_mentioned
true if ANY self-serve account-creation CTA appears:
- Sign Up, Signup, Register, Create Account, Join Now, Join Free
- Get Started, Start Now, Start for Free, Start Building, Try Now, Launch App, Open App
- Icon-only counts if aria-label/title says sign up, register, get started, or create account
false for: Book a demo alone, Contact us alone, newsletter Subscribe, login-only with no sign-up

### free_trial_mentioned
true if ANY no-cost trial period is explicit:
- Free Trial, Start Free Trial, Try Free, Try It Free, Try for Free
- X-day trial, 14-day trial, 30-day trial, trial period, Start trial
false for: Free plan/tier without trial wording, Get started free without trial (sign_up only), Free demo (book_demo)

### book_demo_button
true if ANY sales-led demo/scheduling CTA appears:
- Book Demo, Schedule Demo, Request Demo, Get a Demo, Product Demo, Live Demo
- Book a Call, Schedule a Call, Book Meeting (sales/product context)
- Icon-only counts if aria-label/title mentions demo, schedule, or book a call
false for: Watch video/webinar with no booking, generic Learn more

### talk_to_sales_button
true if ANY speak-with-sales CTA appears:
- Talk to Sales, Contact Sales, Speak to Sales, Request a Quote, Get a Quote
- Enterprise Sales, Talk to an Expert, Chat with Sales, Email Sales
- Contact us ONLY when clearly sales/enterprise context
false for: generic footer Contact/Support/Help, customer-support chat

If page text indicates CAPTCHA, access denied, or bot block (not the real site), set all fields to false."""

BOT_DETECTION_PROMPT = """Look at this website screenshot.

Return JSON only: {"bot_detected": true/false}

Set bot_detected to true ONLY if a live-chat messenger widget is visibly present:
- Floating chat bubble (usually bottom-right or bottom-left corner)
- Known widgets: Intercom, Drift, HubSpot messenger, Zendesk, LiveChat, Crisp, Tidio, Freshchat, Olark, Tawk.to
- Visible labels like "Chat with us", "Live chat", "Message us" on a chat launcher

Set false for: cookie banners, privacy notices, CAPTCHA screens, email signup popups, FAQ widgets, social icons, no chat widget visible.
Do NOT guess — only true if you can see a chat launcher or bubble."""

CHAT_WIDGET_HTML_PATTERNS = [
    "intercom", "widget.intercom", "drift.com", "driftt.com", "hubspot.com/conversations",
    "hs-scripts.com", "zendesk", "zopim", "livechatinc", "crisp.chat", "tidio.co",
    "freshchat", "freshworks.com", "tawk.to", "olark", "ada.support", "forethought",
    "gorgias.chat", "kustomer", "reamaze",
]

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
        interactive = page.evaluate("""() => {
            const sel = 'a, button, [role="button"], input[type="submit"], [role="link"]';
            const seen = new Set();
            const out = [];
            for (const el of document.querySelectorAll(sel)) {
                const text = (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 120);
                const aria = (el.getAttribute('aria-label') || '').trim().slice(0, 120);
                const title = (el.getAttribute('title') || '').trim().slice(0, 120);
                const href = el.href || el.getAttribute('href') || '';
                if (!text && !aria && !title) continue;
                const key = `${text}|${aria}|${title}|${href}`;
                if (seen.has(key)) continue;
                seen.add(key);
                out.push({ text, aria, title, href: String(href).slice(0, 200) });
                if (out.length >= 150) break;
            }
            return out;
        }""")
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
        "interactive": interactive,
        "html": html,
        "title": title,
        "status": status,
    }


def is_blocked(page: dict) -> bool:
    haystack = f"{page.get('text', '')}\n{page.get('html', '')}".lower()
    if any(p in haystack for p in BLOCK_PATTERNS):
        return True
    return page.get("status") in {403, 429, 503}


def _format_interactive(items: list) -> str:
    lines: list[str] = []
    for item in items[:120]:
        parts = []
        if item.get("text"):
            parts.append(f'text="{item["text"]}"')
        if item.get("aria"):
            parts.append(f'aria="{item["aria"]}"')
        if item.get("title"):
            parts.append(f'title="{item["title"]}"')
        if item.get("href"):
            parts.append(f'href="{item["href"]}"')
        if parts:
            lines.append("- " + ", ".join(parts))
    return "\n".join(lines) if lines else "(none extracted)"


def _page_context(page: dict) -> str:
    interactive_block = _format_interactive(page.get("interactive") or [])
    return (
        f"URL: {page['url']}\n"
        f"Final URL: {page.get('final_url', page['url'])}\n"
        f"Title: {page.get('title', '')}\n\n"
        f"Interactive elements (links/buttons — check aria-label for icon-only CTAs):\n"
        f"{interactive_block}\n\n"
        f"Page text:\n{page.get('text', '')[:6000]}"
    )


def _analyze_ctas_gemini(page: dict) -> dict:
    from google import genai
    from google.genai import types

    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    response = client.models.generate_content(
        model=model,
        contents=[CTA_ANALYSIS_PROMPT, _page_context(page)],
        config=types.GenerateContentConfig(temperature=0.1, response_mime_type="application/json"),
    )
    inp, out, total = usage_from_gemini(response)
    log_token_usage(
        url=page["url"],
        call_type="cta_analysis",
        provider="gemini",
        model=model,
        input_tokens=inp,
        output_tokens=out,
        total_tokens=total,
    )
    return _parse_json(response.text or "")


def _analyze_ctas_groq(page: dict) -> dict:
    from groq import Groq

    model = os.getenv("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": f"{CTA_ANALYSIS_PROMPT}\n\n{_page_context(page)}"}],
        temperature=0.1,
        response_format={"type": "json_object"},
    )
    inp, out, total = usage_from_groq(response)
    log_token_usage(
        url=page["url"],
        call_type="cta_analysis",
        provider="groq",
        model=model,
        input_tokens=inp,
        output_tokens=out,
        total_tokens=total,
    )
    return _parse_json(response.choices[0].message.content or "")


def analyze_ctas_from_page(page: dict) -> dict:
    """Detect CTA fields via LLM using page text + interactive elements."""
    if os.getenv("GEMINI_API_KEY"):
        return _analyze_ctas_gemini(page)
    return _analyze_ctas_groq(page)


def _chat_widget_in_html(page: dict) -> bool:
    html = page.get("html", "")
    return any(p in html for p in CHAT_WIDGET_HTML_PATTERNS)


def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    return json.loads(raw)


def _detect_bot_gemini(page: dict) -> bool:
    from google import genai
    from google.genai import types

    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    image = Path(page["screenshot"]).read_bytes()
    response = client.models.generate_content(
        model=model,
        contents=[
            BOT_DETECTION_PROMPT,
            types.Part.from_bytes(data=image, mime_type="image/png"),
        ],
        config=types.GenerateContentConfig(temperature=0.1, response_mime_type="application/json"),
    )
    inp, out, total = usage_from_gemini(response)
    log_token_usage(
        url=page["url"],
        call_type="bot_detection",
        provider="gemini",
        model=model,
        input_tokens=inp,
        output_tokens=out,
        total_tokens=total,
    )
    return bool(_parse_json(response.text or "").get("bot_detected"))


def _detect_bot_groq(page: dict) -> bool:
    from groq import Groq

    model = os.getenv("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    image_b64 = base64.b64encode(Path(page["screenshot"]).read_bytes()).decode()
    response = client.chat.completions.create(
        model=model,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": BOT_DETECTION_PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
            ],
        }],
        temperature=0.1,
        response_format={"type": "json_object"},
    )
    inp, out, total = usage_from_groq(response)
    log_token_usage(
        url=page["url"],
        call_type="bot_detection",
        provider="groq",
        model=model,
        input_tokens=inp,
        output_tokens=out,
        total_tokens=total,
    )
    return bool(_parse_json(response.choices[0].message.content or "").get("bot_detected"))


def detect_bot_from_screenshot(page: dict) -> bool:
    """Detect live-chat widget — HTML sniff first, then screenshot vision."""
    if _chat_widget_in_html(page):
        return True
    if os.getenv("GEMINI_API_KEY"):
        return _detect_bot_gemini(page)
    return _detect_bot_groq(page)


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
        blocked = is_blocked(page)
        ctas = analyze_ctas_from_page(page)
        bot = detect_bot_from_screenshot(page)
        return QualificationResult(
            url=page["url"],
            pricing_mentioned=False if blocked else ctas["pricing_mentioned"],
            sign_up_mentioned=False if blocked else ctas["sign_up_mentioned"],
            free_trial_mentioned=False if blocked else ctas["free_trial_mentioned"],
            book_demo_button=False if blocked else ctas["book_demo_button"],
            talk_to_sales_button=False if blocked else ctas["talk_to_sales_button"],
            monthly_traffic=traffic,
            bot_detected=bot,
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
