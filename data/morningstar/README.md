# Morningstar drop-in

Export a screener or portfolio from Morningstar as CSV and put it in this
directory. The next run picks it up automatically and overlays star rating,
fair value, price/fair value, economic moat and uncertainty onto the ideas.

Headers are matched loosely, so an unmodified export works — `Ticker` or
`Symbol`, `Star Rating` or `Morningstar Rating`, `Fair Value` or
`Fair Value Estimate`, and so on. Preamble rows above the header are skipped.

Refresh it weekly; fair value estimates do not move daily.

**The CSVs themselves are gitignored** — they are subscriber data and stay on
your machine. Only this note is tracked.

Why manual: Morningstar has no API on the Investor/Premium tier, and scraping
a logged-in session would breach the subscriber agreement. If you have
Morningstar Direct, wire `fetch_direct()` in
`swingtrader/providers/morningstar.py` to your Direct Web Services endpoint
instead. See [`docs/DATA_SOURCES.md`](../../docs/DATA_SOURCES.md).
