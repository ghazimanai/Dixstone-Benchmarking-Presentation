import unittest

from swingtrader import snapshot
from swingtrader.config import Config
from swingtrader.regime import MarketContext
from swingtrader.risk import portfolio_summary, size_trade
from swingtrader.setups import SetupResult
from tests.helpers import make_history, uptrend


def result(entry=100.0, stop=95.0, target=110.0) -> SetupResult:
    return SetupResult(
        name="trend_pullback", symbol="TEST", direction="long",
        score=80.0, entry=entry, stop=stop, target=target,
    )


def base_cfg(**account) -> Config:
    cfg = Config()
    cfg.universe.watchlist = ["TEST"]
    for key, value in account.items():
        setattr(cfg.account, key, value)
    cfg.validate()
    return cfg


def liquid_snapshot(avg_dollar_volume=1e9):
    snap = snapshot.build(make_history(closes=uptrend(260)))
    snap.avg_dollar_volume = avg_dollar_volume
    snap.close = 100.0
    return snap


ON = MarketContext(benchmark="SPY", regime="risk_on")
OFF = MarketContext(benchmark="SPY", regime="risk_off")


class TestSizing(unittest.TestCase):
    def test_shares_come_from_the_distance_to_the_stop(self):
        cfg = base_cfg(equity=100_000, risk_per_trade_pct=1.0, max_position_pct=100.0)
        plan = size_trade(result(entry=100, stop=95), liquid_snapshot(), cfg, ON)
        self.assertEqual(plan.shares, 200)         # 1,000 risk / 5 per share
        self.assertAlmostEqual(plan.risk_amount, 1000.0)
        self.assertAlmostEqual(plan.risk_pct_of_equity, 1.0)

    def test_a_wider_stop_buys_fewer_shares_for_the_same_risk(self):
        cfg = base_cfg(equity=100_000, risk_per_trade_pct=1.0, max_position_pct=100.0)
        tight = size_trade(result(entry=100, stop=98), liquid_snapshot(), cfg, ON)
        wide = size_trade(result(entry=100, stop=90), liquid_snapshot(), cfg, ON)
        self.assertGreater(tight.shares, wide.shares)
        self.assertAlmostEqual(tight.risk_amount, wide.risk_amount, delta=10.0)

    def test_reward_risk_is_carried_through(self):
        cfg = base_cfg()
        plan = size_trade(result(entry=100, stop=95, target=115), liquid_snapshot(), cfg, ON)
        self.assertAlmostEqual(plan.reward_risk, 3.0)


class TestCaps(unittest.TestCase):
    def test_notional_cap_binds(self):
        cfg = base_cfg(equity=100_000, risk_per_trade_pct=5.0, max_open_risk_pct=10.0,
                       max_position_pct=10.0)
        plan = size_trade(result(entry=100, stop=99), liquid_snapshot(), cfg, ON)
        self.assertEqual(plan.shares, 100)         # 10,000 cap / 100 per share
        self.assertTrue(any("position cap" in c for c in plan.size_caps_applied))

    def test_liquidity_cap_binds_on_a_thin_name(self):
        cfg = base_cfg(equity=10_000_000, risk_per_trade_pct=1.0, max_position_pct=100.0,
                       max_adv_participation_pct=1.0)
        snap = liquid_snapshot(avg_dollar_volume=1_000_000)  # 10,000 shares/day
        plan = size_trade(result(entry=100, stop=95), snap, cfg, ON)
        self.assertEqual(plan.shares, 100)         # 1% of 10,000 shares
        self.assertTrue(any("liquidity cap" in c for c in plan.size_caps_applied))
        self.assertAlmostEqual(plan.adv_participation_pct, 1.0, places=6)

    def test_portfolio_heat_cap_limits_the_last_idea(self):
        cfg = base_cfg(equity=100_000, risk_per_trade_pct=1.0, max_open_risk_pct=2.5,
                       max_position_pct=100.0)
        plan = size_trade(result(entry=100, stop=95), liquid_snapshot(), cfg, ON,
                          open_risk_used=2_000.0)
        self.assertAlmostEqual(plan.risk_amount, 500.0, delta=5.0)
        self.assertTrue(any("heat cap" in c for c in plan.size_caps_applied))

    def test_no_headroom_means_no_trade(self):
        cfg = base_cfg(equity=100_000, max_open_risk_pct=2.0)
        plan = size_trade(result(), liquid_snapshot(), cfg, ON, open_risk_used=2_000.0)
        self.assertEqual(plan.shares, 0)
        self.assertFalse(plan.tradeable)

    def test_risk_off_regime_halves_the_size(self):
        cfg = base_cfg(equity=100_000, risk_per_trade_pct=1.0, max_position_pct=100.0)
        cfg.regime.risk_off_action = "reduce"
        cfg.regime.reduce_factor = 0.5
        full = size_trade(result(), liquid_snapshot(), cfg, ON)
        reduced = size_trade(result(), liquid_snapshot(), cfg, OFF)
        self.assertEqual(reduced.shares, full.shares // 2)
        self.assertTrue(any("risk-off" in c for c in reduced.size_caps_applied))

    def test_ignore_action_leaves_size_untouched(self):
        cfg = base_cfg(equity=100_000, risk_per_trade_pct=1.0, max_position_pct=100.0)
        cfg.regime.risk_off_action = "ignore"
        self.assertEqual(
            size_trade(result(), liquid_snapshot(), cfg, OFF).shares,
            size_trade(result(), liquid_snapshot(), cfg, ON).shares,
        )


class TestGuards(unittest.TestCase):
    def test_zero_risk_per_share_is_refused(self):
        plan = size_trade(result(entry=100, stop=100), liquid_snapshot(), base_cfg(), ON)
        self.assertEqual(plan.shares, 0)
        self.assertTrue(plan.warnings)

    def test_a_very_wide_stop_is_flagged(self):
        cfg = base_cfg(max_position_pct=100.0)
        plan = size_trade(result(entry=100, stop=70), liquid_snapshot(), cfg, ON)
        self.assertTrue(any("wide" in w for w in plan.warnings))

    def test_stop_distance_pct(self):
        plan = size_trade(result(entry=100, stop=95), liquid_snapshot(), base_cfg(), ON)
        self.assertAlmostEqual(plan.stop_distance_pct, 5.0)


class TestPortfolioSummary(unittest.TestCase):
    def test_totals_and_headroom(self):
        cfg = base_cfg(equity=100_000, risk_per_trade_pct=1.0, max_open_risk_pct=4.0,
                       max_position_pct=100.0)
        plans = [size_trade(result(), liquid_snapshot(), cfg, ON) for _ in range(2)]
        summary = portfolio_summary(plans, cfg)
        self.assertEqual(summary["ideas"], 2.0)
        self.assertAlmostEqual(summary["total_risk_pct"], 2.0, delta=0.05)
        self.assertAlmostEqual(summary["heat_headroom_pct"], 2.0, delta=0.05)

    def test_empty_book_is_all_zeroes(self):
        summary = portfolio_summary([], base_cfg())
        self.assertEqual(summary["ideas"], 0.0)
        self.assertEqual(summary["total_risk"], 0.0)


if __name__ == "__main__":
    unittest.main()
