import unittest

from swingtrader import snapshot
from tests.helpers import downtrend, flat, make_history, uptrend


class TestSnapshotBuild(unittest.TestCase):
    def test_short_history_returns_none(self):
        self.assertIsNone(snapshot.build(make_history(closes=uptrend(20))))

    def test_uptrend_snapshot_is_internally_consistent(self):
        snap = snapshot.build(make_history(closes=uptrend(260)))
        self.assertIsNotNone(snap)
        self.assertEqual(snap.bars, 260)
        self.assertTrue(snap.above_200)
        self.assertTrue(snap.sma50_rising)
        self.assertTrue(snap.stacked_bullish)
        self.assertGreater(snap.close, snap.sma200)
        self.assertGreater(snap.roc63, 0)
        self.assertLessEqual(snap.close, snap.high)
        self.assertGreaterEqual(snap.close, snap.low)

    def test_downtrend_is_not_bullish(self):
        snap = snapshot.build(make_history(closes=downtrend(260)))
        self.assertFalse(snap.above_200)
        self.assertFalse(snap.stacked_bullish)
        self.assertLess(snap.roc63, 0)


class TestDerivedProperties(unittest.TestCase):
    def setUp(self):
        self.snap = snapshot.build(make_history(closes=uptrend(260)))

    def test_atr_pct_is_a_percentage_of_price(self):
        expected = 100.0 * self.snap.atr14 / self.snap.close
        self.assertAlmostEqual(self.snap.atr_pct, expected)

    def test_distance_pct_signs(self):
        self.assertGreater(self.snap.distance_pct(self.snap.close * 0.9), 0)
        self.assertLess(self.snap.distance_pct(self.snap.close * 1.1), 0)
        self.assertIsNone(self.snap.distance_pct(None))
        self.assertIsNone(self.snap.distance_pct(0.0))

    def test_close_position_in_range_is_bounded(self):
        self.assertGreaterEqual(self.snap.close_position_in_range, 0.0)
        self.assertLessEqual(self.snap.close_position_in_range, 1.0)

    def test_pct_from_52w_high_is_never_positive_by_more_than_noise(self):
        self.assertLessEqual(self.snap.pct_from_52w_high, 0.001)


class TestStreaks(unittest.TestCase):
    def test_consecutive_down_counts_only_the_tail(self):
        closes = [10.0] * 250 + [12.0, 11.0, 10.0, 9.0]
        snap = snapshot.build(make_history(closes=closes))
        self.assertEqual(snap.consecutive_down, 3)
        self.assertEqual(snap.consecutive_up, 0)

    def test_consecutive_up(self):
        closes = [10.0] * 250 + [8.0, 9.0, 10.0]
        snap = snapshot.build(make_history(closes=closes))
        self.assertEqual(snap.consecutive_up, 2)


class TestGapDetection(unittest.TestCase):
    def test_planted_gap_is_found_with_its_age(self):
        closes = uptrend(260)
        hist = make_history(closes=closes, gaps={254: 12.0})
        snap = snapshot.build(hist)
        self.assertGreaterEqual(snap.max_gap_pct_10d, 11.9)
        self.assertTrue(snap.event_gap)
        self.assertEqual(snap.days_since_big_gap, 259 - 254)

    def test_no_gap_means_no_event_flag(self):
        snap = snapshot.build(make_history(closes=uptrend(260)))
        self.assertFalse(snap.event_gap)
        self.assertIsNone(snap.days_since_big_gap)

    def test_gap_outside_the_window_is_ignored(self):
        hist = make_history(closes=uptrend(260), gaps={200: 20.0})
        snap = snapshot.build(hist)
        self.assertFalse(snap.event_gap)


class TestTightness(unittest.TestCase):
    def test_flat_tape_is_tighter_than_a_trend(self):
        quiet = snapshot.build(make_history(closes=flat(260))).tightness
        moving = snapshot.build(make_history(closes=uptrend(260))).tightness
        self.assertLess(quiet, moving)

    def test_tightness_resists_a_single_gap(self):
        """The whole point of the median: one gap must not make a name look calm."""
        closes = uptrend(260)
        clean = snapshot.build(make_history(closes=closes)).tightness
        gapped = snapshot.build(make_history(closes=closes, gaps={252: 15.0})).tightness
        # A gap widens the recent range, so tightness rises; it must not collapse
        # toward zero the way an ATR-denominated measure would.
        self.assertGreater(gapped, clean * 0.8)


if __name__ == "__main__":
    unittest.main()
