"""Bloomberg adapter (BLPAPI).

Read this before wiring it up, because the constraint is structural rather
than a missing feature:

* The **Desktop API** (`blpapi` against localhost:8194) requires a logged-in
  Bloomberg Terminal on the *same machine*. A cloud container has no
  Terminal, so a server-side scheduler can never use this path.
* **Server API / B-PIPE / Data License** are headless, but they are separate
  enterprise entitlements, not part of a Terminal seat.
* Terminal data is licensed to the seat holder. Bloomberg's agreement
  restricts redistributing it, so anything sourced here stays inside the
  private brief and is never published to a shared artifact or repo.

The practical shape: run the scheduler on the Terminal workstation and this
adapter fills in; run it in CI and it reports `unavailable` and the brief
falls back to the manual verification checklist.
"""

from __future__ import annotations

import os
from datetime import date, datetime

from swingtrader.providers.base import Fundamental, ProviderStatus

NAME = "bloomberg"
HOST_ENV = "BLOOMBERG_HOST"
PORT_ENV = "BLOOMBERG_PORT"
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 8194

FIELDS = [
    "BEST_TARGET_PRICE",
    "BEST_ANALYST_RATING",
    "ANNOUNCEMENT_DT",
    "GICS_SECTOR_NAME",
    "CUR_MKT_CAP",
    "SHORT_INT_RATIO",
]


def _blpapi():
    try:
        import blpapi

        return blpapi
    except ImportError:
        return None


def status() -> ProviderStatus:
    if _blpapi() is None:
        return ProviderStatus(
            NAME,
            "unconfigured",
            "blpapi not installed -- Desktop API also needs a running Terminal "
            "on this machine, so this only works on your workstation",
            entitlement="Bloomberg Terminal (Desktop API) or B-PIPE",
        )
    host = os.environ.get(HOST_ENV, DEFAULT_HOST)
    port = os.environ.get(PORT_ENV, str(DEFAULT_PORT))
    if not _terminal_listening(host, int(port)):
        return ProviderStatus(
            NAME,
            "unavailable",
            f"blpapi installed but nothing is listening on {host}:{port} "
            "(start the Terminal and log in)",
            entitlement="Bloomberg Terminal (Desktop API) or B-PIPE",
        )
    return ProviderStatus(
        NAME, "configured", f"Desktop API reachable on {host}:{port}",
        entitlement="Bloomberg Terminal (Desktop API)",
    )


def _terminal_listening(host: str, port: int, timeout: float = 1.0) -> bool:
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def fetch(symbols: list[str], yellow_key: str = "US Equity") -> dict[str, Fundamental]:
    """Reference-data request for `symbols` via the Desktop API."""
    blpapi = _blpapi()
    if blpapi is None:
        raise RuntimeError("blpapi is not installed")

    host = os.environ.get(HOST_ENV, DEFAULT_HOST)
    port = int(os.environ.get(PORT_ENV, DEFAULT_PORT))

    opts = blpapi.SessionOptions()
    opts.setServerHost(host)
    opts.setServerPort(port)
    session = blpapi.Session(opts)
    if not session.start():
        raise RuntimeError(f"could not start a Bloomberg session on {host}:{port}")

    try:
        if not session.openService("//blp/refdata"):
            raise RuntimeError("could not open //blp/refdata")
        service = session.getService("//blp/refdata")
        request = service.createRequest("ReferenceDataRequest")
        for sym in symbols:
            request.append("securities", f"{sym} {yellow_key}")
        for field in FIELDS:
            request.append("fields", field)
        session.sendRequest(request)
        return _drain(session, blpapi)
    finally:
        session.stop()


def _drain(session, blpapi) -> dict[str, Fundamental]:
    out: dict[str, Fundamental] = {}
    while True:
        event = session.nextEvent(500)
        for message in event:
            data = message.getElement("securityData") if message.hasElement("securityData") else None
            if data is None:
                continue
            for i in range(data.numValues()):
                item = data.getValueAsElement(i)
                sym = str(item.getElementAsString("security")).split(" ")[0].upper()
                if item.hasElement("securityError"):
                    continue
                fields = item.getElement("fieldData")
                out[sym] = Fundamental(
                    symbol=sym,
                    source=NAME,
                    sector=_get(fields, "GICS_SECTOR_NAME"),
                    market_cap=_get_float(fields, "CUR_MKT_CAP"),
                    analyst_mean_target=_get_float(fields, "BEST_TARGET_PRICE"),
                    analyst_rating=_get(fields, "BEST_ANALYST_RATING"),
                    earnings_date=_get_date(fields, "ANNOUNCEMENT_DT"),
                    notes=["Bloomberg content is seat-licensed -- keep it out of shared output"],
                )
        if event.eventType() == blpapi.Event.RESPONSE:
            return out


def _get(fields, name: str) -> str | None:
    if not fields.hasElement(name):
        return None
    text = str(fields.getElementAsString(name)).strip()
    return text or None


def _get_float(fields, name: str) -> float | None:
    if not fields.hasElement(name):
        return None
    try:
        return float(fields.getElementAsFloat(name))
    except Exception:
        return None


def _get_date(fields, name: str) -> date | None:
    raw = _get(fields, name)
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw[:10], fmt).date()
        except ValueError:
            continue
    return None
