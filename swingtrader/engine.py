"""Orchestration: universe -> data -> filters -> setups -> sized plans.

This is the only module that knows the whole pipeline. Everything it calls
is independently testable, and every drop-out along the way is recorded
rather than swallowed, so the brief can explain why a name the operator
expected to see is missing.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime

from swingtrader.cache import BarCache
from swingtrader.config import Config
from swingtrader.providers import bloomberg, finviz, lseg, morningstar, prices_yahoo
from swingtrader.providers.base import Fundamental, History, ProviderStatus
from swingtrader.regime import MarketContext, classify
from swingtrader.risk import TradePlan, portfolio_summary, size_trade
from swingtrader.setups import SetupResult, evaluate
from swingtrader.snapshot import Snapshot, build as build_snapshot
from swingtrader.universe import Universe, build as build_universe, merge_fundamentals

FETCH_DELAY_SECONDS = 0.15  # polite throttle; Yahoo rate-limits per source IP


@dataclass
class Candidate:
    """One symbol that cleared the filters, with its best setup and size."""

    symbol: str
    snapshot: Snapshot
    setup: SetupResult
    plan: TradePlan
    alternatives: list[SetupResult] = field(default_factory=list)
    fundamental: Fundamental | None = None
    overlay_notes: list[str] = field(default_factory=list)


@dataclass
class Brief:
    """Everything the report needs, and nothing it has to recompute."""

    as_of: date
    generated_at: datetime
    context: MarketContext
    # The session the bars come from. Run on a Monday and this is Friday --
    # every level in the brief is computed off that close, not off today.
    data_session: date | None = None
    picks: list[Candidate] = field(default_factory=list)
    considered: int = 0
    rejections: list[tuple[str, str]] = field(default_factory=list)
    near_misses: list[tuple[str, str, float]] = field(default_factory=list)
    providers: list[ProviderStatus] = field(default_factory=list)
    universe: Universe | None = None
    portfolio: dict[str, float] = field(default_factory=dict)
    fetch_errors: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def provider_statuses() -> list[ProviderStatus]:
    return [
        prices_yahoo.status(),
        finviz.status(),
        morningstar.status(),
        lseg.status(),
        bloomberg.status(),
    ]


def fetch_histories(
    symbols: list[str],
    lookback_days: int,
    cache: BarCache | None = None,
    delay: float = FETCH_DELAY_SECONDS,
) -> tuple[dict[str, History], dict[str, str]]:
    """Fetch daily bars, preferring cache, recording per-symbol failures."""
    out: dict[str, History] = {}
    errors: dict[str, str] = {}
    for sym in symbols:
        if cache is not None:
            cached = cache.get(sym)
            if cached is not None and len(cached) >= 30:
                out[sym] = cached
                continue
        try:
            hist = prices_yahoo.fetch_history(sym, lookback_days)
        except Exception as exc:
            errors[sym] = str(exc)
            continue
        out[sym] = hist
        if cache is not None:
            try:
                cache.put(hist)
            except OSError:
                pass  # a broken cache must not stop the brief
        if delay:
            time.sleep(delay)
    return out, errors


def load_overlays(symbols: list[str], screener: dict[str, Fundamental]) -> tuple[
    dict[str, Fundamental], list[str]
]:
    """Merge every configured fundamental source, least authoritative first."""
    notes: list[str] = []
    layers: list[dict[str, Fundamental]] = [screener]

    ms = morningstar.load_exports()
    if ms:
        notes.append(f"Morningstar overlay: {len(ms)} symbols from manual export")
        layers.append(ms)

    if lseg.status().usable:
        try:
            data = lseg.fetch(symbols)
            notes.append(f"LSEG overlay: {len(data)} symbols")
            layers.append(data)
        except Exception as exc:
            notes.append(f"LSEG overlay unavailable: {exc}")

    if bloomberg.status().usable:
        try:
            data = bloomberg.fetch(symbols)
            notes.append(f"Bloomberg overlay: {len(data)} symbols")
            layers.append(data)
        except Exception as exc:
            notes.append(f"Bloomberg overlay unavailable: {exc}")

    return merge_fundamentals(*layers), notes


def passes_filters(
    snap: Snapshot, cfg: Config, fund: Fundamental | None, today: date
) -> str | None:
    """Return a rejection reason, or None when the name is eligible."""
    f = cfg.filters
    if snap.bars < f.min_bars:
        return f"only {snap.bars} bars of history (need {f.min_bars})"
    if snap.close < f.min_price:
        return f"price {snap.close:,.2f} below minimum {f.min_price:,.2f}"
    if f.max_price is not None and snap.close > f.max_price:
        return f"price {snap.close:,.2f} above maximum {f.max_price:,.2f}"
    if snap.avg_dollar_volume is None:
        return "no volume history to judge liquidity"
    if snap.avg_dollar_volume < f.min_avg_dollar_volume:
        return (
            f"20-day dollar volume {snap.avg_dollar_volume/1e6:,.1f}M below "
            f"{f.min_avg_dollar_volume/1e6:,.1f}M"
        )
    atr_pct = snap.atr_pct
    if atr_pct is None:
        return "ATR unavailable"
    if atr_pct < f.min_atr_pct:
        return f"ATR {atr_pct:.1f}% of price -- too quiet to pay for the spread"
    if atr_pct > f.max_atr_pct:
        return f"ATR {atr_pct:.1f}% of price -- too volatile to size sensibly"
    if fund and fund.earnings_date and f.earnings_blackout_days >= 0:
        days = (fund.earnings_date - today).days
        if 0 <= days <= f.earnings_blackout_days:
            return f"earnings in {days} day(s) ({fund.earnings_date.isoformat()})"
    return None


def run(cfg: Config, use_cache: bool = True, cache_dir: str | None = None) -> Brief:
    """Produce today's brief end to end."""
    cache = BarCache(cache_dir) if (use_cache and cache_dir) else (BarCache() if use_cache else None)
    brief = Brief(
        as_of=date.today(),
        generated_at=datetime.now(),
        context=MarketContext(benchmark=cfg.regime.benchmark),
        providers=provider_statuses(),
    )

    uni = build_universe(cfg)
    brief.universe = uni
    brief.warnings.extend(uni.notes)
    if not uni.symbols:
        brief.warnings.append("universe is empty -- nothing to evaluate")
        return brief

    # Benchmark first: the regime gates everything downstream.
    bench_hist, bench_err = fetch_histories(
        [cfg.regime.benchmark], cfg.universe.lookback_days, cache
    )
    bench_snap = None
    if cfg.regime.benchmark in bench_hist:
        bench_snap = build_snapshot(bench_hist[cfg.regime.benchmark])
    else:
        brief.warnings.append(
            f"benchmark {cfg.regime.benchmark} unavailable: "
            f"{bench_err.get(cfg.regime.benchmark, 'unknown error')}"
        )
    brief.context = classify(bench_snap, cfg.regime.benchmark)
    brief.warnings.extend(brief.context.notes)
    if bench_snap is not None:
        brief.data_session = bench_snap.day

    histories, errors = fetch_histories(uni.symbols, cfg.universe.lookback_days, cache)
    brief.fetch_errors = errors
    for sym, err in errors.items():
        brief.rejections.append((sym, f"price history unavailable: {err}"))

    overlays, overlay_notes = load_overlays(list(histories), uni.fundamentals)
    brief.warnings.extend(overlay_notes)

    scored: list[tuple[Snapshot, SetupResult, list[SetupResult]]] = []
    for sym, hist in histories.items():
        snap = build_snapshot(hist)
        if snap is None:
            brief.rejections.append((sym, "not enough bars to build a snapshot"))
            continue
        brief.considered += 1
        if brief.data_session is None or snap.day > brief.data_session:
            brief.data_session = snap.day

        reason = passes_filters(snap, cfg, overlays.get(sym), brief.as_of)
        if reason:
            brief.rejections.append((sym, reason))
            continue

        results = evaluate(snap, brief.context, cfg)
        if not results:
            brief.rejections.append((sym, "no setup rule fired"))
            continue

        best = results[0]
        if best.score < cfg.setups.min_score:
            brief.near_misses.append((sym, best.name, best.score))
            continue
        scored.append((snap, best, results[1:]))

    scored.sort(key=lambda row: row[1].score, reverse=True)

    open_risk = 0.0
    for snap, best, alts in scored:
        if len(brief.picks) >= cfg.output.count:
            break
        plan = size_trade(best, snap, cfg, brief.context, open_risk_used=open_risk)
        if not plan.tradeable:
            brief.rejections.append((snap.symbol, plan.warnings[0] if plan.warnings else "sized to zero"))
            continue
        open_risk += plan.risk_amount
        fund = overlays.get(snap.symbol)
        brief.picks.append(
            Candidate(
                symbol=snap.symbol,
                snapshot=snap,
                setup=best,
                plan=plan,
                alternatives=alts,
                fundamental=fund,
                overlay_notes=_overlay_notes(fund, snap),
            )
        )

    brief.portfolio = portfolio_summary([c.plan for c in brief.picks], cfg)
    brief.near_misses.sort(key=lambda row: row[2], reverse=True)
    return brief


def _overlay_notes(fund: Fundamental | None, snap: Snapshot) -> list[str]:
    """Turn whatever fundamental data arrived into short, honest lines."""
    if fund is None:
        return ["no fundamental overlay -- price and volume only"]

    notes: list[str] = []
    if fund.star_rating is not None:
        notes.append(f"Morningstar rating {fund.star_rating:.0f}/5")
    if fund.price_to_fair_value is not None:
        notes.append(f"price/fair value {fund.price_to_fair_value:.2f}")
    elif fund.fair_value:
        notes.append(
            f"fair value {fund.fair_value:,.2f} "
            f"({100.0 * (snap.close / fund.fair_value - 1):+.0f}% vs last)"
        )
    if fund.economic_moat:
        notes.append(f"moat: {fund.economic_moat.lower()}")
    if fund.analyst_mean_target:
        notes.append(
            f"consensus target {fund.analyst_mean_target:,.2f} "
            f"({100.0 * (fund.analyst_mean_target / snap.close - 1):+.0f}%)"
        )
    if fund.analyst_rating:
        notes.append(f"consensus rating {fund.analyst_rating}")
    if fund.earnings_date:
        notes.append(f"earnings {fund.earnings_date.isoformat()}")
    else:
        notes.append("earnings date unknown -- confirm before entering")
    if fund.short_float:
        notes.append(f"short float {100.0 * fund.short_float:.1f}%")
    return notes or ["no fundamental overlay -- price and volume only"]


__all__ = ["Brief", "Candidate", "run", "provider_statuses", "fetch_histories", "passes_filters"]
