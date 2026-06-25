"""Thread-safe JSONL log for Gemini token usage per URL."""

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

_lock = threading.Lock()
DEFAULT_LOG_PATH = Path("logs/token_usage.jsonl")


def _log_path() -> Path:
    return Path(os.getenv("TOKEN_LOG_PATH", DEFAULT_LOG_PATH))


def log_token_usage(
    *,
    url: str,
    model: str,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    total_tokens: int | None = None,
    cached_tokens: int | None = None,
    duration_ms: float | None = None,
    status: str = "success",
    error: str | None = None,
) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "url": url,
        "provider": "gemini",
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
        "cached_tokens": cached_tokens,
        "duration_ms": round(duration_ms, 1) if duration_ms is not None else None,
        "status": status,
    }
    if error:
        entry["error"] = error

    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    with _lock:
        with path.open("a", encoding="utf-8") as f:
            f.write(line)


def usage_from_gemini(response) -> tuple[int | None, int | None, int | None, int | None]:
    meta = getattr(response, "usage_metadata", None)
    if not meta:
        return None, None, None, None
    return (
        getattr(meta, "prompt_token_count", None),
        getattr(meta, "candidates_token_count", None),
        getattr(meta, "total_token_count", None),
        getattr(meta, "cached_content_token_count", None),
    )
