"""Morningstar overlay.

Morningstar has no API on the retail Investor/Premium tier -- programmatic
access lives in Morningstar Direct Web Services, a separate enterprise
entitlement. Scraping a Premium session's HTML would breach the subscriber
agreement, so this adapter deliberately does not do that.

Two supported paths instead:

1. **Manual export (works on Premium today).** Export a screener or
   portfolio from Morningstar to CSV and drop it in the ingest directory.
   Column names are matched loosely so an unmodified export just works.
2. **Direct Web Services.** If the operator has Direct credentials, set
   MORNINGSTAR_DIRECT_TOKEN and the adapter reports as configured; wire the
   entitlement-specific endpoint into `fetch_direct` below.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

from swingtrader.providers.base import Fundamental, ProviderStatus

NAME = "morningstar"
INGEST_ENV = "MORNINGSTAR_INGEST_DIR"
TOKEN_ENV = "MORNINGSTAR_DIRECT_TOKEN"
DEFAULT_INGEST = Path("data/morningstar")

# Header -> field. Morningstar's exports vary by report, so match on a
# normalised (lowercased, punctuation-stripped) form of each header.
FIELD_ALIASES = {
    "ticker": "symbol",
    "symbol": "symbol",
    "starrating": "star_rating",
    "morningstarrating": "star_rating",
    "rating": "star_rating",
    "fairvalue": "fair_value",
    "fairvalueestimate": "fair_value",
    "fve": "fair_value",
    "pricefairvalue": "price_to_fair_value",
    "pricetofairvalue": "price_to_fair_value",
    "pfv": "price_to_fair_value",
    "economicmoat": "economic_moat",
    "moat": "economic_moat",
    "uncertainty": "uncertainty",
    "fairvalueuncertainty": "uncertainty",
    "sector": "sector",
    "industry": "industry",
    "marketcap": "market_cap",
}


def ingest_dir() -> Path:
    return Path(os.environ.get(INGEST_ENV) or DEFAULT_INGEST)


def status() -> ProviderStatus:
    if os.environ.get(TOKEN_ENV, "").strip():
        return ProviderStatus(
            NAME, "configured", "Direct Web Services token present",
            entitlement="Morningstar Direct",
        )
    directory = ingest_dir()
    files = sorted(directory.glob("*.csv")) if directory.exists() else []
    if files:
        newest = max(files, key=lambda p: p.stat().st_mtime)
        return ProviderStatus(
            NAME,
            "configured",
            f"manual export: {len(files)} file(s), newest {newest.name}",
            entitlement="Morningstar Premium (manual CSV export)",
        )
    return ProviderStatus(
        NAME,
        "unconfigured",
        f"no CSV in {directory}/ -- export a screener from Morningstar and drop it there "
        "(Premium has no API; Direct Web Services does)",
        entitlement="Morningstar Premium / Direct",
    )


def load_exports(directory: Path | str | None = None) -> dict[str, Fundamental]:
    """Read every CSV in the ingest directory into Fundamental overlays."""
    path = Path(directory) if directory else ingest_dir()
    if not path.exists():
        return {}

    out: dict[str, Fundamental] = {}
    for csv_path in sorted(path.glob("*.csv")):
        for row in _read_rows(csv_path):
            sym = (row.get("symbol") or "").strip().upper()
            if not sym:
                continue
            out[sym] = Fundamental(
                symbol=sym,
                source=f"{NAME}:{csv_path.name}",
                sector=row.get("sector"),
                industry=row.get("industry"),
                market_cap=_num(row.get("market_cap")),
                star_rating=_num(row.get("star_rating")),
                fair_value=_num(row.get("fair_value")),
                price_to_fair_value=_num(row.get("price_to_fair_value")),
                economic_moat=_clean(row.get("economic_moat")),
                uncertainty=_clean(row.get("uncertainty")),
                notes=[f"from {csv_path.name}"],
            )
    return out


def fetch_direct(symbols: list[str]) -> dict[str, Fundamental]:
    """Placeholder for Morningstar Direct Web Services.

    Direct endpoints and dataset ids differ per contract, so this is left as
    an explicit integration point rather than a guess that would fail at 6am.
    """
    if not os.environ.get(TOKEN_ENV, "").strip():
        raise RuntimeError(f"{TOKEN_ENV} is not set")
    raise NotImplementedError(
        "Wire your Morningstar Direct Web Services endpoint here -- the dataset "
        "ids are contract-specific. Until then the manual CSV export path is used."
    )


def _read_rows(path: Path) -> list[dict[str, str]]:
    """Read a CSV, tolerating the preamble rows Morningstar exports prepend."""
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return []

    lines = text.splitlines()
    header_idx = next(
        (
            i
            for i, line in enumerate(lines[:25])
            if any(key in _norm(line) for key in ("ticker", "symbol"))
        ),
        None,
    )
    if header_idx is None:
        return []

    reader = csv.DictReader(lines[header_idx:])
    rows: list[dict[str, str]] = []
    for raw in reader:
        mapped: dict[str, str] = {}
        for header, value in raw.items():
            if header is None:
                continue
            field = FIELD_ALIASES.get(_norm(header))
            if field and value is not None:
                mapped[field] = value.strip()
        if mapped.get("symbol"):
            rows.append(mapped)
    return rows


def _norm(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _clean(raw: str | None) -> str | None:
    if not raw:
        return None
    text = raw.strip()
    return text or None


def _num(raw: str | None) -> float | None:
    if not raw:
        return None
    text = raw.strip().replace(",", "").replace("$", "").replace("%", "")
    if text in ("-", "", "N/A", "—"):
        return None
    mult = 1.0
    if text and text[-1] in "KMBT":
        mult = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[text[-1]]
        text = text[:-1]
    try:
        return float(text) * mult
    except ValueError:
        return None
