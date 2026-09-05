"""Market regime.

A swing-long book behaves very differently depending on whether the index is
above or below its own trend. Rather than let every setup re-litigate that,
the regime is computed once from the benchmark and passed down: it gates
which directions are allowed and scales position size.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from swingtrader.snapshot import Snapshot


@dataclass
class MarketContext:
    """Benchmark state shared by every setup evaluation."""

    benchmark: str
    regime: str = "unknown"          # risk_on | neutral | risk_off | unknown
    close: float | None = None
    above_200: bool | None = None
    above_50: bool | None = None
    roc21: float | None = None
    roc63: float | None = None
    atr_pct: float | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def longs_allowed(self) -> bool:
        return self.regime != "risk_off"

    @property
    def shorts_favoured(self) -> bool:
        return self.regime == "risk_off"

    def size_factor(self, action: str, reduce_factor: float) -> float:
        """How much of the normal position size this regime permits."""
        if self.regime == "risk_off" and action == "reduce":
            return max(0.0, min(1.0, reduce_factor))
        return 1.0

    def summary(self) -> str:
        bits = [f"{self.benchmark} regime: {self.regime.replace('_', '-')}"]
        if self.close is not None:
            bits.append(f"last {self.close:,.2f}")
        if self.roc21 is not None:
            bits.append(f"21d {self.roc21:+.1f}%")
        if self.roc63 is not None:
            bits.append(f"63d {self.roc63:+.1f}%")
        return " | ".join(bits)


def classify(snap: Snapshot | None, benchmark: str) -> MarketContext:
    """Derive the regime from the benchmark's own snapshot."""
    if snap is None:
        return MarketContext(
            benchmark=benchmark,
            regime="unknown",
            notes=["benchmark history unavailable -- treating regime as neutral"],
        )

    ctx = MarketContext(
        benchmark=benchmark,
        close=snap.close,
        above_200=snap.above_200,
        above_50=snap.sma50 is not None and snap.close > snap.sma50,
        roc21=snap.roc21,
        roc63=snap.roc63,
        atr_pct=snap.atr_pct,
    )

    score = 0
    if ctx.above_200:
        score += 2
    if ctx.above_50:
        score += 1
    if snap.sma200_rising:
        score += 1
    if (snap.roc21 or 0) > 0:
        score += 1

    if score >= 4:
        ctx.regime = "risk_on"
    elif score >= 2:
        ctx.regime = "neutral"
    else:
        ctx.regime = "risk_off"

    if ctx.above_200 is False:
        ctx.notes.append(f"{benchmark} is below its 200-day average")
    if snap.atr_pct and snap.atr_pct > 2.5:
        ctx.notes.append(
            f"{benchmark} ATR is {snap.atr_pct:.1f}% of price -- index volatility is elevated"
        )
    return ctx
