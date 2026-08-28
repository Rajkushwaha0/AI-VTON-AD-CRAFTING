"""API key auth for Kling API."""

from __future__ import annotations

import os

KLING_API_BASE_DEFAULT = "https://api-singapore.klingai.com/v1"


def kling_configured() -> bool:
    return bool((os.environ.get("KLING_API_KEY") or "").strip())


def api_base_url() -> str:
    base = (os.environ.get("KLING_API_BASE_URL") or KLING_API_BASE_DEFAULT).strip()
    return base.rstrip("/")


def api_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {(os.environ.get('KLING_API_KEY') or '').strip()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
