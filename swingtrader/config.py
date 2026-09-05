"""Configuration loading.

Everything tunable lives in a YAML file so the strategy can be changed
without touching code, and so a change to the rules shows up as a reviewable
diff. Secrets never go in here: `${ENV_VAR}` placeholders are expanded from
the environment at load time.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path("config/swingtrader.yml")
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


@dataclass
class AccountConfig:
    """Capital at risk and the caps that keep one idea from sinking the book."""

    equity: float = 100_000.0
    currency: str = "USD"
    risk_per_trade_pct: float = 0.75      # % of equity lost if the stop fills
    max_open_risk_pct: float = 4.0        # total heat across all open ideas
    max_position_pct: float = 20.0        # notional cap for a single name
    max_adv_participation_pct: float = 2.0  # do not be more than this of a day's volume


@dataclass
class ScreenConfig:
    """One saved Finviz Elite screener pull."""

    name: str = "screen"
    filters: str = ""
    order: str = "-change"
    signal: str | None = None
    limit: int = 60


@dataclass
class UniverseConfig:
    watchlist: list[str] = field(default_factory=list)
    finviz_screens: list[ScreenConfig] = field(default_factory=list)
    benchmark: str = "SPY"
    sector_etfs: list[str] = field(default_factory=list)
    max_symbols: int = 150
    lookback_days: int = 400


@dataclass
class FilterConfig:
    """Hard gates. A name failing any of these never reaches scoring."""

    min_price: float = 5.0
    max_price: float | None = None
    min_avg_dollar_volume: float = 20_000_000.0
    min_atr_pct: float = 1.5              # too quiet to pay for the spread
    max_atr_pct: float = 12.0             # too wild to size sensibly
    min_bars: int = 210                   # need a real 200-day average
    earnings_blackout_days: int = 3       # skip names reporting inside this window
    exclude_symbols: list[str] = field(default_factory=list)


@dataclass
class SetupConfig:
    enabled: list[str] = field(
        default_factory=lambda: [
            "trend_pullback",
            "breakout",
            "momentum_flag",
            "oversold_reversion",
        ]
    )
    allow_shorts: bool = False
    min_score: float = 55.0
    atr_stop_multiple: float = 1.5
    reward_multiple: float = 2.0
    max_hold_days: int = 10


@dataclass
class RegimeConfig:
    """Market-wide gate. Long setups behave differently in a downtrend."""

    benchmark: str = "SPY"
    risk_off_action: str = "reduce"       # reduce | halt | ignore
    reduce_factor: float = 0.5            # scale position size in risk-off


@dataclass
class OutputConfig:
    count: int = 5
    directory: str = "out"
    formats: list[str] = field(default_factory=lambda: ["markdown", "html", "json"])
    include_verification_checklist: bool = True


@dataclass
class Config:
    account: AccountConfig = field(default_factory=AccountConfig)
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    filters: FilterConfig = field(default_factory=FilterConfig)
    setups: SetupConfig = field(default_factory=SetupConfig)
    regime: RegimeConfig = field(default_factory=RegimeConfig)
    output: OutputConfig = field(default_factory=OutputConfig)

    @classmethod
    def load(cls, path: Path | str | None = None) -> "Config":
        target = Path(path) if path else DEFAULT_CONFIG_PATH
        if not target.exists():
            if path:
                raise FileNotFoundError(f"config not found: {target}")
            return cls()
        return cls.from_dict(_read_yaml(target))

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Config":
        raw = _expand_env(raw or {})
        cfg = cls()
        cfg.account = _build(AccountConfig, raw.get("account"))
        cfg.filters = _build(FilterConfig, raw.get("filters"))
        cfg.setups = _build(SetupConfig, raw.get("setups"))
        cfg.regime = _build(RegimeConfig, raw.get("regime"))
        cfg.output = _build(OutputConfig, raw.get("output"))

        uni_raw = dict(raw.get("universe") or {})
        screens = uni_raw.pop("finviz_screens", []) or []
        cfg.universe = _build(UniverseConfig, uni_raw)
        cfg.universe.finviz_screens = [_build(ScreenConfig, s) for s in screens]
        cfg.universe.watchlist = [s.strip().upper() for s in cfg.universe.watchlist if s]
        cfg.filters.exclude_symbols = [
            s.strip().upper() for s in cfg.filters.exclude_symbols if s
        ]
        cfg.validate()
        return cfg

    def validate(self) -> None:
        """Fail loudly at 06:00 rather than silently sizing something absurd."""
        problems: list[str] = []
        if self.account.equity <= 0:
            problems.append("account.equity must be positive")
        if not 0 < self.account.risk_per_trade_pct <= 10:
            problems.append("account.risk_per_trade_pct must be in (0, 10]")
        if self.account.max_open_risk_pct < self.account.risk_per_trade_pct:
            problems.append("account.max_open_risk_pct is below risk_per_trade_pct")
        if self.setups.atr_stop_multiple <= 0:
            problems.append("setups.atr_stop_multiple must be positive")
        if self.setups.reward_multiple <= 0:
            problems.append("setups.reward_multiple must be positive")
        if self.filters.min_atr_pct >= self.filters.max_atr_pct:
            problems.append("filters.min_atr_pct must be below max_atr_pct")
        if self.regime.risk_off_action not in ("reduce", "halt", "ignore"):
            problems.append("regime.risk_off_action must be reduce, halt or ignore")
        if not self.universe.watchlist and not self.universe.finviz_screens:
            problems.append("universe needs a watchlist or at least one finviz screen")
        if problems:
            raise ValueError("invalid config:\n  - " + "\n  - ".join(problems))


def _read_yaml(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml

        return yaml.safe_load(text) or {}
    except ImportError:
        return _mini_yaml(text)


def _build(cls, raw: Any):
    """Instantiate a dataclass from a mapping, ignoring unknown keys."""
    if not is_dataclass(cls):
        raise TypeError(f"{cls} is not a dataclass")
    known = {f.name for f in fields(cls)}
    data = {k: v for k, v in (raw or {}).items() if k in known}
    unknown = set((raw or {}).keys()) - known
    if unknown:
        raise ValueError(f"unknown {cls.__name__} keys: {', '.join(sorted(unknown))}")
    return cls(**data)


def _expand_env(value: Any) -> Any:
    """Recursively expand ${VAR} and ${VAR:-default} in strings."""
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, str):
        return _ENV_PATTERN.sub(
            lambda m: os.environ.get(m.group(1), m.group(2) or ""), value
        )
    return value


def _mini_yaml(text: str) -> dict[str, Any]:
    """A small YAML subset parser, used only when PyYAML is absent.

    Covers what the shipped config actually uses: nested mappings, block
    lists (`- item`), lists of mappings, inline `[a, b]` lists and comments.
    Anchors, multi-line strings and flow mappings are not supported -- if you
    need those, install PyYAML.
    """
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        body = _strip_comment(raw).rstrip()
        if not body.strip():
            continue
        lines.append((len(raw) - len(raw.lstrip()), body.strip()))

    if not lines:
        return {}
    value, _ = _parse_block(lines, 0)
    return value if isinstance(value, dict) else {}


def _parse_block(lines: list[tuple[int, str]], index: int) -> tuple[Any, int]:
    """Parse the block beginning at `index`. Returns (value, next index)."""
    indent = lines[index][0]
    if _is_item(lines[index][1]):
        return _parse_list(lines, index, indent)
    return _parse_mapping(lines, index, indent)


def _parse_list(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[list, int]:
    items: list[Any] = []
    while index < len(lines) and lines[index][0] == indent and _is_item(lines[index][1]):
        body = lines[index][1][2:].strip()
        index += 1
        key, rest, is_pair = _split_pair(body)
        if not is_pair:
            items.append(_scalar(body))
            continue

        # A mapping item: its first key came from the `- ` line, the rest of
        # its keys sit indented below it.
        item: dict[str, Any] = {}
        if rest:
            item[key] = _scalar(rest)
        elif index < len(lines) and lines[index][0] > indent:
            item[key], index = _parse_block(lines, index)
        else:
            item[key] = None

        while index < len(lines) and lines[index][0] > indent and not _is_item(lines[index][1]):
            index = _consume_pair(lines, index, item)
        items.append(item)
    return items, index


def _parse_mapping(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[dict, int]:
    mapping: dict[str, Any] = {}
    while index < len(lines) and lines[index][0] == indent and not _is_item(lines[index][1]):
        index = _consume_pair(lines, index, mapping)
    return mapping, index


def _consume_pair(lines: list[tuple[int, str]], index: int, into: dict[str, Any]) -> int:
    """Read one `key: value` line plus any block that belongs to it."""
    indent, body = lines[index]
    key, rest, _ = _split_pair(body)
    index += 1

    if rest:
        into[key] = _scalar(rest)
        return index

    # An empty value means the value is the block below -- either indented,
    # or a list written at the key's own indentation.
    if index < len(lines) and (
        lines[index][0] > indent
        or (lines[index][0] == indent and _is_item(lines[index][1]))
    ):
        into[key], index = _parse_block(lines, index)
    else:
        into[key] = None
    return index


def _strip_comment(line: str) -> str:
    """Drop a trailing `# comment`.

    YAML only treats `#` as a comment when it follows whitespace, and never
    inside a quoted string -- so `filters: a#b` and `name: "a # b"` survive.
    """
    quote: str | None = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
    return line


def _is_item(body: str) -> bool:
    return body.startswith("- ") or body == "-"


def _split_pair(body: str) -> tuple[str, str, bool]:
    """Split `key: value`. The third element says whether it really is a pair.

    A colon only starts a value when followed by a space or end of line, so
    `- https://example.com` stays a scalar.
    """
    key, sep, rest = body.partition(":")
    if not sep or (rest and not rest.startswith(" ")):
        return body, "", False
    return key.strip(), rest.strip(), True


def _scalar(raw: str) -> Any:
    text = raw.strip().strip('"').strip("'")
    lowered = text.lower()
    if lowered in ("null", "none", "~", ""):
        return None
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        return [_scalar(p) for p in inner.split(",")] if inner else []
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text
