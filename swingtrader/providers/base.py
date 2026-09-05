"""Shared types for data providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

Availability = Literal["live", "configured", "unconfigured", "unavailable"]


@dataclass(frozen=True)
class Bar:
    """One daily OHLCV bar."""

    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class History:
    """A ticker's daily bar history, oldest first."""

    symbol: str
    bars: list[Bar]
    currency: str = "USD"
    exchange: str = ""
    source: str = ""

    def __len__(self) -> int:
        return len(self.bars)

    @property
    def closes(self) -> list[float]:
        return [b.close for b in self.bars]

    @property
    def highs(self) -> list[float]:
        return [b.high for b in self.bars]

    @property
    def lows(self) -> list[float]:
        return [b.low for b in self.bars]

    @property
    def opens(self) -> list[float]:
        return [b.open for b in self.bars]

    @property
    def volumes(self) -> list[float]:
        return [b.volume for b in self.bars]

    @property
    def last_bar(self) -> Bar | None:
        return self.bars[-1] if self.bars else None


@dataclass
class Fundamental:
    """Fundamental / analyst overlay for one ticker.

    Every field is optional: which of them are populated depends entirely on
    which subscriptions the operator has wired up.
    """

    symbol: str
    source: str
    sector: str | None = None
    industry: str | None = None
    market_cap: float | None = None
    star_rating: float | None = None          # Morningstar 1-5
    fair_value: float | None = None           # Morningstar FVE
    price_to_fair_value: float | None = None
    economic_moat: str | None = None          # None / Narrow / Wide
    uncertainty: str | None = None
    analyst_mean_target: float | None = None  # LSEG / Bloomberg consensus
    analyst_rating: str | None = None
    earnings_date: date | None = None
    short_float: float | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class ProviderStatus:
    """Whether a source is usable right now, and why not if it isn't."""

    name: str
    availability: Availability
    detail: str = ""
    entitlement: str = ""

    @property
    def usable(self) -> bool:
        return self.availability in ("live", "configured")

    def line(self) -> str:
        icon = {"live": "OK ", "configured": "SET", "unconfigured": "-- ", "unavailable": "XX "}
        return f"[{icon[self.availability]}] {self.name:<14} {self.detail}"
