"""Daily brief rendering: Markdown, HTML and JSON.

The HTML file is the one meant to be read at 06:30 with coffee; the JSON is
the machine-readable record that makes it possible to score yesterday's
brief against what actually happened.
"""

from __future__ import annotations

import html
import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path

from swingtrader.config import Config
from swingtrader.engine import Brief, Candidate

# What to check in the terminals that a scheduler cannot reach on its own.
VERIFICATION_STEPS = [
    ("Bloomberg", "{sym} US Equity DES / CN — company news and any overnight headline"),
    ("Bloomberg", "{sym} US Equity ERN — confirm the next earnings date before entering"),
    ("Reuters/LSEG", "{sym} — broker actions and estimate revisions in the last five sessions"),
    ("Morningstar", "{sym} — star rating, fair value estimate and moat vs the current price"),
    ("Finviz", "{sym} — float, short interest and the intraday chart at the entry level"),
]


def _session_line(brief: Brief) -> str:
    """State which close the levels were computed from, and flag stale data."""
    if brief.data_session is None:
        return "no session data"
    age = (brief.as_of - brief.data_session).days
    label = f"levels from the {brief.data_session.strftime('%a %d %b')} close"
    if age > 4:
        label += f" ({age} days old -- check the feed)"
    return label


def render_all(brief: Brief, cfg: Config) -> dict[str, Path]:
    """Write every configured format. Returns {format: path}."""
    out_dir = Path(cfg.output.directory)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = brief.as_of.isoformat()
    written: dict[str, Path] = {}

    renderers = {
        "markdown": (f"swing-brief-{stamp}.md", to_markdown),
        "html": (f"swing-brief-{stamp}.html", to_html),
        "json": (f"swing-brief-{stamp}.json", to_json),
    }
    for fmt in cfg.output.formats:
        entry = renderers.get(fmt)
        if entry is None:
            continue
        filename, fn = entry
        path = out_dir / filename
        path.write_text(fn(brief, cfg), encoding="utf-8")
        written[fmt] = path

    if written:
        latest = out_dir / "latest.html"
        if "html" in written:
            latest.write_text(to_html(brief, cfg), encoding="utf-8")
            written["latest"] = latest
    return written


# --- markdown ----------------------------------------------------------------


def to_markdown(brief: Brief, cfg: Config) -> str:
    lines: list[str] = []
    add = lines.append

    add(f"# Swing brief — {brief.as_of.isoformat()}")
    add("")
    add(f"_Generated {brief.generated_at.strftime('%Y-%m-%d %H:%M')} · "
        f"{_session_line(brief)} · "
        f"research screen, not investment advice · you place every order yourself._")
    add("")
    add(f"**{brief.context.summary()}**")
    if brief.context.notes:
        for note in brief.context.notes:
            add(f"- {note}")
    add("")

    p = brief.portfolio
    if p:
        add(f"**Book:** {int(p['ideas'])} idea(s) · risk {p['total_risk']:,.0f} "
            f"({p['total_risk_pct']:.2f}% of equity) · gross exposure "
            f"{p['gross_exposure_pct']:.1f}% · heat headroom "
            f"{p['heat_headroom_pct']:.2f}pp")
        add("")

    if not brief.picks:
        add("## No qualifying setups today")
        add("")
        add("Nothing cleared the score threshold. That is a result, not a failure — "
            "a screen that always finds five ideas is not screening.")
    else:
        add(f"## Today's {len(brief.picks)} idea(s)")
        add("")
        add("| # | Symbol | Setup | Score | Entry | Stop | Target | R:R | Shares | Risk |")
        add("|---|--------|-------|-------|-------|------|--------|-----|--------|------|")
        for i, c in enumerate(brief.picks, 1):
            pl = c.plan
            rr = f"{pl.reward_risk:.1f}" if pl.reward_risk else "—"
            add(f"| {i} | **{c.symbol}** | {pl.setup.replace('_', ' ')} | "
                f"{c.setup.score:.0f} | {pl.entry:,.2f} | {pl.stop:,.2f} | "
                f"{pl.target:,.2f} | {rr} | {pl.shares:,} | {pl.risk_amount:,.0f} |")
        add("")

        for i, c in enumerate(brief.picks, 1):
            lines.extend(_markdown_detail(i, c, cfg))

    if brief.near_misses:
        add("## Near misses")
        add("")
        for sym, name, score in brief.near_misses[:10]:
            add(f"- **{sym}** — {name.replace('_', ' ')}, score {score:.0f} "
                f"(threshold {cfg.setups.min_score:.0f})")
        add("")

    add("## Data sources this run")
    add("")
    for status in brief.providers:
        mark = {"live": "✅", "configured": "✅", "unconfigured": "⚪", "unavailable": "❌"}
        add(f"- {mark[status.availability]} **{status.name}** — {status.detail}")
    add("")

    if brief.warnings:
        add("## Notes and gaps")
        add("")
        for warning in brief.warnings:
            add(f"- {warning}")
        add("")

    add(f"_Universe considered: {brief.considered} symbols · "
        f"{len(brief.rejections)} filtered out._")
    return "\n".join(lines) + "\n"


def _markdown_detail(index: int, c: Candidate, cfg: Config) -> list[str]:
    pl, snap, setup = c.plan, c.snapshot, c.setup
    lines = [
        f"### {index}. {c.symbol} — {pl.setup.replace('_', ' ')} ({pl.direction})",
        "",
        f"**Score {setup.score:.0f}/100** · {pl.entry_style} entry at "
        f"{pl.entry:,.2f} · stop {pl.stop:,.2f} ({pl.stop_distance_pct:.1f}%) · "
        f"target {pl.target:,.2f} · hold up to {pl.max_hold_days} sessions",
        "",
        f"Size {pl.shares:,} shares ≈ {pl.notional:,.0f} notional, risking "
        f"{pl.risk_amount:,.0f} ({pl.risk_pct_of_equity:.2f}% of equity).",
        "",
        f"Last {snap.close:,.2f} · ATR {snap.atr_pct:.1f}% · RSI(14) "
        f"{snap.rsi14:.0f} · rel-vol {snap.rel_volume:.2f}x · "
        f"{snap.pct_from_52w_high:+.1f}% from the 52-week high"
        if snap.atr_pct and snap.rsi14 and snap.rel_volume and snap.pct_from_52w_high is not None
        else f"Last {snap.close:,.2f}",
        "",
    ]
    if setup.reasons:
        lines.append("**Why it scored:**")
        lines.extend(f"- {r}" for r in setup.reasons)
        lines.append("")
    if c.overlay_notes:
        lines.append("**Overlay:** " + " · ".join(c.overlay_notes))
        lines.append("")
    watch = list(setup.cautions) + list(pl.warnings) + list(pl.size_caps_applied)
    if watch:
        lines.append("**Watch out:**")
        lines.extend(f"- {w}" for w in watch)
        lines.append("")
    if cfg.output.include_verification_checklist:
        lines.append("**Before you enter — check in your terminals:**")
        lines.extend(f"- {src}: {step.format(sym=c.symbol)}" for src, step in VERIFICATION_STEPS)
        lines.append("")
    return lines


# --- json --------------------------------------------------------------------


def to_json(brief: Brief, cfg: Config) -> str:
    payload = {
        "as_of": brief.as_of.isoformat(),
        "data_session": brief.data_session.isoformat() if brief.data_session else None,
        "generated_at": brief.generated_at.isoformat(timespec="seconds"),
        "regime": {
            "benchmark": brief.context.benchmark,
            "state": brief.context.regime,
            "close": brief.context.close,
            "roc21": brief.context.roc21,
            "roc63": brief.context.roc63,
        },
        "portfolio": brief.portfolio,
        "picks": [
            {
                "symbol": c.symbol,
                "setup": c.plan.setup,
                "direction": c.plan.direction,
                "score": round(c.setup.score, 2),
                "entry": round(c.plan.entry, 4),
                "stop": round(c.plan.stop, 4),
                "target": round(c.plan.target, 4),
                "entry_style": c.plan.entry_style,
                "shares": c.plan.shares,
                "notional": round(c.plan.notional, 2),
                "risk_amount": round(c.plan.risk_amount, 2),
                "risk_pct_of_equity": round(c.plan.risk_pct_of_equity, 4),
                "reward_risk": round(c.plan.reward_risk, 3) if c.plan.reward_risk else None,
                "max_hold_days": c.plan.max_hold_days,
                "reasons": c.setup.reasons,
                "cautions": c.setup.cautions + c.plan.warnings,
                "size_caps": c.plan.size_caps_applied,
                "overlay": c.overlay_notes,
                "snapshot": _snapshot_json(c),
            }
            for c in brief.picks
        ],
        "near_misses": [
            {"symbol": s, "setup": n, "score": round(v, 2)} for s, n, v in brief.near_misses[:25]
        ],
        "rejections": [{"symbol": s, "reason": r} for s, r in brief.rejections],
        "providers": [
            {"name": p.name, "availability": p.availability, "detail": p.detail}
            for p in brief.providers
        ],
        "warnings": brief.warnings,
        "considered": brief.considered,
        "config": {
            "min_score": cfg.setups.min_score,
            "enabled_setups": cfg.setups.enabled,
            "risk_per_trade_pct": cfg.account.risk_per_trade_pct,
            "equity": cfg.account.equity,
        },
    }
    return json.dumps(payload, indent=2, default=_json_default)


def _snapshot_json(c: Candidate) -> dict:
    s = c.snapshot
    return {
        "day": s.day.isoformat(),
        "close": s.close,
        "atr_pct": s.atr_pct,
        "rsi14": s.rsi14,
        "rsi2": s.rsi2,
        "adx14": s.adx14,
        "rel_volume": s.rel_volume,
        "avg_dollar_volume": s.avg_dollar_volume,
        "sma20": s.sma20,
        "sma50": s.sma50,
        "sma200": s.sma200,
        "roc21": s.roc21,
        "roc63": s.roc63,
        "pct_from_52w_high": s.pct_from_52w_high,
    }


def _json_default(obj):
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if is_dataclass(obj):
        return asdict(obj)
    return str(obj)


# --- html --------------------------------------------------------------------

_CSS = """
:root{--bg:#fbfaf8;--panel:#fff;--ink:#16181d;--muted:#5f6672;--line:#e4e2dd;
--accent:#1c4f8b;--long:#0f7a52;--short:#a8322a;--warn:#8a5a12;--warn-bg:#fdf6e6;
--chip:#eef1f5;}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#14161a;
--panel:#1b1e24;--ink:#e8eaee;--muted:#98a0ad;--line:#2b3038;--accent:#6ba4e8;
--long:#4cc38a;--short:#e2705f;--warn:#e0b25e;--warn-bg:#2a2417;--chip:#242932;}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.6 ui-sans-serif,-apple-system,"Segoe UI",Inter,system-ui,sans-serif;}
.wrap{max-width:980px;margin:0 auto;padding:32px 20px 72px}
h1{font-size:26px;margin:0 0 4px;letter-spacing:-.02em}
h2{font-size:17px;margin:34px 0 12px;letter-spacing:-.01em}
h3{font-size:16px;margin:0 0 2px;letter-spacing:-.01em}
.sub{color:var(--muted);font-size:13px;margin:0 0 20px}
.regime{background:var(--panel);border:1px solid var(--line);border-radius:10px;
padding:14px 16px;margin:0 0 18px}
.regime b{font-size:15px}
.badge{display:inline-block;padding:2px 9px;border-radius:999px;font-size:11px;
font-weight:600;letter-spacing:.04em;text-transform:uppercase}
.risk_on{background:rgba(15,122,82,.14);color:var(--long)}
.neutral{background:var(--chip);color:var(--muted)}
.risk_off{background:rgba(168,50,42,.14);color:var(--short)}
.unknown{background:var(--chip);color:var(--muted)}
.stats{display:flex;flex-wrap:wrap;gap:10px;margin:12px 0 0}
.stat{background:var(--chip);border-radius:8px;padding:8px 12px;min-width:120px}
.stat .k{display:block;font-size:11px;color:var(--muted);text-transform:uppercase;
letter-spacing:.05em}
.stat .v{font-size:16px;font-weight:600;font-variant-numeric:tabular-nums}
.tablewrap{overflow-x:auto;border:1px solid var(--line);border-radius:10px;background:var(--panel)}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{padding:9px 12px;text-align:right;white-space:nowrap;border-bottom:1px solid var(--line)}
th:first-child,td:first-child,th:nth-child(2),td:nth-child(2),
th:nth-child(3),td:nth-child(3){text-align:left}
th{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);
background:var(--chip)}
tbody tr:last-child td{border-bottom:none}
td.num{font-variant-numeric:tabular-nums}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;
padding:18px 20px;margin:0 0 14px}
.card header{display:flex;flex-wrap:wrap;gap:10px;align-items:baseline;
justify-content:space-between;margin:0 0 10px}
.score{font-variant-numeric:tabular-nums;font-weight:700;font-size:20px;color:var(--accent)}
.levels{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}
.lvl{background:var(--chip);border-radius:7px;padding:7px 11px;font-size:13px}
.lvl b{font-variant-numeric:tabular-nums}
.lvl.entry b{color:var(--accent)}.lvl.stop b{color:var(--short)}.lvl.target b{color:var(--long)}
ul{margin:6px 0 0;padding-left:20px}li{margin:3px 0}
.why li::marker{color:var(--long)}
.warn{background:var(--warn-bg);border-left:3px solid var(--warn);border-radius:0 7px 7px 0;
padding:9px 13px;margin:10px 0 0;font-size:13.5px}
.warn ul{margin:4px 0 0}
.overlay{font-size:13px;color:var(--muted);margin:8px 0 0}
.check{margin:12px 0 0;font-size:13px}
.check summary{cursor:pointer;color:var(--accent);font-weight:600}
.src{display:grid;grid-template-columns:auto 1fr;gap:6px 10px;font-size:13.5px;
background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.src .n{font-weight:600}
.empty{background:var(--panel);border:1px dashed var(--line);border-radius:10px;
padding:28px;text-align:center;color:var(--muted)}
footer{margin:38px 0 0;padding:16px 0 0;border-top:1px solid var(--line);
color:var(--muted);font-size:12.5px}
@media print{body{background:#fff}.card,.regime,.tablewrap,.src{break-inside:avoid}}
"""


def to_html(brief: Brief, cfg: Config) -> str:
    e = html.escape
    ctx = brief.context
    p = brief.portfolio

    parts: list[str] = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>Swing brief · {brief.as_of.isoformat()}</title>",
        f"<style>{_CSS}</style></head><body><div class='wrap'>",
        f"<h1>Swing brief · {brief.as_of.strftime('%A %d %B %Y')}</h1>",
        f"<p class='sub'>Generated {e(brief.generated_at.strftime('%H:%M'))} · "
        f"{e(_session_line(brief))} · "
        "screening output, not investment advice · every order is placed by you.</p>",
        "<div class='regime'>",
        f"<span class='badge {e(ctx.regime)}'>{e(ctx.regime.replace('_', ' '))}</span> ",
        f"<b>{e(ctx.summary())}</b>",
    ]
    if ctx.notes:
        parts.append("<ul>" + "".join(f"<li>{e(n)}</li>" for n in ctx.notes) + "</ul>")

    if p:
        parts.append("<div class='stats'>")
        for key, label, fmt in (
            ("ideas", "Ideas", "{:.0f}"),
            ("total_risk", "Risk at stop", "{:,.0f}"),
            ("total_risk_pct", "% of equity", "{:.2f}%"),
            ("gross_exposure_pct", "Gross exposure", "{:.1f}%"),
            ("heat_headroom_pct", "Heat headroom", "{:.2f}pp"),
        ):
            parts.append(
                f"<div class='stat'><span class='k'>{label}</span>"
                f"<span class='v'>{fmt.format(p.get(key, 0.0))}</span></div>"
            )
        parts.append("</div>")
    parts.append("</div>")

    if not brief.picks:
        parts.append(
            "<div class='empty'><b>No qualifying setups today.</b><br>"
            f"Nothing cleared a score of {cfg.setups.min_score:.0f}. "
            "A screen that finds five ideas every day is not screening.</div>"
        )
    else:
        parts.append(f"<h2>Today's {len(brief.picks)} idea(s)</h2>")
        parts.append("<div class='tablewrap'><table><thead><tr>"
                     "<th>#</th><th>Symbol</th><th>Setup</th><th>Score</th><th>Entry</th>"
                     "<th>Stop</th><th>Target</th><th>R:R</th><th>Shares</th><th>Risk</th>"
                     "</tr></thead><tbody>")
        for i, c in enumerate(brief.picks, 1):
            pl = c.plan
            rr = f"{pl.reward_risk:.1f}" if pl.reward_risk else "—"
            parts.append(
                f"<tr><td class='num'>{i}</td><td><b>{e(c.symbol)}</b></td>"
                f"<td>{e(pl.setup.replace('_', ' '))}</td>"
                f"<td class='num'>{c.setup.score:.0f}</td>"
                f"<td class='num'>{pl.entry:,.2f}</td><td class='num'>{pl.stop:,.2f}</td>"
                f"<td class='num'>{pl.target:,.2f}</td><td class='num'>{rr}</td>"
                f"<td class='num'>{pl.shares:,}</td><td class='num'>{pl.risk_amount:,.0f}</td></tr>"
            )
        parts.append("</tbody></table></div>")

        parts.append("<h2>The detail</h2>")
        for i, c in enumerate(brief.picks, 1):
            parts.append(_html_card(i, c, cfg))

    if brief.near_misses:
        parts.append("<h2>Near misses</h2><div class='tablewrap'><table><thead><tr>"
                     "<th>Symbol</th><th>Setup</th><th>Score</th></tr></thead><tbody>")
        for sym, name, score in brief.near_misses[:10]:
            parts.append(
                f"<tr><td><b>{e(sym)}</b></td><td>{e(name.replace('_', ' '))}</td>"
                f"<td class='num'>{score:.0f}</td></tr>"
            )
        parts.append("</tbody></table></div>")

    parts.append("<h2>Data sources this run</h2><div class='src'>")
    icons = {"live": "✅", "configured": "✅", "unconfigured": "⚪", "unavailable": "❌"}
    for status in brief.providers:
        parts.append(
            f"<div class='n'>{icons[status.availability]} {e(status.name)}</div>"
            f"<div>{e(status.detail)}</div>"
        )
    parts.append("</div>")

    if brief.warnings:
        parts.append("<h2>Notes and gaps</h2><div class='warn'><ul>")
        parts.extend(f"<li>{e(w)}</li>" for w in brief.warnings)
        parts.append("</ul></div>")

    parts.append(
        f"<footer>Universe considered: {brief.considered} symbols · "
        f"{len(brief.rejections)} filtered out · setups "
        f"{e(', '.join(cfg.setups.enabled))} · minimum score {cfg.setups.min_score:.0f}."
        "<br>Levels are computed from end-of-day bars. Verify against live quotes "
        "before acting; slippage, borrow and fees are not modelled.</footer>"
    )
    parts.append("</div></body></html>")
    return "".join(parts)


def _html_card(index: int, c: Candidate, cfg: Config) -> str:
    e = html.escape
    pl, snap, setup = c.plan, c.snapshot, c.setup
    bits: list[str] = [
        "<div class='card'><header>",
        f"<h3>{index}. {e(c.symbol)} — {e(pl.setup.replace('_', ' '))} "
        f"<span class='badge {'risk_on' if pl.direction == 'long' else 'risk_off'}'>"
        f"{e(pl.direction)}</span></h3>",
        f"<span class='score'>{setup.score:.0f}<span style='font-size:12px'>/100</span></span>",
        "</header>",
        "<div class='levels'>",
        f"<span class='lvl entry'>{e(pl.entry_style)} entry <b>{pl.entry:,.2f}</b></span>",
        f"<span class='lvl stop'>stop <b>{pl.stop:,.2f}</b> ({pl.stop_distance_pct:.1f}%)</span>",
        f"<span class='lvl target'>target <b>{pl.target:,.2f}</b></span>",
        f"<span class='lvl'>size <b>{pl.shares:,}</b> sh ≈ {pl.notional:,.0f}</span>",
        f"<span class='lvl'>risk <b>{pl.risk_amount:,.0f}</b> "
        f"({pl.risk_pct_of_equity:.2f}%)</span>",
        f"<span class='lvl'>hold ≤ <b>{pl.max_hold_days}</b> sessions</span>",
        "</div>",
    ]

    facts = [f"last {snap.close:,.2f}"]
    if snap.atr_pct:
        facts.append(f"ATR {snap.atr_pct:.1f}%")
    if snap.rsi14:
        facts.append(f"RSI(14) {snap.rsi14:.0f}")
    if snap.rel_volume:
        facts.append(f"rel-vol {snap.rel_volume:.2f}x")
    if snap.pct_from_52w_high is not None:
        facts.append(f"{snap.pct_from_52w_high:+.1f}% from 52w high")
    if snap.avg_dollar_volume:
        facts.append(f"ADV {snap.avg_dollar_volume/1e6:,.0f}M")
    bits.append(f"<p class='overlay'>{e(' · '.join(facts))}</p>")

    if setup.reasons:
        bits.append("<ul class='why'>")
        bits.extend(f"<li>{e(r)}</li>" for r in setup.reasons)
        bits.append("</ul>")

    if c.overlay_notes:
        bits.append(f"<p class='overlay'><b>Overlay:</b> {e(' · '.join(c.overlay_notes))}</p>")

    watch = list(setup.cautions) + list(pl.warnings) + list(pl.size_caps_applied)
    if watch:
        bits.append("<div class='warn'><b>Watch out</b><ul>")
        bits.extend(f"<li>{e(w)}</li>" for w in watch)
        bits.append("</ul></div>")

    if cfg.output.include_verification_checklist:
        bits.append("<details class='check'><summary>Verify in your terminals before entering</summary><ul>")
        bits.extend(
            f"<li><b>{e(src)}</b> — {e(step.format(sym=c.symbol))}</li>"
            for src, step in VERIFICATION_STEPS
        )
        bits.append("</ul></details>")

    bits.append("</div>")
    return "".join(bits)
