"""Command line interface."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from swingtrader import __version__, engine, report, setups
from swingtrader.cache import BarCache
from swingtrader.config import Config
from swingtrader.providers import finviz, prices_yahoo
from swingtrader.regime import classify
from swingtrader.snapshot import build as build_snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="swingtrader",
        description="Daily swing-trade research brief from your own data sources.",
    )
    parser.add_argument("--version", action="version", version=f"swingtrader {__version__}")
    parser.add_argument("-c", "--config", default=None, help="path to the YAML config")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="build today's brief")
    run_p.add_argument("--count", type=int, default=None, help="override number of ideas")
    run_p.add_argument("--min-score", type=float, default=None, help="override score threshold")
    run_p.add_argument("--equity", type=float, default=None, help="override account equity")
    run_p.add_argument("--no-cache", action="store_true", help="ignore the local bar cache")
    run_p.add_argument("--dry-run", action="store_true", help="print to stdout, write nothing")
    run_p.add_argument("--quiet", action="store_true", help="only print the written paths")

    sub.add_parser("providers", help="show which data sources are usable right now")
    sub.add_parser("setups", help="list the available setup rules")

    ex_p = sub.add_parser("explain", help="score one symbol and show every rule's verdict")
    ex_p.add_argument("symbol")

    sc_p = sub.add_parser("screen", help="run the configured Finviz screens and list symbols")
    sc_p.add_argument("--limit", type=int, default=None)

    ca_p = sub.add_parser("cache", help="inspect or clear the local bar cache")
    ca_p.add_argument("action", choices=["clear", "info"])

    args = parser.parse_args(argv)

    try:
        cfg = Config.load(args.config)
    except (FileNotFoundError, ValueError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    handlers = {
        "run": lambda: _cmd_run(cfg, args),
        "providers": lambda: _cmd_providers(),
        "setups": lambda: _cmd_setups(cfg),
        "explain": lambda: _cmd_explain(cfg, args.symbol),
        "screen": lambda: _cmd_screen(cfg, args.limit),
        "cache": lambda: _cmd_cache(args.action),
    }
    return handlers[args.command]()


def _cmd_run(cfg: Config, args) -> int:
    if args.count is not None:
        cfg.output.count = args.count
    if args.min_score is not None:
        cfg.setups.min_score = args.min_score
    if args.equity is not None:
        cfg.account.equity = args.equity
        cfg.validate()

    brief = engine.run(cfg, use_cache=not args.no_cache)

    if args.dry_run:
        print(report.to_markdown(brief, cfg))
        return 0

    written = report.render_all(brief, cfg)
    if not args.quiet:
        print(report.to_markdown(brief, cfg))
        print("---")
    for fmt, path in written.items():
        print(f"{fmt:9s} {path}")
    return 0


def _cmd_providers() -> int:
    print("Data sources\n")
    for status in engine.provider_statuses():
        print(f"  {status.line()}")
        if status.entitlement:
            print(f"       {'':14s} needs: {status.entitlement}")
    print(
        "\nLegend: OK = working now · SET = credentials present · "
        "-- = not configured · XX = configured but unreachable"
    )
    return 0


def _cmd_setups(cfg: Config) -> int:
    enabled = set(cfg.setups.enabled)
    print("Setup rules\n")
    for name in setups.available():
        mark = "on " if name in enabled else "off"
        doc = (setups._REGISTRY[name].__doc__ or "").strip().splitlines()[0]
        print(f"  [{mark}] {name:<20} {doc}")
    print(f"\nMinimum score to make the brief: {cfg.setups.min_score:.0f}")
    return 0


def _cmd_explain(cfg: Config, symbol: str) -> int:
    symbol = symbol.upper()
    try:
        hist = prices_yahoo.fetch_history(symbol, cfg.universe.lookback_days)
    except Exception as exc:
        print(f"could not fetch {symbol}: {exc}", file=sys.stderr)
        return 1

    snap = build_snapshot(hist)
    if snap is None:
        print(f"{symbol}: not enough history", file=sys.stderr)
        return 1

    bench_snap = None
    try:
        bench_snap = build_snapshot(
            prices_yahoo.fetch_history(cfg.regime.benchmark, cfg.universe.lookback_days)
        )
    except Exception:
        pass
    ctx = classify(bench_snap, cfg.regime.benchmark)

    print(f"{symbol} — {snap.day.isoformat()} ({snap.bars} bars, {hist.exchange})\n")
    print(f"  {ctx.summary()}\n")
    rows = [
        ("close", f"{snap.close:,.2f}"),
        ("20 / 50 / 200 SMA", _triple(snap.sma20, snap.sma50, snap.sma200)),
        ("EMA 10 / 20", _triple(snap.ema10, snap.ema20, None)),
        ("RSI 14 / 2", _triple(snap.rsi14, snap.rsi2, None)),
        ("ATR14 (% of price)", f"{snap.atr14:,.2f} ({snap.atr_pct:.2f}%)" if snap.atr14 else "—"),
        ("ADX14", f"{snap.adx14:.1f}" if snap.adx14 else "—"),
        ("relative volume", f"{snap.rel_volume:.2f}x" if snap.rel_volume else "—"),
        ("20d avg dollar volume", f"{snap.avg_dollar_volume/1e6:,.1f}M" if snap.avg_dollar_volume else "—"),
        ("ROC 21 / 63 / 126", _triple(snap.roc21, snap.roc63, snap.roc126)),
        ("from 52w high", f"{snap.pct_from_52w_high:+.1f}%" if snap.pct_from_52w_high is not None else "—"),
        ("20d Donchian hi / lo", _triple(snap.donchian_high20, snap.donchian_low20, None)),
        ("streak up / down", f"{snap.consecutive_up} / {snap.consecutive_down}"),
    ]
    for label, value in rows:
        print(f"  {label:<24} {value}")

    reason = engine.passes_filters(snap, cfg, None, snap.day)
    print(f"\n  hard filters: {'PASS' if reason is None else 'REJECT — ' + reason}\n")

    results = setups.evaluate(snap, ctx, cfg)
    if not results:
        print("  no setup rule fired")
        return 0

    print("Setup scores\n")
    for res in results:
        flag = "✓" if res.score >= cfg.setups.min_score else " "
        rr = f"  R:R {res.reward_risk:.1f}" if res.reward_risk else ""
        print(f"  {flag} {res.name:<20} {res.score:5.1f}  entry {res.entry:,.2f}  "
              f"stop {res.stop:,.2f}  target {res.target:,.2f}{rr}")
        for reason_text in res.reasons:
            print(f"      + {reason_text}")
        for caution in res.cautions:
            print(f"      ! {caution}")
    return 0


def _cmd_screen(cfg: Config, limit: int | None) -> int:
    if not finviz.token():
        print(finviz.status().detail, file=sys.stderr)
        return 1
    total = 0
    for screen in cfg.universe.finviz_screens:
        try:
            rows = finviz.screen(
                filters=screen.filters,
                order=screen.order,
                signal=screen.signal,
                limit=limit or screen.limit,
            )
        except Exception as exc:
            print(f"{screen.name}: FAILED — {exc}", file=sys.stderr)
            continue
        symbols = finviz.symbols(rows)
        total += len(symbols)
        print(f"{screen.name} ({screen.filters}) — {len(symbols)} symbols")
        print("  " + " ".join(symbols))
    print(f"\n{total} symbols across {len(cfg.universe.finviz_screens)} screen(s)")
    return 0


def _cmd_cache(action: str) -> int:
    cache = BarCache()
    if action == "clear":
        print(f"removed {cache.clear()} cached file(s) from {cache.dir}")
        return 0
    files = sorted(cache.dir.glob("*.json")) if cache.dir.exists() else []
    size = sum(p.stat().st_size for p in files)
    print(f"{cache.dir}: {len(files)} symbol(s), {size/1024:,.0f} KB")
    return 0


def _triple(a: float | None, b: float | None, c: float | None) -> str:
    parts = [f"{v:,.2f}" if v is not None else "—" for v in (a, b, c) if v is not None or True]
    return " / ".join(p for p, v in zip(parts, (a, b, c)) if v is not None) or "—"


if __name__ == "__main__":
    raise SystemExit(main())
