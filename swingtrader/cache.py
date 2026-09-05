"""On-disk cache for daily bars.

A scheduled job that re-runs (retry, second look at midday, a backtest of
yesterday's brief) should not re-hit a rate-limited endpoint for history it
already has. Bars are immutable once the session closes, so caching them by
symbol and keeping the newest trading day as the freshness key is safe.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import date, datetime
from pathlib import Path

from swingtrader.providers.base import Bar, History

DEFAULT_DIR = Path(os.environ.get("SWINGTRADER_CACHE", ".swingtrader-cache"))


class BarCache:
    """JSON-per-symbol cache of daily bars."""

    def __init__(self, directory: Path | str = DEFAULT_DIR, max_age_hours: float = 12.0):
        self.dir = Path(directory)
        self.max_age_seconds = max_age_hours * 3600.0

    def _path(self, symbol: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-._" else "_" for ch in symbol.upper())
        return self.dir / f"{safe}.json"

    def get(self, symbol: str) -> History | None:
        path = self._path(symbol)
        try:
            if time.time() - path.stat().st_mtime > self.max_age_seconds:
                return None
            raw = json.loads(path.read_text())
        except (OSError, ValueError):
            return None

        try:
            bars = [
                Bar(
                    day=date.fromisoformat(b["d"]),
                    open=b["o"],
                    high=b["h"],
                    low=b["l"],
                    close=b["c"],
                    volume=b["v"],
                )
                for b in raw["bars"]
            ]
        except (KeyError, TypeError, ValueError):
            return None
        if not bars:
            return None
        return History(
            symbol=raw.get("symbol", symbol.upper()),
            bars=bars,
            currency=raw.get("currency", "USD"),
            exchange=raw.get("exchange", ""),
            source=f"{raw.get('source', 'cache')}+cache",
        )

    def put(self, hist: History) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "symbol": hist.symbol,
            "currency": hist.currency,
            "exchange": hist.exchange,
            "source": hist.source,
            "written": datetime.now().isoformat(timespec="seconds"),
            "bars": [
                {"d": b.day.isoformat(), "o": b.open, "h": b.high, "l": b.low,
                 "c": b.close, "v": b.volume}
                for b in hist.bars
            ],
        }
        # Atomic replace so a killed job never leaves a half-written cache file.
        fd, tmp = tempfile.mkstemp(dir=str(self.dir), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(payload, fh, separators=(",", ":"))
            os.replace(tmp, self._path(hist.symbol))
        except Exception:
            Path(tmp).unlink(missing_ok=True)
            raise

    def clear(self) -> int:
        removed = 0
        if self.dir.exists():
            for path in self.dir.glob("*.json"):
                path.unlink()
                removed += 1
        return removed
