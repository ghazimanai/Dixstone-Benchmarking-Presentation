"""Synthetic bar builders so tests never touch the network."""

from __future__ import annotations

import math
from datetime import date, timedelta

from swingtrader.providers.base import Bar, History

START = date(2024, 1, 1)


def make_bars(
    closes: list[float],
    volumes: list[float] | None = None,
    range_pct: float = 0.02,
    gaps: dict[int, float] | None = None,
) -> list[Bar]:
    """Build daily bars around a close series.

    `range_pct` sets each bar's high/low spread; `gaps` maps a bar index to an
    overnight gap in percent so tests can plant an earnings-style jump.
    """
    gaps = gaps or {}
    bars: list[Bar] = []
    for i, close in enumerate(closes):
        prev_close = closes[i - 1] if i else close
        gap = gaps.get(i, 0.0)
        open_ = prev_close * (1 + gap / 100.0) if i else close
        span = close * range_pct
        high = max(open_, close) + span / 2
        low = min(open_, close) - span / 2
        bars.append(
            Bar(
                day=_weekday(i),
                open=round(open_, 4),
                high=round(high, 4),
                low=round(max(low, 0.01), 4),
                close=round(close, 4),
                volume=(volumes[i] if volumes else 1_000_000.0),
            )
        )
    return bars


def make_history(
    symbol: str = "TEST",
    closes: list[float] | None = None,
    volumes: list[float] | None = None,
    **kwargs,
) -> History:
    closes = closes if closes is not None else uptrend(260)
    return History(
        symbol=symbol,
        bars=make_bars(closes, volumes, **kwargs),
        currency="USD",
        exchange="TEST",
        source="fixture",
    )


def uptrend(n: int, start: float = 100.0, daily: float = 0.0035, wobble: float = 0.006) -> list[float]:
    """A steady advance with a mild sine wobble, so RSI is not pinned at 100."""
    return [
        start * math.exp(daily * i) * (1 + wobble * math.sin(i / 3.0)) for i in range(n)
    ]


def downtrend(n: int, start: float = 100.0, daily: float = -0.003) -> list[float]:
    return uptrend(n, start=start, daily=daily)


def flat(n: int, level: float = 100.0) -> list[float]:
    return [level * (1 + 0.002 * math.sin(i / 2.0)) for i in range(n)]


def _weekday(index: int) -> date:
    """Advance by trading days so fixtures never land on a weekend."""
    day = START
    added = 0
    while added < index:
        day += timedelta(days=1)
        if day.weekday() < 5:
            added += 1
    return day
