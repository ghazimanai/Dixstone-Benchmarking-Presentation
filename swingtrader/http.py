"""Minimal, dependency-free HTTP helper.

Uses urllib so the whole tool runs on a bare Python 3.9+ install with no
`pip install` step -- which matters a lot for something that has to fire
unattended every weekday morning.
"""

from __future__ import annotations

import gzip
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping

# Deliberately terse. Yahoo aggressively rate-limits the common full Chrome
# UA string (it is the one every scraper copies) and returns 429 to it while
# serving a plain "Mozilla/5.0" normally. Override with SWINGTRADER_UA.
DEFAULT_UA = os.environ.get("SWINGTRADER_UA", "Mozilla/5.0")


class HttpError(RuntimeError):
    """Raised when a request fails after all retries."""

    def __init__(self, url: str, status: int | None, detail: str):
        self.url = url
        self.status = status
        super().__init__(f"{url} -> {status or 'no response'}: {detail}")


def get(
    url: str,
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = 20.0,
    retries: int = 3,
    backoff: float = 1.5,
) -> bytes:
    """GET with retry + exponential backoff. Returns the raw body."""
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        url = f"{url}{'&' if '?' in url else '?'}{urllib.parse.urlencode(clean)}"

    hdrs = {"User-Agent": DEFAULT_UA, "Accept-Encoding": "gzip", "Accept": "*/*"}
    hdrs.update(headers or {})

    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                return body
        except urllib.error.HTTPError as exc:  # noqa: PERF203
            last = exc
            # Client errors other than rate-limiting will not fix themselves.
            if exc.code not in (408, 429, 500, 502, 503, 504):
                raise HttpError(url, exc.code, exc.reason or "http error") from exc
        except Exception as exc:  # network reset, DNS, timeout
            last = exc
        if attempt < retries - 1:
            time.sleep(backoff ** (attempt + 1))

    status = getattr(last, "code", None)
    raise HttpError(url, status, str(last))


def get_json(url: str, **kwargs: Any) -> Any:
    """GET and parse JSON."""
    return json.loads(get(url, **kwargs).decode("utf-8", errors="replace"))


def get_text(url: str, **kwargs: Any) -> str:
    """GET and decode as text."""
    return get(url, **kwargs).decode("utf-8", errors="replace")
