"""Swing-trade setup rules.

Each rule is a small function that reads a `Snapshot` and returns a
`SetupResult` carrying a 0-100 conviction score, the entry/stop/target levels
implied by the rule, and the plain-English reasons behind the number. Nothing
here fetches data or sizes a position -- rules stay pure so they can be
unit-tested against fixed bars and reasoned about in isolation.

Scoring convention: hard *gates* decide whether a setup applies at all;
weighted *components* (each normalised to 0-1) decide how good an example of
it this is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from swingtrader.config import Config
from swingtrader.regime import MarketContext
from swingtrader.snapshot import Snapshot

ENTRY_BUFFER = 0.0015  # trigger a shade beyond the level so a touch is not a fill


@dataclass
class SetupResult:
    """One rule's verdict on one symbol."""

    name: str
    symbol: str
    direction: str                     # long | short
    score: float                       # 0-100
    entry: float
    stop: float
    target: float
    reasons: list[str] = field(default_factory=list)
    cautions: list[str] = field(default_factory=list)
    max_hold_days: int = 10
    entry_style: str = "stop"          # stop | limit

    @property
    def risk_per_share(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def reward_per_share(self) -> float:
        return abs(self.target - self.entry)

    @property
    def reward_risk(self) -> float | None:
        risk = self.risk_per_share
        return None if risk <= 0 else self.reward_per_share / risk


SetupFn = Callable[[Snapshot, MarketContext, Config], SetupResult | None]
_REGISTRY: dict[str, SetupFn] = {}


def register(name: str) -> Callable[[SetupFn], SetupFn]:
    def wrap(fn: SetupFn) -> SetupFn:
        _REGISTRY[name] = fn
        return fn

    return wrap


def available() -> list[str]:
    return sorted(_REGISTRY)


def evaluate(snap: Snapshot, ctx: MarketContext, cfg: Config) -> list[SetupResult]:
    """Run every enabled rule against one symbol, best score first."""
    results: list[SetupResult] = []
    for name in cfg.setups.enabled:
        fn = _REGISTRY.get(name)
        if fn is None:
            continue
        try:
            result = fn(snap, ctx, cfg)
        except Exception:  # one broken rule must not kill the whole brief
            continue
        if result is None:
            continue
        if result.direction == "short" and not cfg.setups.allow_shorts:
            continue
        if result.direction == "long" and not ctx.longs_allowed and cfg.regime.risk_off_action == "halt":
            continue
        if result.risk_per_share <= 0:
            continue
        results.append(result)
    return sorted(results, key=lambda r: r.score, reverse=True)


# --- scoring helpers ---------------------------------------------------------


def _ramp(value: float | None, lo: float, hi: float) -> float:
    """0 below `lo`, 1 above `hi`, linear in between. Handles hi < lo."""
    if value is None:
        return 0.0
    if hi == lo:
        return 1.0 if value >= hi else 0.0
    frac = (value - lo) / (hi - lo)
    return max(0.0, min(1.0, frac))


def _band(value: float | None, lo: float, best_lo: float, best_hi: float, hi: float) -> float:
    """Plateau at 1 between best_lo..best_hi, tapering to 0 at lo and hi."""
    if value is None:
        return 0.0
    if best_lo <= value <= best_hi:
        return 1.0
    if value < best_lo:
        return _ramp(value, lo, best_lo)
    return 1.0 - _ramp(value, best_hi, hi)


def _blend(parts: list[tuple[str, float, float]]) -> tuple[float, list[str]]:
    """Weighted average of (label, weight, value) -> (0-100 score, reasons)."""
    total_weight = sum(w for _, w, _ in parts) or 1.0
    score = 100.0 * sum(w * v for _, w, v in parts) / total_weight
    reasons = [label for label, _, value in parts if value >= 0.6 and label]
    return score, reasons


def _long_levels(snap: Snapshot, cfg: Config, trigger: float) -> tuple[float, float, float]:
    """Entry/stop/target for a long, using the tighter of ATR and structure."""
    atr = snap.atr14 or (snap.close * 0.02)
    entry = trigger * (1 + ENTRY_BUFFER)
    atr_stop = entry - cfg.setups.atr_stop_multiple * atr
    structural = snap.swing_low10
    # Prefer the structural low, but only while it is within a sane distance:
    # after a gap the 10-day low can sit 20% away, which is not a stop.
    if structural is not None and structural < entry and structural > entry - 2.5 * atr:
        stop = min(atr_stop, structural * (1 - ENTRY_BUFFER))
    else:
        stop = atr_stop
    target = entry + cfg.setups.reward_multiple * (entry - stop)
    return entry, stop, target


def _short_levels(snap: Snapshot, cfg: Config, trigger: float) -> tuple[float, float, float]:
    atr = snap.atr14 or (snap.close * 0.02)
    entry = trigger * (1 - ENTRY_BUFFER)
    atr_stop = entry + cfg.setups.atr_stop_multiple * atr
    structural = snap.swing_high10
    if structural is not None and structural > entry and structural < entry + 2.5 * atr:
        stop = max(atr_stop, structural * (1 + ENTRY_BUFFER))
    else:
        stop = atr_stop
    target = entry - cfg.setups.reward_multiple * (stop - entry)
    return entry, stop, target


def _relative_strength(snap: Snapshot, ctx: MarketContext) -> float | None:
    """Symbol 63-day return minus the benchmark's, in percentage points."""
    if snap.roc63 is None or ctx.roc63 is None:
        return None
    return snap.roc63 - ctx.roc63


# --- the rules ---------------------------------------------------------------


@register("trend_pullback")
def trend_pullback(snap: Snapshot, ctx: MarketContext, cfg: Config) -> SetupResult | None:
    """Buy the first pause in an established uptrend.

    The workhorse setup: an intact trend, a controlled pullback into the
    20-day average on lighter volume, momentum resetting rather than breaking.
    """
    if not snap.above_200 or snap.sma50 is None or snap.sma200 is None:
        return None
    if snap.sma50 <= snap.sma200 or not snap.sma50_rising:
        return None
    if (snap.adx14 or 0) < 15:
        return None

    dist_ema20 = snap.distance_pct(snap.ema20)
    if dist_ema20 is None or dist_ema20 > 8.0:
        return None  # not a pullback, it is an extension
    if (snap.rsi14 or 100) > 62:
        return None  # nothing has reset yet

    parts = [
        # Best when hugging the 20-day average from just above or just below.
        ("pulled back into the 20-day average", 2.0, _band(dist_ema20, -6.0, -1.5, 3.0, 8.0)),
        ("momentum reset without breaking", 1.5, _band(snap.rsi14, 25.0, 38.0, 55.0, 65.0)),
        ("trend strength intact (ADX)", 1.2, _ramp(snap.adx14, 15.0, 30.0)),
        ("50-day above a rising 200-day", 1.0, 1.0 if snap.sma200_rising else 0.4),
        # Volume drying up on the dip is the classic tell.
        ("selling dried up on the pullback", 1.0, 1.0 - _ramp(snap.rel_volume, 0.9, 1.8)),
        ("closed in the top half of the day", 0.8, _ramp(snap.close_position_in_range, 0.35, 0.8)),
        ("holding well above the 200-day", 0.8, _ramp(snap.distance_pct(snap.sma200), 2.0, 20.0)),
    ]
    rs = _relative_strength(snap, ctx)
    if rs is not None:
        parts.append((f"outperforming {ctx.benchmark} over 3 months", 1.0, _ramp(rs, -5.0, 15.0)))

    score, reasons = _blend(parts)
    entry, stop, target = _long_levels(snap, cfg, snap.high)

    cautions: list[str] = []
    if snap.prev_high > snap.high:
        cautions.append(
            f"inside day -- the prior session's high ({snap.prev_high:,.2f}) is the "
            "wider breakout level if this trigger fails"
        )
    if (snap.rsi14 or 50) < 35:
        cautions.append("RSI below 35 -- the pullback is deep enough to be a change of trend")
    if (snap.rel_volume or 0) > 1.8:
        cautions.append("heavy volume on the down move")

    return SetupResult(
        name="trend_pullback",
        symbol=snap.symbol,
        direction="long",
        score=score,
        entry=entry,
        stop=stop,
        target=target,
        reasons=reasons,
        cautions=cautions,
        max_hold_days=cfg.setups.max_hold_days,
        entry_style="stop",
    )


@register("breakout")
def breakout(snap: Snapshot, ctx: MarketContext, cfg: Config) -> SetupResult | None:
    """Buy a 20-day range breakout that arrives with volume after a squeeze."""
    if snap.donchian_high20 is None or snap.sma50 is None or snap.sma200 is None:
        return None
    if not (snap.close > snap.sma50 > snap.sma200):
        return None

    dist_to_high = snap.distance_pct(snap.donchian_high20)
    if dist_to_high is None or dist_to_high < -2.5:
        return None  # too far below the range high to call it a breakout

    # A breakout is worth more when the range that preceded it was tight.
    squeeze = snap.tightness

    parts = [
        ("pushing through the 20-day high", 2.0, _band(dist_to_high, -2.5, -0.3, 2.5, 6.0)),
        ("volume confirming the break", 1.8, _ramp(snap.rel_volume, 1.0, 2.0)),
        ("coiled before the break", 1.3, 1.0 - _ramp(squeeze, 0.5, 1.1)),
        ("near the 52-week high", 1.2, _ramp(snap.pct_from_52w_high, -25.0, -2.0)),
        ("closed strong on the day", 1.0, _ramp(snap.close_position_in_range, 0.4, 0.85)),
        ("trend structure aligned", 1.0, 1.0 if snap.stacked_bullish else 0.5),
        ("directional strength (ADX)", 0.8, _ramp(snap.adx14, 18.0, 35.0)),
    ]
    rs = _relative_strength(snap, ctx)
    if rs is not None:
        parts.append((f"leading {ctx.benchmark}", 1.0, _ramp(rs, 0.0, 20.0)))

    score, reasons = _blend(parts)
    entry, stop, target = _long_levels(snap, cfg, max(snap.high, snap.donchian_high20))

    cautions: list[str] = []
    if (snap.rel_volume or 0) < 1.1:
        cautions.append("breakout volume is unconvincing")
    if (snap.rsi14 or 0) > 78:
        cautions.append("RSI above 78 -- extended, expect a retest")

    return SetupResult(
        name="breakout",
        symbol=snap.symbol,
        direction="long",
        score=score,
        entry=entry,
        stop=stop,
        target=target,
        reasons=reasons,
        cautions=cautions,
        max_hold_days=cfg.setups.max_hold_days,
        entry_style="stop",
    )


@register("momentum_flag")
def momentum_flag(snap: Snapshot, ctx: MarketContext, cfg: Config) -> SetupResult | None:
    """Buy a tight consolidation inside a strong three-month advance."""
    if snap.roc63 is None or snap.roc63 <= 0 or snap.sma50 is None:
        return None
    if snap.close < snap.sma50:
        return None

    tightness = snap.tightness
    if tightness is None or tightness > 0.85:
        return None  # still moving, not consolidating

    dist_ema10 = snap.distance_pct(snap.ema10)
    parts = [
        ("strong three-month advance", 2.0, _ramp(snap.roc63, 8.0, 40.0)),
        ("range has gone tight", 1.8, 1.0 - _ramp(tightness, 0.35, 0.85)),
        ("riding the 10-day average", 1.3, _band(dist_ema10, -5.0, -1.0, 4.0, 9.0)),
        ("twelve-month trend confirms", 1.0, _ramp(snap.roc126, 0.0, 40.0)),
        ("volume quiet during the pause", 1.0, 1.0 - _ramp(snap.rel_volume, 0.8, 1.6)),
        ("momentum not yet exhausted", 0.9, _band(snap.rsi14, 40.0, 50.0, 70.0, 82.0)),
        # A flag that formed on the back of an earnings gap is post-event
        # drift; it behaves differently and deserves a lower score.
        ("advance is trend-driven, not gap-driven", 1.2, 0.25 if snap.event_gap else 1.0),
    ]
    rs = _relative_strength(snap, ctx)
    if rs is not None:
        parts.append((f"relative strength vs {ctx.benchmark}", 1.4, _ramp(rs, 0.0, 25.0)))

    score, reasons = _blend(parts)
    entry, stop, target = _long_levels(snap, cfg, snap.high)

    cautions: list[str] = []
    if snap.event_gap:
        cautions.append(
            f"a {snap.max_gap_pct_10d:.0f}% gap {snap.days_since_big_gap} sessions ago -- "
            "this is post-event drift, not trend continuation"
        )
    if snap.pct_from_52w_high is not None and snap.pct_from_52w_high < -20:
        cautions.append("well off the 52-week high despite the momentum reading")

    return SetupResult(
        name="momentum_flag",
        symbol=snap.symbol,
        direction="long",
        score=score,
        entry=entry,
        stop=stop,
        target=target,
        reasons=reasons,
        cautions=cautions,
        max_hold_days=cfg.setups.max_hold_days,
        entry_style="stop",
    )


@register("oversold_reversion")
def oversold_reversion(snap: Snapshot, ctx: MarketContext, cfg: Config) -> SetupResult | None:
    """Buy a short, sharp flush inside a longer uptrend.

    Shorter-fused than the others: the edge decays within a few sessions, so
    the hold is capped and the target is the mean rather than a 2R extension.
    """
    if not snap.above_200 or snap.rsi2 is None:
        return None
    if snap.rsi2 > 15 or snap.consecutive_down < 2:
        return None

    below_lower_band = None
    if snap.bb_lower:
        below_lower_band = 100.0 * (snap.bb_lower - snap.close) / snap.close

    parts = [
        ("short-term washout (RSI-2)", 2.0, 1.0 - _ramp(snap.rsi2, 0.0, 15.0)),
        ("stretched below the lower band", 1.5, _ramp(below_lower_band, -1.0, 3.0)),
        ("multi-day flush", 1.2, _ramp(float(snap.consecutive_down), 2.0, 5.0)),
        ("long-term trend still up", 1.5, _ramp(snap.distance_pct(snap.sma200), 0.0, 15.0)),
        ("still above the 50-day", 0.8, 1.0 if (snap.sma50 and snap.close > snap.sma50) else 0.2),
        ("volatility worth trading", 0.8, _band(snap.atr_pct, 1.0, 2.0, 6.0, 10.0)),
    ]
    score, reasons = _blend(parts)

    # Mean reversion enters into weakness, not on a breakout trigger.
    atr = snap.atr14 or (snap.close * 0.02)
    entry = snap.close
    stop = min(snap.close - cfg.setups.atr_stop_multiple * atr, (snap.swing_low10 or snap.close) * 0.995)
    target = snap.sma20 or snap.bb_mid or (entry + 2.0 * atr)
    if target <= entry:
        target = entry + 1.5 * atr

    cautions = ["mean-reversion setup: the edge fades fast, do not let it become a position"]
    if snap.sma50 and snap.close < snap.sma50:
        cautions.append("already below the 50-day -- weaker version of this setup")

    return SetupResult(
        name="oversold_reversion",
        symbol=snap.symbol,
        direction="long",
        score=score,
        entry=entry,
        stop=stop,
        target=target,
        reasons=reasons,
        cautions=cautions,
        max_hold_days=min(5, cfg.setups.max_hold_days),
        entry_style="limit",
    )


@register("short_breakdown")
def short_breakdown(snap: Snapshot, ctx: MarketContext, cfg: Config) -> SetupResult | None:
    """Mirror of the breakout, for a downtrend losing 20-day support."""
    if snap.donchian_low20 is None or snap.sma50 is None or snap.sma200 is None:
        return None
    if not (snap.close < snap.sma50 < snap.sma200):
        return None

    dist_to_low = snap.distance_pct(snap.donchian_low20)
    if dist_to_low is None or dist_to_low > 2.5:
        return None

    parts = [
        ("losing the 20-day low", 2.0, _band(dist_to_low, -6.0, -2.5, 0.3, 2.5)),
        ("volume confirming the break", 1.8, _ramp(snap.rel_volume, 1.0, 2.0)),
        ("closed weak on the day", 1.2, 1.0 - _ramp(snap.close_position_in_range, 0.15, 0.6)),
        ("downtrend structure aligned", 1.2, 1.0 if snap.close < snap.sma50 < snap.sma200 else 0.5),
        ("directional strength (ADX)", 0.9, _ramp(snap.adx14, 18.0, 35.0)),
        ("not yet washed out", 0.9, _ramp(snap.rsi14, 20.0, 40.0)),
    ]
    rs = _relative_strength(snap, ctx)
    if rs is not None:
        parts.append((f"lagging {ctx.benchmark}", 1.0, 1.0 - _ramp(rs, -20.0, 0.0)))

    score, reasons = _blend(parts)
    entry, stop, target = _short_levels(snap, cfg, min(snap.low, snap.donchian_low20))

    cautions = ["short: borrow availability and hard-to-borrow fees are not modelled here"]
    if ctx.regime == "risk_on":
        cautions.append(f"{ctx.benchmark} is in an uptrend -- shorting against the tape")

    return SetupResult(
        name="short_breakdown",
        symbol=snap.symbol,
        direction="short",
        score=score,
        entry=entry,
        stop=stop,
        target=target,
        reasons=reasons,
        cautions=cautions,
        max_hold_days=cfg.setups.max_hold_days,
        entry_style="stop",
    )
