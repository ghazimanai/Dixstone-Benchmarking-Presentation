"""Per-symbol indicator snapshot.

Collapses a whole price history into the handful of numbers the setup rules
actually read. Computing this once per symbol keeps the rules cheap and, more
importantly, keeps them readable: a setup should say what it wants, not
recompute an ATR.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from swingtrader import indicators as ind
from swingtrader.providers.base import History


@dataclass
class Snapshot:
    """Indicator values as of the most recent bar."""

    symbol: str
    day: date
    bars: int
    close: float
    open: float
    high: float
    low: float
    volume: float
    prev_close: float
    prev_high: float
    prev_low: float

    sma20: float | None = None
    sma50: float | None = None
    sma200: float | None = None
    ema10: float | None = None
    ema20: float | None = None
    sma50_prev: float | None = None
    sma200_prev: float | None = None

    rsi14: float | None = None
    rsi14_prev: float | None = None
    rsi2: float | None = None
    atr14: float | None = None
    adx14: float | None = None
    macd_hist: float | None = None
    macd_hist_prev: float | None = None

    bb_upper: float | None = None
    bb_mid: float | None = None
    bb_lower: float | None = None
    donchian_high20: float | None = None
    donchian_low20: float | None = None

    rel_volume: float | None = None
    avg_dollar_volume: float | None = None
    roc21: float | None = None
    roc63: float | None = None
    roc126: float | None = None

    high_52w: float | None = None
    low_52w: float | None = None
    swing_low10: float | None = None
    swing_high10: float | None = None
    range_pct_5d: float | None = None
    median_range_pct_20: float | None = None
    max_gap_pct_10d: float | None = None
    days_since_big_gap: int | None = None
    consecutive_down: int = 0
    consecutive_up: int = 0

    # --- derived conveniences -------------------------------------------------

    @property
    def atr_pct(self) -> float | None:
        if self.atr14 is None or self.close <= 0:
            return None
        return 100.0 * self.atr14 / self.close

    @property
    def gap_pct(self) -> float | None:
        if self.prev_close <= 0:
            return None
        return 100.0 * (self.open / self.prev_close - 1.0)

    @property
    def pct_from_52w_high(self) -> float | None:
        if not self.high_52w:
            return None
        return 100.0 * (self.close / self.high_52w - 1.0)

    @property
    def tightness(self) -> float | None:
        """5-day range measured in typical daily ranges.

        Uses the *median* daily range rather than ATR on purpose: Wilder's
        ATR is inflated for weeks by a single earnings gap, which makes a
        violently volatile name look like a quiet consolidation. A median
        barely moves for one outlier day.

        Roughly: below 0.5 is a genuine coil, 1.0 is a normally trending
        stock, above that it is still moving.
        """
        if self.range_pct_5d is None or not self.median_range_pct_20:
            return None
        return self.range_pct_5d / (self.median_range_pct_20 * 5.0)

    @property
    def event_gap(self) -> bool:
        """True when a recent overnight gap says this move is news-driven."""
        return (self.max_gap_pct_10d or 0.0) >= 7.0

    @property
    def close_position_in_range(self) -> float | None:
        """0 = closed on the low, 1 = closed on the high. Conviction proxy."""
        span = self.high - self.low
        if span <= 0:
            return None
        return (self.close - self.low) / span

    @property
    def above_200(self) -> bool:
        return self.sma200 is not None and self.close > self.sma200

    @property
    def sma50_rising(self) -> bool:
        return (
            self.sma50 is not None
            and self.sma50_prev is not None
            and self.sma50 > self.sma50_prev
        )

    @property
    def sma200_rising(self) -> bool:
        return (
            self.sma200 is not None
            and self.sma200_prev is not None
            and self.sma200 > self.sma200_prev
        )

    @property
    def stacked_bullish(self) -> bool:
        """Classic bullish alignment: price > 50ma > 200ma, both rising."""
        return (
            self.sma50 is not None
            and self.sma200 is not None
            and self.close > self.sma50 > self.sma200
            and self.sma50_rising
        )

    def distance_pct(self, level: float | None) -> float | None:
        """How far the close sits above (+) or below (-) a level, in percent."""
        if level is None or level <= 0:
            return None
        return 100.0 * (self.close / level - 1.0)


def build(hist: History) -> Snapshot | None:
    """Compute a snapshot from a price history, or None if it is too short."""
    if len(hist) < 30:
        return None

    closes, highs, lows = hist.closes, hist.highs, hist.lows
    vols = hist.volumes
    bar = hist.bars[-1]
    prev = hist.bars[-2]

    sma50 = ind.sma(closes, 50)
    sma200 = ind.sma(closes, 200)
    rsi14 = ind.rsi(closes, 14)
    _, _, hist_line = ind.macd(closes)
    bb_up, bb_mid, bb_lo = ind.bollinger(closes, 20, 2.0)
    dc_hi, dc_lo = ind.donchian(highs, lows, 20)

    window = min(len(closes), 252)
    dollar_vols = [closes[i] * vols[i] for i in range(len(closes))]
    addv = sum(dollar_vols[-21:-1]) / 20 if len(dollar_vols) >= 21 else None

    five = min(5, len(closes))
    hi5, lo5 = max(highs[-five:]), min(lows[-five:])
    range_pct_5d = 100.0 * (hi5 - lo5) / bar.close if bar.close > 0 else None

    ranges = sorted(
        100.0 * (highs[i] - lows[i]) / closes[i]
        for i in range(max(0, len(closes) - 20), len(closes))
        if closes[i] > 0
    )
    median_range_pct = ranges[len(ranges) // 2] if ranges else None
    max_gap, since_gap = _gap_stats(hist, lookback=10)

    return Snapshot(
        symbol=hist.symbol,
        day=bar.day,
        bars=len(hist),
        close=bar.close,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        volume=bar.volume,
        prev_close=prev.close,
        prev_high=prev.high,
        prev_low=prev.low,
        sma20=ind.last(ind.sma(closes, 20)),
        sma50=ind.last(sma50),
        sma200=ind.last(sma200),
        sma50_prev=ind.last(sma50, 5),
        sma200_prev=ind.last(sma200, 5),
        ema10=ind.last(ind.ema(closes, 10)),
        ema20=ind.last(ind.ema(closes, 20)),
        rsi14=ind.last(rsi14),
        rsi14_prev=ind.last(rsi14, 1),
        rsi2=ind.last(ind.rsi(closes, 2)),
        atr14=ind.last(ind.atr(highs, lows, closes, 14)),
        adx14=ind.last(ind.adx(highs, lows, closes, 14)),
        macd_hist=ind.last(hist_line),
        macd_hist_prev=ind.last(hist_line, 1),
        bb_upper=ind.last(bb_up),
        bb_mid=ind.last(bb_mid),
        bb_lower=ind.last(bb_lo),
        donchian_high20=ind.last(dc_hi),
        donchian_low20=ind.last(dc_lo),
        rel_volume=ind.last(ind.relative_volume(vols, 20)),
        avg_dollar_volume=addv,
        roc21=ind.last(ind.roc(closes, 21)),
        roc63=ind.last(ind.roc(closes, 63)),
        roc126=ind.last(ind.roc(closes, 126)),
        high_52w=max(highs[-window:]),
        low_52w=min(lows[-window:]),
        swing_low10=ind.swing_low(lows, 10),
        swing_high10=ind.swing_high(highs, 10),
        range_pct_5d=range_pct_5d,
        median_range_pct_20=median_range_pct,
        max_gap_pct_10d=max_gap,
        days_since_big_gap=since_gap,
        consecutive_down=_streak(closes, down=True),
        consecutive_up=_streak(closes, down=False),
    )


def _gap_stats(hist: History, lookback: int = 10) -> tuple[float | None, int | None]:
    """Largest absolute overnight gap recently, and how long ago it was.

    An earnings gap distorts ATR, the 5-day range and the pullback reading
    all at once, so the setups need to know one happened.
    """
    bars = hist.bars
    if len(bars) < 2:
        return None, None
    start = max(1, len(bars) - lookback)
    largest = 0.0
    most_recent: int | None = None
    for i in range(start, len(bars)):
        prev_close = bars[i - 1].close
        if prev_close <= 0:
            continue
        gap = abs(100.0 * (bars[i].open / prev_close - 1.0))
        largest = max(largest, gap)
        if gap >= 7.0:
            most_recent = len(bars) - 1 - i
    return largest, most_recent


def _streak(closes: list[float], down: bool) -> int:
    """Consecutive closes lower (or higher) than the one before."""
    count = 0
    for i in range(len(closes) - 1, 0, -1):
        moved_down = closes[i] < closes[i - 1]
        if moved_down == down:
            count += 1
        else:
            break
    return count
