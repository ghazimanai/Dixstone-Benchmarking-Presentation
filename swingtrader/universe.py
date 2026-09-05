"""Candidate universe assembly.

Two sources feed the funnel: a hand-kept watchlist (names the operator
already follows) and Finviz Elite screens (broad discovery). Everything is
deduplicated and capped so a runaway screener cannot turn the morning job
into a thousand-request crawl.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from swingtrader.config import Config
from swingtrader.providers import finviz
from swingtrader.providers.base import Fundamental


@dataclass
class Universe:
    symbols: list[str] = field(default_factory=list)
    fundamentals: dict[str, Fundamental] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.symbols)


def build(cfg: Config) -> Universe:
    """Assemble today's candidate list."""
    uni = Universe()
    seen: set[str] = set()
    excluded = set(cfg.filters.exclude_symbols)

    def add(symbols: list[str], source: str) -> int:
        added = 0
        for raw in symbols:
            sym = raw.strip().upper()
            if not sym or sym in seen or sym in excluded:
                continue
            seen.add(sym)
            uni.symbols.append(sym)
            added += 1
        if added:
            uni.sources.append(f"{source}: {added} symbols")
        return added

    add(cfg.universe.watchlist, "watchlist")

    if cfg.universe.finviz_screens:
        if not finviz.token():
            uni.notes.append(
                f"{len(cfg.universe.finviz_screens)} Finviz screen(s) skipped -- "
                "FINVIZ_AUTH_TOKEN is not set, so only the watchlist is in play"
            )
        else:
            for screen in cfg.universe.finviz_screens:
                try:
                    rows = finviz.screen(
                        filters=screen.filters,
                        order=screen.order,
                        signal=screen.signal,
                        limit=screen.limit,
                    )
                except Exception as exc:
                    uni.notes.append(f"Finviz screen '{screen.name}' failed: {exc}")
                    continue
                add(finviz.symbols(rows), f"finviz:{screen.name}")
                # Merge rather than update: a symbol appearing in two screens
                # must not lose fields to whichever screen happened to run last.
                uni.fundamentals = merge_fundamentals(
                    uni.fundamentals, finviz.to_fundamentals(rows)
                )

    if len(uni.symbols) > cfg.universe.max_symbols:
        dropped = len(uni.symbols) - cfg.universe.max_symbols
        uni.symbols = uni.symbols[: cfg.universe.max_symbols]
        uni.notes.append(
            f"universe truncated to {cfg.universe.max_symbols} symbols ({dropped} dropped)"
        )

    return uni


def merge_fundamentals(
    *layers: dict[str, Fundamental]
) -> dict[str, Fundamental]:
    """Combine overlays, later layers filling gaps rather than overwriting.

    Order matters: pass the least authoritative source first. A field already
    populated by an earlier layer is only replaced when it is empty, so a
    Bloomberg target price will not be silently clobbered by a screener's.
    """
    merged: dict[str, Fundamental] = {}
    for layer in layers:
        for sym, fund in layer.items():
            existing = merged.get(sym)
            if existing is None:
                merged[sym] = Fundamental(**{**fund.__dict__, "notes": list(fund.notes)})
                continue
            for key, value in fund.__dict__.items():
                if key in ("symbol", "source", "notes"):
                    continue
                if getattr(existing, key, None) in (None, "") and value not in (None, ""):
                    setattr(existing, key, value)
            existing.source = f"{existing.source}+{fund.source}"
            existing.notes.extend(fund.notes)
    return merged
