"""Technical indicators, implemented in pure Python.

No pandas/numpy on purpose: the scheduled job must run on a stock Python
image without a build toolchain. Every function takes a plain list of floats
(oldest first) and returns a list of the same length, with `None` in the
warm-up positions so indices always line up with the price series.
"""

from __future__ import annotations

from typing import Sequence

Series = list[float | None]


def _f(values: Sequence[float]) -> list[float]:
    return [float(v) for v in values]


def sma(values: Sequence[float], period: int) -> Series:
    """Simple moving average."""
    vals = _f(values)
    out: Series = [None] * len(vals)
    if period <= 0 or len(vals) < period:
        return out
    window = sum(vals[:period])
    out[period - 1] = window / period
    for i in range(period, len(vals)):
        window += vals[i] - vals[i - period]
        out[i] = window / period
    return out


def ema(values: Sequence[float], period: int) -> Series:
    """Exponential moving average, seeded with an SMA of the first `period`."""
    vals = _f(values)
    out: Series = [None] * len(vals)
    if period <= 0 or len(vals) < period:
        return out
    k = 2.0 / (period + 1.0)
    prev = sum(vals[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(vals)):
        prev = (vals[i] - prev) * k + prev
        out[i] = prev
    return out


def rsi(closes: Sequence[float], period: int = 14) -> Series:
    """Relative Strength Index using Wilder's smoothing."""
    vals = _f(closes)
    out: Series = [None] * len(vals)
    if len(vals) <= period:
        return out

    gains = losses = 0.0
    for i in range(1, period + 1):
        delta = vals[i] - vals[i - 1]
        gains += max(delta, 0.0)
        losses += max(-delta, 0.0)
    avg_gain = gains / period
    avg_loss = losses / period
    out[period] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)

    for i in range(period + 1, len(vals)):
        delta = vals[i] - vals[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(delta, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-delta, 0.0)) / period
        out[i] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return out


def true_range(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]) -> Series:
    """True range: max(H-L, |H-Cprev|, |L-Cprev|)."""
    h, l, c = _f(highs), _f(lows), _f(closes)
    out: Series = [None] * len(c)
    for i in range(1, len(c)):
        out[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    return out


def atr(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 14
) -> Series:
    """Average True Range (Wilder)."""
    tr = true_range(highs, lows, closes)
    out: Series = [None] * len(tr)
    vals = [v for v in tr if v is not None]
    if len(vals) < period:
        return out

    first = period  # index of the last TR in the seed window (tr[0] is None)
    seed = sum(v for v in tr[1 : period + 1] if v is not None) / period
    out[first] = seed
    prev = seed
    for i in range(first + 1, len(tr)):
        cur = tr[i]
        if cur is None:
            continue
        prev = (prev * (period - 1) + cur) / period
        out[i] = prev
    return out


def adx(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 14
) -> Series:
    """Average Directional Index -- trend strength, direction-agnostic."""
    h, l, c = _f(highs), _f(lows), _f(closes)
    n = len(c)
    out: Series = [None] * n
    if n < period * 2 + 1:
        return out

    tr = true_range(h, l, c)
    plus_dm: list[float] = [0.0] * n
    minus_dm: list[float] = [0.0] * n
    for i in range(1, n):
        up, down = h[i] - h[i - 1], l[i - 1] - l[i]
        plus_dm[i] = up if (up > down and up > 0) else 0.0
        minus_dm[i] = down if (down > up and down > 0) else 0.0

    atr_s = sum(v for v in tr[1 : period + 1] if v is not None)
    pdm_s = sum(plus_dm[1 : period + 1])
    mdm_s = sum(minus_dm[1 : period + 1])

    dx: list[float | None] = [None] * n
    for i in range(period + 1, n):
        cur_tr = tr[i] or 0.0
        atr_s = atr_s - atr_s / period + cur_tr
        pdm_s = pdm_s - pdm_s / period + plus_dm[i]
        mdm_s = mdm_s - mdm_s / period + minus_dm[i]
        if atr_s == 0:
            continue
        pdi = 100.0 * pdm_s / atr_s
        mdi = 100.0 * mdm_s / atr_s
        denom = pdi + mdi
        dx[i] = 0.0 if denom == 0 else 100.0 * abs(pdi - mdi) / denom

    ready = [i for i, v in enumerate(dx) if v is not None]
    if len(ready) < period:
        return out
    start = ready[period - 1]
    prev = sum(dx[i] or 0.0 for i in ready[:period]) / period
    out[start] = prev
    for i in range(start + 1, n):
        if dx[i] is None:
            continue
        prev = (prev * (period - 1) + (dx[i] or 0.0)) / period
        out[i] = prev
    return out


def macd(
    closes: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[Series, Series, Series]:
    """MACD line, signal line, histogram."""
    ef, es = ema(closes, fast), ema(closes, slow)
    line: Series = [
        (a - b) if (a is not None and b is not None) else None for a, b in zip(ef, es)
    ]
    dense = [v for v in line if v is not None]
    sig: Series = [None] * len(line)
    if len(dense) >= signal:
        offset = len(line) - len(dense)
        for i, v in enumerate(ema(dense, signal)):
            sig[offset + i] = v
    hist: Series = [
        (a - b) if (a is not None and b is not None) else None for a, b in zip(line, sig)
    ]
    return line, sig, hist


def bollinger(
    closes: Sequence[float], period: int = 20, mult: float = 2.0
) -> tuple[Series, Series, Series]:
    """Bollinger bands: (upper, middle, lower)."""
    vals = _f(closes)
    mid = sma(vals, period)
    upper: Series = [None] * len(vals)
    lower: Series = [None] * len(vals)
    for i in range(period - 1, len(vals)):
        m = mid[i]
        if m is None:
            continue
        window = vals[i - period + 1 : i + 1]
        var = sum((x - m) ** 2 for x in window) / period
        sd = var**0.5
        upper[i], lower[i] = m + mult * sd, m - mult * sd
    return upper, mid, lower


def donchian(
    highs: Sequence[float], lows: Sequence[float], period: int = 20
) -> tuple[Series, Series]:
    """Donchian channel highs/lows over the *prior* `period` bars (excludes today)."""
    h, l = _f(highs), _f(lows)
    up: Series = [None] * len(h)
    dn: Series = [None] * len(l)
    for i in range(period, len(h)):
        up[i] = max(h[i - period : i])
        dn[i] = min(l[i - period : i])
    return up, dn


def relative_volume(volumes: Sequence[float], period: int = 20) -> Series:
    """Today's volume divided by the average of the prior `period` sessions."""
    vols = _f(volumes)
    out: Series = [None] * len(vols)
    for i in range(period, len(vols)):
        avg = sum(vols[i - period : i]) / period
        out[i] = None if avg <= 0 else vols[i] / avg
    return out


def roc(values: Sequence[float], period: int) -> Series:
    """Rate of change in percent over `period` bars."""
    vals = _f(values)
    out: Series = [None] * len(vals)
    for i in range(period, len(vals)):
        base = vals[i - period]
        out[i] = None if base == 0 else (vals[i] / base - 1.0) * 100.0
    return out


def percent_rank(values: Sequence[float | None], lookback: int) -> Series:
    """Percentile (0-100) of each value within the trailing `lookback` window."""
    out: Series = [None] * len(values)
    for i in range(len(values)):
        cur = values[i]
        if cur is None or i < lookback:
            continue
        window = [v for v in values[i - lookback : i + 1] if v is not None]
        if len(window) < 2:
            continue
        below = sum(1 for v in window if v < cur)
        out[i] = 100.0 * below / (len(window) - 1)
    return out


def swing_low(lows: Sequence[float], lookback: int = 10) -> float | None:
    """Lowest low of the last `lookback` bars -- the structural stop reference."""
    if not lows:
        return None
    return min(_f(lows)[-lookback:])


def swing_high(highs: Sequence[float], lookback: int = 10) -> float | None:
    """Highest high of the last `lookback` bars."""
    if not highs:
        return None
    return max(_f(highs)[-lookback:])


def last(series: Sequence[float | None], offset: int = 0) -> float | None:
    """Value at `offset` bars back from the end, or None."""
    idx = len(series) - 1 - offset
    return series[idx] if 0 <= idx < len(series) else None
