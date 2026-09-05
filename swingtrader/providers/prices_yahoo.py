"""Daily OHLCV from the Yahoo Finance chart endpoint.

The always-on backbone of the tool: no key, no desktop application, no
entitlement, so the technical engine keeps working even when every paid
terminal is out of reach from the scheduler.

Yahoo rate-limits per source IP and the two public hosts throttle
independently, so requests rotate across them and a 429 on one host is
retried on the other before giving up.
"""

from __future__ import annotations

import os
import random
import time
from datetime import date, datetime, timezone

from swingtrader.http import HttpError, get_json
from swingtrader.providers.base import Bar, History, ProviderStatus

HOSTS = ("https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com")
PATH = "/v8/finance/chart/{symbol}"
NAME = "yahoo-prices"


def status() -> ProviderStatus:
    if os.environ.get("SWINGTRADER_DISABLE_YAHOO"):
        return ProviderStatus(NAME, "unavailable", "disabled via SWINGTRADER_DISABLE_YAHOO")
    return ProviderStatus(
        NAME, "live", "daily OHLCV, no key required", entitlement="public endpoint"
    )


def fetch_history(symbol: str, lookback_days: int = 400, timeout: float = 20.0) -> History:
    """Fetch daily bars for `symbol`, oldest first.

    Raises HttpError when every host refuses, ValueError when the response
    carries no usable series (bad ticker, delisted, unexpected shape).
    """
    rng = _range_for(lookback_days)
    params = {"range": rng, "interval": "1d", "includePrePost": "false"}
    last_err: Exception | None = None

    for attempt, host in enumerate(_host_order()):
        url = host + PATH.format(symbol=symbol)
        try:
            payload = get_json(url, params=params, timeout=timeout, retries=2, backoff=2.0)
            return _parse(symbol, payload)
        except HttpError as exc:
            last_err = exc
            if exc.status == 429 and attempt + 1 < len(HOSTS):
                time.sleep(1.0 + random.random())
                continue
            if exc.status in (400, 404):
                raise ValueError(f"{symbol}: not found upstream ({exc.status})") from exc
            last_err = exc

    raise HttpError(symbol, getattr(last_err, "status", None), str(last_err))


def _parse(symbol: str, payload: dict) -> History:
    chart = (payload or {}).get("chart") or {}
    if chart.get("error"):
        raise ValueError(f"{symbol}: {chart['error'].get('description', 'chart error')}")
    results = chart.get("result") or []
    if not results:
        raise ValueError(f"{symbol}: empty chart result")

    res = results[0]
    stamps = res.get("timestamp") or []
    quote = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    meta = res.get("meta") or {}

    opens, highs = quote.get("open") or [], quote.get("high") or []
    lows, closes = quote.get("low") or [], quote.get("close") or []
    vols = quote.get("volume") or []

    bars: list[Bar] = []
    for i, ts in enumerate(stamps):
        o, h, l, c = _at(opens, i), _at(highs, i), _at(lows, i), _at(closes, i)
        if None in (o, h, l, c):
            continue  # Yahoo pads holidays and halts with nulls
        bars.append(
            Bar(
                day=datetime.fromtimestamp(ts, tz=timezone.utc).date(),
                open=float(o),
                high=float(h),
                low=float(l),
                close=float(c),
                volume=float(_at(vols, i) or 0.0),
            )
        )

    if not bars:
        raise ValueError(f"{symbol}: no usable bars in response")

    return History(
        symbol=symbol.upper(),
        bars=bars,
        currency=meta.get("currency") or "USD",
        exchange=meta.get("fullExchangeName") or meta.get("exchangeName") or "",
        source=NAME,
    )


def _host_order() -> list[str]:
    hosts = list(HOSTS)
    random.shuffle(hosts)
    return hosts


def _at(seq: list, i: int):
    return seq[i] if i < len(seq) else None


def _range_for(days: int) -> str:
    for limit, label in ((5, "5d"), (31, "1mo"), (93, "3mo"), (186, "6mo"), (370, "1y")):
        if days <= limit:
            return label
    return "2y" if days <= 740 else "5y"


def latest_session(hist: History) -> date | None:
    bar = hist.last_bar
    return bar.day if bar else None
