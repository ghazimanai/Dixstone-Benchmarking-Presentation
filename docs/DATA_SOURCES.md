# What each subscription can actually do

The short version: **of the four paid sources, only Finviz Elite can be
queried by an unattended server.** Bloomberg and LSEG need their desktop
application running on the same machine; Morningstar has no API on the
retail tier at all. That constraint, not effort, is what shapes this tool.

So the design is: automate everything that *can* be automated, and for the
rest, hand you a precise checklist of what to look up in each terminal —
five lines per idea, not a re-reading of the whole market.

---

## Finviz Elite — works headless ✅

Elite includes a CSV export endpoint that takes the same parameters as the
web screener plus an auth token:

```
https://elite.finviz.com/export.ashx?v=152&f=<filters>&o=<order>&auth=<token>
```

Get the token from **Finviz → Settings → API**, then:

```bash
export FINVIZ_AUTH_TOKEN=...
python3 -m swingtrader screen        # check it works
```

To add a screen: build it on the Finviz website, copy the `f=` value out of
the browser URL, and paste it into `finviz_screens[].filters` in the config.
The export endpoint accepts the same string.

The free tier has no export endpoint, so without Elite the tool falls back
to your watchlist only — and says so in the brief.

## Morningstar — manual export, or Direct ⚠️

| Tier | Programmatic access |
|---|---|
| Investor / Premium (retail) | **None.** There is no API on this tier. |
| Direct | Yes — Direct Web Services, a separate enterprise entitlement. |

Scraping a logged-in Premium session would breach the subscriber agreement,
so this tool does not do it. Two supported paths instead:

**Manual export (works on Premium today).** Export a screener or portfolio
from Morningstar as CSV and drop it in `data/morningstar/`. Headers are
matched loosely — star rating, fair value, price/fair value, moat and
uncertainty are picked up from an unmodified export. Refresh it weekly;
fair value estimates do not move daily.

**Direct Web Services.** If you have Direct, set `MORNINGSTAR_DIRECT_TOKEN`
and implement `fetch_direct()` in `swingtrader/providers/morningstar.py`.
It is left as a stub on purpose: the dataset ids are contract-specific, and
a guess there would fail silently at 06:00.

## Bloomberg — workstation only ⚠️

| Path | Headless? | Notes |
|---|---|---|
| Desktop API (`blpapi` → localhost:8194) | No | Needs a logged-in Terminal **on the same machine**. |
| Server API / B-PIPE / Data License | Yes | Separate enterprise entitlements, not part of a Terminal seat. |

A cloud runner has no Terminal, so the Desktop API can never work there.
Run the scheduler on your Terminal workstation and the adapter fills in
consensus targets, ratings and announcement dates automatically.

Terminal data is licensed to the seat holder. The adapter keeps anything it
returns inside the local brief and never publishes it.

## Reuters / LSEG — same shape as Bloomberg ⚠️

Reuters market data is now LSEG. Two session types:

| Session | Headless? | Notes |
|---|---|---|
| Desktop (`lseg-data`, `refinitiv-data`, `eikon`) | No | Proxies through Workspace/Eikon on localhost:9000. |
| Platform (RDP / Delivery Platform) | Yes | Machine credentials; a separate entitlement from a Workspace seat. |

```bash
pip install lseg-data
export LSEG_APP_KEY=...
# plus LSEG_MACHINE_ID / LSEG_MACHINE_SECRET for a headless platform session
```

LSEG content is licensed. Like Bloomberg, it stays in the private brief.

## Prices and volume — the always-on backbone ✅

Daily OHLCV comes from Yahoo's public chart endpoint. No key, no
entitlement, so the technical engine keeps working even when every terminal
is unreachable. Two practical notes learned the hard way:

- Yahoo rate-limits per source IP, and it throttles the common full Chrome
  user-agent string hard — a plain `Mozilla/5.0` is served normally. Override
  with `SWINGTRADER_UA` if you need to.
- `query1` and `query2` throttle independently, so requests rotate across
  both and a 429 on one is retried on the other.

Bars are cached on disk (`.swingtrader-cache/`, 12-hour freshness) so a
re-run costs nothing.

If you want a source with an SLA behind it, your broker's market-data API
(Interactive Brokers, Schwab, Tradier, Alpaca) is free with an account and
drops straight into `swingtrader/providers/` behind the same `History`
interface.

---

## Where to run it

| | Workstation (Terminal/Workspace installed) | Cloud runner / GitHub Actions |
|---|---|---|
| Yahoo prices | ✅ | ✅ |
| Finviz Elite | ✅ | ✅ |
| Morningstar CSV | ✅ | ✅ if the export is committed or synced |
| Bloomberg | ✅ Desktop API | ❌ no Terminal |
| LSEG / Reuters | ✅ desktop session | ⚠️ only with RDP machine credentials |

**The recommendation:** run it on the workstation where your Terminal and
Workspace already are. That is the only place all four subscriptions can
contribute. Use the GitHub Actions workflow as a backstop for days you are
not at that machine — it still produces the full technical brief and simply
marks the terminal-sourced overlays as unavailable.

## A note on scraping

Every one of these subscriptions has a subscriber agreement that restricts
automated extraction and redistribution. This tool deliberately uses only
documented, entitled interfaces — the Finviz export endpoint, the vendor
SDKs, and manual exports you make yourself. It never drives a logged-in
browser session against a site that forbids it. If you have an entitlement
that opens a better door (Direct, B-PIPE, RDP), the adapters are the place
to wire it in.
