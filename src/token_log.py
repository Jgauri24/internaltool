"""Append-only JSONL log for LLM token usage per URL."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_LOG_PATH = Path("logs/token_usage.jsonl")


def _log_path() -> Path:
    return Path(os.getenv("TOKEN_LOG_PATH", DEFAULT_LOG_PATH))


def log_token_usage(
    *,
    url: str,
    call_type: str,
    provider: str,
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
    total_tokens: int | None = None,
) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "url": url,
        "call_type": call_type,
        "provider": provider,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens
        if total_tokens is not None
        else (
            (input_tokens or 0) + (output_tokens or 0)
            if input_tokens is not None or output_tokens is not None
            else None
        ),
    }
    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def usage_from_gemini(response) -> tuple[int | None, int | None, int | None]:
    meta = getattr(response, "usage_metadata", None)
    if not meta:
        return None, None, None
    inp = getattr(meta, "prompt_token_count", None)
    out = getattr(meta, "candidates_token_count", None)
    total = getattr(meta, "total_token_count", None)
    return inp, out, total


def usage_from_groq(response) -> tuple[int | None, int | None, int | None]:
    usage = getattr(response, "usage", None)
    if not usage:
        return None, None, None
    inp = getattr(usage, "prompt_tokens", None)
    out = getattr(usage, "completion_tokens", None)
    total = getattr(usage, "total_tokens", None)
    return inp, out, total
