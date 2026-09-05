"""Position sizing and portfolio heat.

The setup rules decide *what* looks good; this module decides *how much*,
which is the half that actually determines whether a run of losers is a
drawdown or a disaster. Size comes from the distance to the stop, then gets
clipped by three independent caps: single-name notional, share of the
average day's volume, and total open risk across the book.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from swingtrader.config import Config
from swingtrader.regime import MarketContext
from swingtrader.setups import SetupResult
from swingtrader.snapshot import Snapshot


@dataclass
class TradePlan:
    """A sized, executable expression of one setup."""

    symbol: str
    setup: str
    direction: str
    entry: float
    stop: float
    target: float
    shares: int
    notional: float
    risk_amount: float
    risk_pct_of_equity: float
    reward_risk: float | None
    entry_style: str
    max_hold_days: int
    adv_participation_pct: float | None = None
    size_caps_applied: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def tradeable(self) -> bool:
        return self.shares > 0

    @property
    def stop_distance_pct(self) -> float:
        return 0.0 if self.entry <= 0 else 100.0 * abs(self.entry - self.stop) / self.entry


def size_trade(
    result: SetupResult,
    snap: Snapshot,
    cfg: Config,
    ctx: MarketContext,
    open_risk_used: float = 0.0,
) -> TradePlan:
    """Turn a setup into a share count, applying every cap in turn."""
    acct = cfg.account
    equity = acct.equity
    caps: list[str] = []
    warnings: list[str] = []

    regime_factor = ctx.size_factor(cfg.regime.risk_off_action, cfg.regime.reduce_factor)
    if regime_factor < 1.0:
        caps.append(
            f"risk-off regime: size scaled to {regime_factor:.0%} of normal"
        )

    budget = equity * (acct.risk_per_trade_pct / 100.0) * regime_factor

    # Portfolio heat: never let today's ideas push total open risk past the cap.
    heat_cap = equity * (acct.max_open_risk_pct / 100.0)
    remaining = max(0.0, heat_cap - open_risk_used)
    if budget > remaining:
        budget = remaining
        caps.append(
            f"portfolio heat cap: only {remaining:,.0f} of risk budget left "
            f"({acct.max_open_risk_pct:.1f}% of equity)"
        )

    risk_per_share = result.risk_per_share
    if risk_per_share <= 0 or budget <= 0:
        return _empty_plan(result, cfg, caps, warnings + ["no risk budget available"])

    shares = math.floor(budget / risk_per_share)

    # Cap 1: single-name notional.
    max_notional = equity * (acct.max_position_pct / 100.0)
    if result.entry > 0:
        by_notional = math.floor(max_notional / result.entry)
        if by_notional < shares:
            shares = by_notional
            caps.append(
                f"position cap: {acct.max_position_pct:.0f}% of equity "
                f"({max_notional:,.0f})"
            )

    # Cap 2: liquidity. Being a large share of a day's volume means the exit
    # is theoretical rather than real.
    participation = None
    if snap.avg_dollar_volume and snap.close > 0:
        adv_shares = snap.avg_dollar_volume / snap.close
        by_liquidity = math.floor(adv_shares * (acct.max_adv_participation_pct / 100.0))
        if by_liquidity < shares:
            shares = by_liquidity
            caps.append(
                f"liquidity cap: {acct.max_adv_participation_pct:.1f}% of the "
                f"20-day average volume"
            )
        if shares > 0:
            participation = 100.0 * shares / adv_shares

    shares = max(0, shares)
    risk_amount = shares * risk_per_share
    notional = shares * result.entry

    if shares == 0:
        warnings.append(
            "sized to zero -- the stop is too wide for the risk budget, or the "
            "name is too thin to trade at this size"
        )
    if result.stop <= 0:
        warnings.append("computed stop is non-positive; check the ATR inputs")

    stop_pct = 100.0 * risk_per_share / result.entry if result.entry else 0.0
    if stop_pct > 15.0:
        warnings.append(f"stop sits {stop_pct:.1f}% away -- unusually wide for a swing hold")

    return TradePlan(
        symbol=result.symbol,
        setup=result.name,
        direction=result.direction,
        entry=result.entry,
        stop=result.stop,
        target=result.target,
        shares=shares,
        notional=notional,
        risk_amount=risk_amount,
        risk_pct_of_equity=100.0 * risk_amount / equity if equity else 0.0,
        reward_risk=result.reward_risk,
        entry_style=result.entry_style,
        max_hold_days=result.max_hold_days,
        adv_participation_pct=participation,
        size_caps_applied=caps,
        warnings=warnings,
    )


def _empty_plan(
    result: SetupResult, cfg: Config, caps: list[str], warnings: list[str]
) -> TradePlan:
    return TradePlan(
        symbol=result.symbol,
        setup=result.name,
        direction=result.direction,
        entry=result.entry,
        stop=result.stop,
        target=result.target,
        shares=0,
        notional=0.0,
        risk_amount=0.0,
        risk_pct_of_equity=0.0,
        reward_risk=result.reward_risk,
        entry_style=result.entry_style,
        max_hold_days=result.max_hold_days,
        size_caps_applied=caps,
        warnings=warnings,
    )


def portfolio_summary(plans: list[TradePlan], cfg: Config) -> dict[str, float]:
    """Aggregate exposure figures for the top of the brief."""
    equity = cfg.account.equity or 1.0
    total_risk = sum(p.risk_amount for p in plans)
    total_notional = sum(p.notional for p in plans)
    return {
        "ideas": float(len(plans)),
        "total_risk": total_risk,
        "total_risk_pct": 100.0 * total_risk / equity,
        "total_notional": total_notional,
        "gross_exposure_pct": 100.0 * total_notional / equity,
        "heat_cap_pct": cfg.account.max_open_risk_pct,
        "heat_headroom_pct": max(0.0, cfg.account.max_open_risk_pct - 100.0 * total_risk / equity),
    }
