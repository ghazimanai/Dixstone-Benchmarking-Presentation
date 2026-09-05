"""Finviz Elite screener export.

Elite subscribers get a documented CSV export endpoint that takes the same
view/filter/order parameters as the web screener plus an `auth` token from
Settings -> API. That makes Finviz the one paid subscription in this stack
that a headless scheduler can legitimately query on its own, so it does the
heavy lifting for universe selection.

The free tier has no export endpoint. Set FINVIZ_AUTH_TOKEN to enable.
"""

from __future__ import annotations

import csv
import io
import os
from datetime import date, datetime

from swingtrader.http import HttpError, get_text
from swingtrader.providers.base import Fundamental, ProviderStatus

EXPORT_URL = "https://elite.finviz.com/export.ashx"
NAME = "finviz-elite"
TOKEN_ENV = "FINVIZ_AUTH_TOKEN"

# View 152 with these columns covers everything the scoring engine wants from
# a screener row. Column ids come from the Elite "custom" view builder.
DEFAULT_COLUMNS = "0,1,2,3,4,5,6,7,65,66,67,68,69,70,71,72,73,74,75,84,85,87"


def status() -> ProviderStatus:
    token = os.environ.get(TOKEN_ENV, "").strip()
    if not token:
        return ProviderStatus(
            NAME,
            "unconfigured",
            f"set {TOKEN_ENV} (Finviz Elite -> Settings -> API) to enable screener pulls",
            entitlement="Finviz Elite",
        )
    return ProviderStatus(
        NAME, "configured", "screener export enabled", entitlement="Finviz Elite"
    )


def token() -> str | None:
    return os.environ.get(TOKEN_ENV, "").strip() or None


def screen(
    filters: str,
    order: str = "-change",
    view: str = "152",
    columns: str = DEFAULT_COLUMNS,
    signal: str | None = None,
    limit: int = 100,
    timeout: float = 30.0,
) -> list[dict[str, str]]:
    """Run a screener export and return raw rows.

    `filters` is the Finviz filter string exactly as it appears in the web
    screener URL, e.g. "cap_midover,sh_avgvol_o500,ta_sma200_pa".
    """
    auth = token()
    if not auth:
        raise RuntimeError(f"{TOKEN_ENV} is not set; Finviz Elite export unavailable")

    body = get_text(
        EXPORT_URL,
        params={
            "v": view,
            "f": filters or None,
            "o": order or None,
            "s": signal or None,
            "c": columns or None,
            "auth": auth,
        },
        timeout=timeout,
    )

    if body.lstrip().startswith("<"):
        raise RuntimeError(
            "Finviz returned HTML instead of CSV -- the auth token is usually "
            "wrong, expired, or the account is not on Elite"
        )

    rows = list(csv.DictReader(io.StringIO(body)))
    return rows[:limit]


def symbols(rows: list[dict[str, str]]) -> list[str]:
    """Pull the ticker column out of screener rows."""
    out: list[str] = []
    for row in rows:
        sym = (row.get("Ticker") or row.get("ticker") or "").strip().upper()
        if sym and sym not in out:
            out.append(sym)
    return out


def to_fundamentals(rows: list[dict[str, str]]) -> dict[str, Fundamental]:
    """Map screener rows onto the shared Fundamental overlay."""
    out: dict[str, Fundamental] = {}
    for row in rows:
        sym = (row.get("Ticker") or "").strip().upper()
        if not sym or sym in out:
            continue  # first row wins; a later sparser duplicate must not clobber it
        out[sym] = Fundamental(
            symbol=sym,
            source=NAME,
            sector=row.get("Sector") or None,
            industry=row.get("Industry") or None,
            market_cap=_num(row.get("Market Cap")),
            analyst_rating=row.get("Analyst Recom") or None,
            analyst_mean_target=_num(row.get("Target Price")),
            earnings_date=_earnings(row.get("Earnings")),
            short_float=_pct(row.get("Short Float")),
        )
    return out


def _num(raw: str | None) -> float | None:
    """Parse Finviz numerics: '1.23B', '456.7M', '12.3%', '-', ''."""
    if not raw:
        return None
    text = raw.strip().replace(",", "").replace("%", "")
    if text in ("-", "", "N/A"):
        return None
    mult = 1.0
    if text[-1] in "KMBT":
        mult = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[text[-1]]
        text = text[:-1]
    try:
        return float(text) * mult
    except ValueError:
        return None


def _pct(raw: str | None) -> float | None:
    val = _num(raw)
    return None if val is None else val / 100.0


def _earnings(raw: str | None) -> date | None:
    """Finviz earnings cells look like 'Nov 20/a' or 'Feb 04/b'."""
    if not raw or raw.strip() in ("-", ""):
        return None
    text = raw.split("/")[0].strip()
    today = date.today()
    for fmt in ("%b %d %Y", "%m/%d/%Y"):
        for year in (today.year, today.year + 1):
            try:
                parsed = datetime.strptime(f"{text} {year}", fmt).date()
            except ValueError:
                continue
            if -120 <= (parsed - today).days <= 300:
                return parsed
    return None


__all__ = ["screen", "symbols", "to_fundamentals", "status", "token", "NAME", "HttpError"]
