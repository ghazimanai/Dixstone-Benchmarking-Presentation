import unittest
from datetime import date, timedelta
from tempfile import TemporaryDirectory
from unittest import mock

from swingtrader import engine, report, snapshot
from swingtrader.cache import BarCache
from swingtrader.config import Config
from swingtrader.providers.base import Fundamental
from swingtrader.regime import classify
from tests.helpers import downtrend, flat, make_history, uptrend


def cfg_for(symbols, **kw) -> Config:
    cfg = Config()
    cfg.universe.watchlist = symbols
    cfg.output.directory = kw.pop("directory", "out")
    for key, value in kw.items():
        target = cfg.filters if hasattr(cfg.filters, key) else (
            cfg.setups if hasattr(cfg.setups, key) else cfg.output
        )
        setattr(target, key, value)
    cfg.validate()
    return cfg


class TestFilters(unittest.TestCase):
    def setUp(self):
        self.cfg = cfg_for(["TEST"])
        self.snap = snapshot.build(make_history(closes=uptrend(260)))
        self.snap.avg_dollar_volume = 500e6
        self.today = date(2026, 9, 5)

    def test_a_healthy_name_passes(self):
        self.assertIsNone(engine.passes_filters(self.snap, self.cfg, None, self.today))

    def test_a_short_history_is_rejected(self):
        snap = snapshot.build(make_history(closes=uptrend(100)))
        reason = engine.passes_filters(snap, self.cfg, None, self.today)
        self.assertIn("bars", reason)

    def test_a_cheap_stock_is_rejected(self):
        self.snap.close = 2.0
        self.assertIn("below minimum", engine.passes_filters(self.snap, self.cfg, None, self.today))

    def test_an_illiquid_name_is_rejected(self):
        self.snap.avg_dollar_volume = 1_000.0
        self.assertIn("dollar volume", engine.passes_filters(self.snap, self.cfg, None, self.today))

    def test_a_dead_quiet_name_is_rejected(self):
        self.snap.atr14 = self.snap.close * 0.0001
        self.assertIn("too quiet", engine.passes_filters(self.snap, self.cfg, None, self.today))

    def test_a_wild_name_is_rejected(self):
        self.snap.atr14 = self.snap.close * 0.5
        self.assertIn("too volatile", engine.passes_filters(self.snap, self.cfg, None, self.today))

    def test_imminent_earnings_are_blocked(self):
        fund = Fundamental(symbol="TEST", source="t", earnings_date=self.today + timedelta(days=1))
        self.assertIn("earnings", engine.passes_filters(self.snap, self.cfg, fund, self.today))

    def test_earnings_beyond_the_blackout_are_fine(self):
        fund = Fundamental(symbol="TEST", source="t", earnings_date=self.today + timedelta(days=30))
        self.assertIsNone(engine.passes_filters(self.snap, self.cfg, fund, self.today))

    def test_past_earnings_do_not_block(self):
        fund = Fundamental(symbol="TEST", source="t", earnings_date=self.today - timedelta(days=2))
        self.assertIsNone(engine.passes_filters(self.snap, self.cfg, fund, self.today))


class TestRegime(unittest.TestCase):
    def test_uptrending_benchmark_is_risk_on(self):
        ctx = classify(snapshot.build(make_history(closes=uptrend(260))), "SPY")
        self.assertEqual(ctx.regime, "risk_on")
        self.assertTrue(ctx.longs_allowed)

    def test_downtrending_benchmark_is_risk_off(self):
        ctx = classify(snapshot.build(make_history(closes=downtrend(260))), "SPY")
        self.assertEqual(ctx.regime, "risk_off")
        self.assertTrue(ctx.shorts_favoured)
        self.assertTrue(any("200-day" in n for n in ctx.notes))

    def test_a_missing_benchmark_is_unknown_but_not_fatal(self):
        ctx = classify(None, "SPY")
        self.assertEqual(ctx.regime, "unknown")
        self.assertTrue(ctx.longs_allowed)
        self.assertTrue(ctx.notes)

    def test_size_factor_respects_the_action(self):
        off = classify(snapshot.build(make_history(closes=downtrend(260))), "SPY")
        self.assertAlmostEqual(off.size_factor("reduce", 0.5), 0.5)
        self.assertAlmostEqual(off.size_factor("ignore", 0.5), 1.0)
        on = classify(snapshot.build(make_history(closes=uptrend(260))), "SPY")
        self.assertAlmostEqual(on.size_factor("reduce", 0.5), 1.0)


class TestFetchHistories(unittest.TestCase):
    def test_the_cache_is_used_and_the_network_is_not(self):
        hist = make_history(symbol="TEST", closes=uptrend(260))
        with TemporaryDirectory() as tmp:
            cache = BarCache(tmp)
            cache.put(hist)
            with mock.patch.object(engine.prices_yahoo, "fetch_history") as fetch:
                out, errors = engine.fetch_histories(["TEST"], 400, cache, delay=0)
        fetch.assert_not_called()
        self.assertEqual(len(out["TEST"]), 260)
        self.assertFalse(errors)

    def test_a_failing_symbol_is_recorded_not_raised(self):
        def boom(symbol, *a, **k):
            raise ValueError(f"{symbol}: delisted")

        with mock.patch.object(engine.prices_yahoo, "fetch_history", boom):
            out, errors = engine.fetch_histories(["BAD"], 400, None, delay=0)
        self.assertFalse(out)
        self.assertIn("delisted", errors["BAD"])

    def test_one_bad_symbol_does_not_stop_the_others(self):
        def maybe(symbol, *a, **k):
            if symbol == "BAD":
                raise ValueError("nope")
            return make_history(symbol=symbol, closes=uptrend(260))

        with mock.patch.object(engine.prices_yahoo, "fetch_history", maybe):
            out, errors = engine.fetch_histories(["GOOD", "BAD", "ALSOGOOD"], 400, None, delay=0)
        self.assertEqual(set(out), {"GOOD", "ALSOGOOD"})
        self.assertEqual(set(errors), {"BAD"})


class TestCache(unittest.TestCase):
    def test_round_trip_preserves_the_bars(self):
        hist = make_history(symbol="RT", closes=uptrend(60))
        with TemporaryDirectory() as tmp:
            cache = BarCache(tmp)
            cache.put(hist)
            back = cache.get("RT")
        self.assertEqual(len(back), len(hist))
        self.assertEqual(back.bars[0].day, hist.bars[0].day)
        self.assertAlmostEqual(back.bars[-1].close, hist.bars[-1].close)
        self.assertIn("cache", back.source)

    def test_a_stale_entry_is_ignored(self):
        with TemporaryDirectory() as tmp:
            cache = BarCache(tmp, max_age_hours=0.0)
            cache.put(make_history(symbol="OLD", closes=uptrend(60)))
            self.assertIsNone(cache.get("OLD"))

    def test_a_corrupt_file_is_ignored_rather_than_crashing(self):
        with TemporaryDirectory() as tmp:
            cache = BarCache(tmp)
            cache.dir.mkdir(parents=True, exist_ok=True)
            (cache.dir / "JUNK.json").write_text("{not json")
            self.assertIsNone(cache.get("JUNK"))

    def test_a_missing_symbol_returns_none(self):
        with TemporaryDirectory() as tmp:
            self.assertIsNone(BarCache(tmp).get("NOPE"))

    def test_clear_removes_everything(self):
        with TemporaryDirectory() as tmp:
            cache = BarCache(tmp)
            cache.put(make_history(symbol="A", closes=uptrend(60)))
            cache.put(make_history(symbol="B", closes=uptrend(60)))
            self.assertEqual(cache.clear(), 2)
            self.assertIsNone(cache.get("A"))


class TestEndToEnd(unittest.TestCase):
    """The whole pipeline, with the network replaced by fixtures."""

    def _run(self, series_by_symbol, **cfg_kw):
        def fake_fetch(symbol, *a, **k):
            closes = series_by_symbol.get(symbol)
            if closes is None:
                raise ValueError(f"{symbol}: no fixture")

            return make_history(symbol=symbol, closes=closes, volumes=[5e6] * len(closes))

        cfg = cfg_for([s for s in series_by_symbol if s != "SPY"], **cfg_kw)
        with mock.patch.object(engine.prices_yahoo, "fetch_history", fake_fetch):
            return engine.run(cfg, use_cache=False), cfg

    def test_a_trending_market_produces_sized_ideas(self):
        brief, cfg = self._run(
            {"SPY": uptrend(260), "AAA": uptrend(260), "BBB": uptrend(260)},
            min_score=0.0, min_avg_dollar_volume=1.0,
        )
        self.assertEqual(brief.context.regime, "risk_on")
        self.assertTrue(brief.picks)
        for pick in brief.picks:
            self.assertGreater(pick.plan.shares, 0)
            self.assertLess(pick.plan.stop, pick.plan.entry)
            self.assertGreater(pick.plan.target, pick.plan.entry)

    def test_total_risk_never_exceeds_the_heat_cap(self):
        brief, cfg = self._run(
            {"SPY": uptrend(260), **{f"S{i}": uptrend(260) for i in range(8)}},
            min_score=0.0, min_avg_dollar_volume=1.0, count=8,
        )
        self.assertLessEqual(
            brief.portfolio["total_risk_pct"], cfg.account.max_open_risk_pct + 1e-6
        )

    def test_an_unreachable_symbol_lands_in_rejections(self):
        # GHOST has no fixture, so its fetch raises and it must be reported.
        brief, _ = self._run(
            {"SPY": uptrend(260), "AAA": uptrend(260), "GHOST": None},
            min_score=0.0, min_avg_dollar_volume=1.0,
        )
        self.assertIn("GHOST", brief.fetch_errors)
        self.assertTrue(
            any(sym == "GHOST" and "unavailable" in reason for sym, reason in brief.rejections)
        )
        self.assertTrue(any(p.symbol == "AAA" for p in brief.picks))

    def test_a_high_threshold_yields_an_empty_but_valid_brief(self):
        brief, cfg = self._run({"SPY": uptrend(260), "AAA": flat(260)}, min_score=99.9)
        self.assertEqual(brief.picks, [])
        self.assertIn("Swing brief", report.to_markdown(brief, cfg))
        self.assertIn("No qualifying setups", report.to_markdown(brief, cfg))

    def test_every_output_format_renders(self):
        import json

        brief, cfg = self._run(
            {"SPY": uptrend(260), "AAA": uptrend(260)}, min_score=0.0, min_avg_dollar_volume=1.0
        )
        self.assertIn("Swing brief", report.to_markdown(brief, cfg))
        html = report.to_html(brief, cfg)
        self.assertTrue(html.startswith("<!doctype html>"))
        self.assertIn("</html>", html)
        payload = json.loads(report.to_json(brief, cfg))
        self.assertEqual(payload["regime"]["benchmark"], "SPY")
        self.assertEqual(len(payload["picks"]), len(brief.picks))

    def test_render_all_writes_the_configured_files(self):
        with TemporaryDirectory() as tmp:
            brief, cfg = self._run(
                {"SPY": uptrend(260), "AAA": uptrend(260)},
                min_score=0.0, min_avg_dollar_volume=1.0, directory=tmp,
            )
            written = report.render_all(brief, cfg)
        self.assertEqual(set(written) - {"latest"}, {"markdown", "html", "json"})

    def test_a_falling_market_halts_longs_when_configured(self):
        def fake_fetch(symbol, *a, **k):
            closes = downtrend(260) if symbol == "SPY" else uptrend(260)
            return make_history(symbol=symbol, closes=closes, volumes=[5e6] * 260)

        cfg = cfg_for(["AAA"], min_score=0.0, min_avg_dollar_volume=1.0)
        cfg.regime.risk_off_action = "halt"
        with mock.patch.object(engine.prices_yahoo, "fetch_history", fake_fetch):
            brief = engine.run(cfg, use_cache=False)
        self.assertEqual(brief.context.regime, "risk_off")
        self.assertEqual(brief.picks, [])

    def test_html_escapes_a_hostile_symbol_name(self):
        brief, cfg = self._run(
            {"SPY": uptrend(260), "AAA": uptrend(260)}, min_score=0.0, min_avg_dollar_volume=1.0
        )
        if brief.picks:
            brief.picks[0].symbol = "<script>alert(1)</script>"
            html = report.to_html(brief, cfg)
            self.assertNotIn("<script>alert(1)</script>", html)
            self.assertIn("&lt;script&gt;", html)


if __name__ == "__main__":
    unittest.main()


class TestDataSession(unittest.TestCase):
    """The brief must say which close its levels came from."""

    def test_the_session_is_recorded_and_rendered(self):
        def fake_fetch(symbol, *a, **k):
            return make_history(symbol=symbol, closes=uptrend(260), volumes=[5e6] * 260)

        cfg = cfg_for(["AAA"], min_score=0.0, min_avg_dollar_volume=1.0)
        with mock.patch.object(engine.prices_yahoo, "fetch_history", fake_fetch):
            brief = engine.run(cfg, use_cache=False)
        self.assertIsNotNone(brief.data_session)
        self.assertIn("levels from the", report.to_markdown(brief, cfg))
        self.assertIn("levels from the", report.to_html(brief, cfg))

    def test_stale_data_is_called_out(self):
        brief = engine.Brief(
            as_of=date(2026, 9, 20),
            generated_at=engine.datetime(2026, 9, 20, 7, 0),
            context=classify(None, "SPY"),
            data_session=date(2026, 9, 1),
        )
        self.assertIn("days old", report._session_line(brief))

    def test_no_session_data_is_stated_not_crashed(self):
        brief = engine.Brief(
            as_of=date(2026, 9, 20),
            generated_at=engine.datetime(2026, 9, 20, 7, 0),
            context=classify(None, "SPY"),
        )
        self.assertEqual(report._session_line(brief), "no session data")
