"""Reuters / LSEG adapter (Refinitiv Eikon, Workspace, RDP).

Reuters market data now sits behind LSEG. There are two ways in, and the
difference matters for a scheduled job:

* **Desktop session** (`lseg-data` / `refinitiv-data` / `eikon`) proxies
  through Workspace or Eikon running on the *same machine* on port 9000. It
  cannot work from a cloud container -- there is no desktop there to proxy
  to. Run the scheduler on the workstation for this path.
* **Platform session** (RDP / Delivery Platform) authenticates with machine
  credentials and does work headlessly, but it is a separate entitlement
  from a Workspace seat and most desktop subscriptions do not include it.

Set LSEG_APP_KEY (+ LSEG_MACHINE_ID / LSEG_MACHINE_SECRET for a platform
session). Redistribution of LSEG content is contractually restricted -- the
daily brief keeps LSEG-sourced values in the private report only.
"""

from __future__ import annotations

import os
from datetime import date, datetime

from swingtrader.providers.base import Fundamental, ProviderStatus

NAME = "lseg-reuters"
APP_KEY_ENV = "LSEG_APP_KEY"
MACHINE_ID_ENV = "LSEG_MACHINE_ID"
MACHINE_SECRET_ENV = "LSEG_MACHINE_SECRET"

# Fields the swing brief actually uses. Everything else stays in the terminal.
FIELDS = [
    "TR.PriceTargetMean",
    "TR.RecMean",
    "TR.RecLabel",
    "TR.ExpectedReportDate",
    "TR.GICSSector",
    "TR.CompanyMarketCap",
]


def _library():
    """Import whichever LSEG client is installed, newest first."""
    for module in ("lseg.data", "refinitiv.data"):
        try:
            return __import__(module, fromlist=["*"]), module
        except ImportError:
            continue
    try:
        import eikon  # noqa: F401

        return eikon, "eikon"
    except ImportError:
        return None, ""


def status() -> ProviderStatus:
    lib, module = _library()
    if lib is None:
        return ProviderStatus(
            NAME,
            "unconfigured",
            "pip install lseg-data (needs LSEG Workspace running locally, or "
            "RDP machine credentials for a headless session)",
            entitlement="LSEG Workspace / Eikon / RDP",
        )
    if not os.environ.get(APP_KEY_ENV, "").strip():
        return ProviderStatus(
            NAME, "unconfigured", f"{module} installed but {APP_KEY_ENV} is not set",
            entitlement="LSEG Workspace / Eikon / RDP",
        )
    headless = bool(
        os.environ.get(MACHINE_ID_ENV, "").strip()
        and os.environ.get(MACHINE_SECRET_ENV, "").strip()
    )
    kind = "platform session (headless-capable)" if headless else "desktop session (needs Workspace running here)"
    return ProviderStatus(
        NAME, "configured", f"{module}, {kind}", entitlement="LSEG Workspace / Eikon / RDP"
    )


def fetch(symbols: list[str]) -> dict[str, Fundamental]:
    """Pull consensus targets and expected report dates for `symbols`."""
    lib, module = _library()
    if lib is None:
        raise RuntimeError("no LSEG client installed (pip install lseg-data)")
    app_key = os.environ.get(APP_KEY_ENV, "").strip()
    if not app_key:
        raise RuntimeError(f"{APP_KEY_ENV} is not set")

    if module == "eikon":
        lib.set_app_key(app_key)
        frame, err = lib.get_data(symbols, FIELDS)
        if err:
            raise RuntimeError(f"LSEG returned errors: {err}")
        records = frame.to_dict("records")
    else:
        lib.open_session()
        try:
            frame = lib.get_data(universe=symbols, fields=FIELDS)
            records = frame.to_dict("records")
        finally:
            try:
                lib.close_session()
            except Exception:  # a failed close must not lose the data
                pass

    return {
        sym: fund
        for sym, fund in ((_symbol(r), _to_fundamental(r)) for r in records)
        if sym and fund
    }


def _symbol(record: dict) -> str:
    raw = record.get("Instrument") or record.get("instrument") or ""
    return str(raw).split(".")[0].strip().upper()


def _to_fundamental(record: dict) -> Fundamental | None:
    sym = _symbol(record)
    if not sym:
        return None
    return Fundamental(
        symbol=sym,
        source=NAME,
        sector=_str(record.get("GICS Sector Name")),
        market_cap=_float(record.get("Company Market Cap")),
        analyst_mean_target=_float(record.get("Price Target - Mean")),
        analyst_rating=_str(record.get("Recommendation - Label")),
        earnings_date=_date(record.get("Expected Report Date")),
        notes=["LSEG content is licensed -- do not redistribute outside your seat"],
    )


def _str(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _float(value) -> float | None:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return None if num != num else num  # drop NaN


def _date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None
