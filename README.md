# Dixstone — Strategic Peer Benchmarking Presentation

Executive presentation prepared by **Sigma Energy** for **Dixstone Drilling** —
a 26-slide single-file HTML deck covering peer benchmarking, internal SAP-driven
deep-dives, and per-rig one-pagers.

**🌐 Live URL:** https://dixstone-benchmarking-presentation.vercel.app/

## Contents

| File | Purpose |
|---|---|
| `index.html` | The executive presentation (26 slides, scroll-snap navigation) |
| `executive-presentation.html` | Duplicate of `index.html` kept so legacy links don't break |
| `sigma-logo.svg` | Sigma Energy white-on-dark vector mark |
| `dixstone-logo-white.png` | Dixstone wordmark recoloured white for the dark canvas |

## Deck structure

**Part 1 · Peer Benchmarking (slides 1–9)**
Cover · Methodology · Engagement Brief · Where Dixstone Stands · Market Context ·
Segment View · Five Impactful Actions · Next Steps · Close.

**Part 2 · Internal Deep-Dive (slides 10–19)**
PO methodology & annual · PO daily 2025 · GR methodology & Adjusted OPEX ·
Adjusted OPEX daily 2025 · Inventory levels vs brackets · Dead stock by rig ·
Manning vs industry caps · Organisational evolution · Supply chain coherence.

**Part 3 · Rig One-Pagers (slides 20–26)**
Section divider + one slide per rig: AXIMA · BANBA · NUADA · LUG · MIDIR · DRAVUS.

## Editing

The deck is a single self-contained HTML file. Open `index.html` in any browser —
no server, no build step. All CSS and JavaScript live inline. Inter,
Space Grotesk, and JetBrains Mono fonts load from Google Fonts at runtime.

## Deployment

- **Hosting:** Vercel (auto-deploys on every push to `main`)
- **Source data:** SAP exports (ME2N · MB51 · MB52), per-rig P&L (rev-13), and
  NPT history (rev-03) — kept private in OneDrive, never committed here.

## Filters & methodology

All figures in the deep-dive section apply the same filters used in the
April 2026 progress presentation:

- **Drilling-only scope** — non-drilling POs excluded (cementing, wireline)
- **CMT & WIR purchase groups removed**
- **Project (CAPEX) POs excluded** from OPEX views
- **Operating days = field operations + rig moves** (the activity denominator)
- **PO-history join** to re-price GR / MB52 lines lacking valuation

## Also in this repository

`swingtrader/` — a scheduled swing-trade research assistant, unrelated to the
Dixstone engagement. Runs before the open, screens a configured universe,
sizes each idea against a real account and writes a daily brief. Standard
library only. See [`swingtrader/README.md`](swingtrader/README.md) and
[`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md).

## Credits

Prepared by Sigma Energy · Engagement QC25377 · May 2026.
