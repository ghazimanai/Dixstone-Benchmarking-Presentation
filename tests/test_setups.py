import unittest

from swingtrader import setups, snapshot
from swingtrader.config import Config
from swingtrader.regime import MarketContext
from tests.helpers import downtrend, flat, make_history, uptrend


def ctx(regime: str = "risk_on", roc63: float = 5.0) -> MarketContext:
    return MarketContext(benchmark="SPY", regime=regime, roc63=roc63, roc21=1.0)


def cfg(**overrides) -> Config:
    c = Config()
    c.universe.watchlist = ["TEST"]
    for key, value in overrides.items():
        setattr(c.setups, key, value)
    c.validate()
    return c


class TestScoringHelpers(unittest.TestCase):
    def test_ramp_is_clamped_and_linear(self):
        self.assertEqual(setups._ramp(-5, 0, 10), 0.0)
        self.assertEqual(setups._ramp(15, 0, 10), 1.0)
        self.assertAlmostEqual(setups._ramp(2.5, 0, 10), 0.25)
        self.assertEqual(setups._ramp(None, 0, 10), 0.0)

    def test_ramp_handles_a_zero_width_range(self):
        self.assertEqual(setups._ramp(5, 5, 5), 1.0)
        self.assertEqual(setups._ramp(4, 5, 5), 0.0)

    def test_band_plateaus_then_tapers_both_sides(self):
        self.assertEqual(setups._band(0, -10, -2, 2, 10), 1.0)
        self.assertEqual(setups._band(-2, -10, -2, 2, 10), 1.0)
        self.assertEqual(setups._band(-10, -10, -2, 2, 10), 0.0)
        self.assertEqual(setups._band(10, -10, -2, 2, 10), 0.0)
        self.assertAlmostEqual(setups._band(6, -10, -2, 2, 10), 0.5)

    def test_blend_is_a_weighted_average_scaled_to_100(self):
        score, reasons = setups._blend([("a", 1.0, 1.0), ("b", 1.0, 0.0)])
        self.assertAlmostEqual(score, 50.0)
        self.assertEqual(reasons, ["a"])  # only components scoring >= 0.6


class TestGates(unittest.TestCase):
    def test_trend_pullback_requires_price_above_the_200_day(self):
        snap = snapshot.build(make_history(closes=downtrend(260)))
        self.assertIsNone(setups.trend_pullback(snap, ctx(), cfg()))

    def test_breakout_requires_stacked_averages(self):
        snap = snapshot.build(make_history(closes=downtrend(260)))
        self.assertIsNone(setups.breakout(snap, ctx(), cfg()))

    def test_momentum_flag_requires_a_positive_three_month_return(self):
        snap = snapshot.build(make_history(closes=downtrend(260)))
        self.assertIsNone(setups.momentum_flag(snap, ctx(), cfg()))

    def test_oversold_reversion_needs_a_real_flush(self):
        snap = snapshot.build(make_history(closes=uptrend(260)))
        self.assertIsNone(setups.oversold_reversion(snap, ctx(), cfg()))

    def test_oversold_reversion_fires_on_a_flush_inside_an_uptrend(self):
        closes = uptrend(255)
        peak = closes[-1]
        closes += [peak * 0.96, peak * 0.92, peak * 0.88]
        snap = snapshot.build(make_history(closes=closes))
        result = setups.oversold_reversion(snap, ctx(), cfg())
        self.assertIsNotNone(result)
        self.assertEqual(result.direction, "long")
        self.assertLessEqual(result.max_hold_days, 5)
        self.assertEqual(result.entry_style, "limit")

    def test_short_breakdown_needs_a_downtrend(self):
        up = snapshot.build(make_history(closes=uptrend(260)))
        self.assertIsNone(setups.short_breakdown(up, ctx(), cfg()))
        down = snapshot.build(make_history(closes=downtrend(260)))
        self.assertIsNotNone(setups.short_breakdown(down, ctx("risk_off"), cfg()))


class TestLevels(unittest.TestCase):
    """The bug that shipped once: entries drifting far above the last close."""

    def setUp(self):
        self.snap = snapshot.build(make_history(closes=uptrend(260)))
        self.cfg = cfg()

    def test_long_entry_sits_just_above_the_setup_days_high(self):
        entry, _, _ = setups._long_levels(self.snap, self.cfg, self.snap.high)
        self.assertGreater(entry, self.snap.high)
        self.assertLess(entry, self.snap.high * 1.005)

    def test_long_entry_is_never_far_above_the_close(self):
        for fn in (setups.trend_pullback, setups.breakout, setups.momentum_flag):
            result = fn(self.snap, ctx(), self.cfg)
            if result is None:
                continue
            drift = 100.0 * (result.entry / self.snap.close - 1.0)
            self.assertLess(drift, 5.0, f"{result.name} entry is {drift:.1f}% above the close")

    def test_long_stop_is_below_entry_and_target_above(self):
        entry, stop, target = setups._long_levels(self.snap, self.cfg, self.snap.high)
        self.assertLess(stop, entry)
        self.assertGreater(target, entry)

    def test_reward_multiple_is_honoured(self):
        c = cfg(reward_multiple=3.0)
        entry, stop, target = setups._long_levels(self.snap, c, self.snap.high)
        self.assertAlmostEqual((target - entry) / (entry - stop), 3.0, places=6)

    def test_a_distant_swing_low_is_rejected_as_a_stop(self):
        """After a gap the 10-day low can be 20% away. That is not a stop."""
        closes = uptrend(255)
        closes += [closes[-1] * 1.25] * 5  # a violent jump leaves a far-below low
        snap = snapshot.build(make_history(closes=closes))
        entry, stop, _ = setups._long_levels(snap, self.cfg, snap.high)
        self.assertGreaterEqual(stop, entry - 2.5 * snap.atr14 - 0.01)

    def test_short_levels_are_mirrored(self):
        snap = snapshot.build(make_history(closes=downtrend(260)))
        entry, stop, target = setups._short_levels(snap, self.cfg, snap.low)
        self.assertGreater(stop, entry)
        self.assertLess(target, entry)


class TestEvaluate(unittest.TestCase):
    def test_shorts_are_excluded_unless_allowed(self):
        snap = snapshot.build(make_history(closes=downtrend(260)))
        c = cfg()
        c.setups.enabled = ["short_breakdown"]
        c.setups.allow_shorts = False
        self.assertEqual(setups.evaluate(snap, ctx("risk_off"), c), [])
        c.setups.allow_shorts = True
        self.assertTrue(setups.evaluate(snap, ctx("risk_off"), c))

    def test_halt_regime_blocks_new_longs(self):
        snap = snapshot.build(make_history(closes=uptrend(260)))
        c = cfg()
        c.regime.risk_off_action = "halt"
        self.assertEqual(setups.evaluate(snap, ctx("risk_off"), c), [])
        self.assertTrue(setups.evaluate(snap, ctx("risk_on"), c))

    def test_results_come_back_sorted_by_score(self):
        snap = snapshot.build(make_history(closes=uptrend(260)))
        results = setups.evaluate(snap, ctx(), cfg())
        self.assertEqual([r.score for r in results], sorted((r.score for r in results), reverse=True))

    def test_unknown_setup_names_are_skipped_not_fatal(self):
        snap = snapshot.build(make_history(closes=uptrend(260)))
        c = cfg()
        c.setups.enabled = ["does_not_exist", "trend_pullback"]
        setups.evaluate(snap, ctx(), c)  # must not raise

    def test_scores_stay_inside_zero_to_one_hundred(self):
        for closes in (uptrend(260), downtrend(260), flat(260)):
            snap = snapshot.build(make_history(closes=closes))
            c = cfg()
            c.setups.allow_shorts = True
            for res in setups.evaluate(snap, ctx(), c):
                self.assertGreaterEqual(res.score, 0.0)
                self.assertLessEqual(res.score, 100.0)


class TestEventGapPenalty(unittest.TestCase):
    def test_a_gap_driven_flag_scores_below_a_clean_one(self):
        closes = uptrend(260)
        clean = snapshot.build(make_history(closes=closes))
        gapped = snapshot.build(make_history(closes=closes, gaps={253: 12.0}))
        clean_res = setups.momentum_flag(clean, ctx(), cfg())
        gapped_res = setups.momentum_flag(gapped, ctx(), cfg())
        if clean_res and gapped_res:
            self.assertLess(gapped_res.score, clean_res.score)
            self.assertTrue(any("gap" in c for c in gapped_res.cautions))


if __name__ == "__main__":
    unittest.main()
