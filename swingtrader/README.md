# swingtrader — a scheduled swing-trade research assistant

Runs before the open every weekday, screens your universe for swing setups,
sizes each idea against your actual account, and writes a brief you can read
in two minutes.

```bash
python3 -m swingtrader providers     # what can it reach right now?
python3 -m swingtrader run           # build today's brief
open out/latest.html
```

No `pip install`. The whole thing runs on the Python 3.9+ standard library —
which is the point, because something that has to fire unattended at 06:00
should not depend on a dependency tree that can break overnight.

## What it does

1. **Builds a universe** — your watchlist plus any Finviz Elite screens.
2. **Pulls 18 months of daily bars** for each name (cached on disk).
3. **Reads the market regime** from the benchmark, which gates direction and
   scales size.
4. **Applies hard filters** — price, liquidity, volatility band, earnings
   blackout. Anything rejected is reported with the reason.
5. **Scores four setups** against each survivor.
6. **Sizes the survivors** off the distance to the stop, clipped by
   single-name notional, share of daily volume, and total portfolio heat.
7. **Writes the brief** — HTML to read, Markdown to paste, JSON to score
   later against what actually happened.

## The setups

| Rule | Idea | Entry | Hold |
|---|---|---|---|
| `trend_pullback` | First pause in an established uptrend: intact trend, controlled dip into the 20-day, volume drying up, momentum resetting rather than breaking. | Stop above the setup day's high | ≤ 10 sessions |
| `breakout` | 20-day range high taken out on real volume after a genuine squeeze. | Stop above the range high | ≤ 10 sessions |
| `momentum_flag` | Strong three-month advance that has gone quiet and tight. | Stop above the setup day's high | ≤ 10 sessions |
| `oversold_reversion` | Short sharp flush (RSI-2 under 15) inside a longer uptrend. Target is the mean, not a 2R extension. | Limit at the close | ≤ 5 sessions |
| `short_breakdown` | Mirror of the breakout for a downtrend. Off by default. | Stop below the range low | ≤ 10 sessions |

Each rule has **gates** (does this setup apply at all?) and weighted
**components** (how good an example is it?). The brief prints the components
that scored well, so a number is never the whole explanation.

Two details that came out of testing against live data and are worth knowing:

- The entry trigger is the **most recent completed session's** high. Using
  `max(today, yesterday)` sounds harmless and quietly pushes every entry
  2–3% above where price actually is, dragging the stop out with it.
- "Tightness" is measured against the **median** daily range, not ATR.
  Wilder's ATR stays inflated for weeks after one earnings gap, which makes
  a violently volatile name score as a quiet consolidation. A recent gap
  above 7% is also flagged on the idea itself.

## Sizing

Shares come from the stop distance — `risk budget ÷ (entry − stop)` — then
three independent caps clip it:

| Cap | Default | Why |
|---|---|---|
| Single-name notional | 20% of equity | One idea should not be the book. |
| Share of 20-day volume | 2% | A position you cannot exit in a day is not liquid. |
| Total open risk (heat) | 4% of equity | Six losers in a row should be a drawdown, not a hole. |

Every cap that binds is printed on the idea, so a small share count always
comes with its reason.

## Configuration

Everything lives in [`config/swingtrader.yml`](../config/swingtrader.yml) —
strategy changes show up as reviewable diffs. Set `account.equity` first;
every share count scales off it. Secrets never go in the file: use
`${ENV_VAR}` placeholders, expanded at load time.

The config is validated on load, so a bad combination fails immediately with
a list of problems rather than silently sizing something absurd at 06:00.

## Data sources

Only Finviz Elite can be queried by an unattended server. Bloomberg and LSEG
need their desktop application running on the same machine; Morningstar has
no API on the retail tier. **[docs/DATA_SOURCES.md](../docs/DATA_SOURCES.md)
covers what each subscription can and cannot do, and how to wire it in.**

For everything that cannot be automated, each idea in the brief carries a
five-line checklist of exactly what to look up in each terminal before you
enter.

## Commands

| Command | Purpose |
|---|---|
| `run` | Build today's brief. `--count`, `--min-score`, `--equity`, `--dry-run`, `--no-cache`. |
| `providers` | Which sources are usable right now, and what is missing for the rest. |
| `explain SYMBOL` | Every indicator and every rule's verdict for one name — the debugging tool. |
| `screen` | Run the Finviz screens and list what comes back. |
| `setups` | List the rules and which are enabled. |
| `cache clear\|info` | Manage the local bar cache. |

## Scheduling

**On your workstation** (recommended — the only place all four subscriptions
can contribute):

```cron
30 6 * * 1-5  cd /path/to/repo && /usr/bin/python3 -m swingtrader run --quiet
```

**In CI**, [`.github/workflows/daily-swing-brief.yml`](../.github/workflows/daily-swing-brief.yml)
runs weekdays at 11:00 UTC and keeps each brief as an artifact. Set
`FINVIZ_AUTH_TOKEN` and `SWINGTRADER_EQUITY` as repository secrets. Terminal
overlays are simply marked unavailable there.

## Tests

```bash
python3 -m unittest discover -s tests -t .
```

137 tests, no network access required — every fixture is synthetic, so the
suite is deterministic and runs in about six seconds.

## Scope

This is a screening and research tool. It reads public and entitled market
data, applies rules you configured, and writes a document. It does not
connect to a broker, place orders, or manage positions — every order is
placed by you. Levels are computed from end-of-day bars; slippage, borrow
costs and fees are not modelled. Nothing it produces is investment advice.
