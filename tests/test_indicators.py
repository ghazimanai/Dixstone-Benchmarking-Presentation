import unittest

from swingtrader import indicators as ind


class TestMovingAverages(unittest.TestCase):
    def test_sma_matches_hand_calculation(self):
        values = [1, 2, 3, 4, 5, 6]
        out = ind.sma(values, 3)
        self.assertEqual(out[:2], [None, None])
        self.assertAlmostEqual(out[2], 2.0)
        self.assertAlmostEqual(out[5], 5.0)

    def test_sma_too_short_is_all_none(self):
        self.assertEqual(ind.sma([1, 2], 5), [None, None])

    def test_ema_seeds_from_sma_then_weights_recent(self):
        values = [float(i) for i in range(1, 11)]
        out = ind.ema(values, 5)
        self.assertAlmostEqual(out[4], 3.0)  # SMA of 1..5
        self.assertGreater(out[9], out[4])
        # A constant series must give a flat EMA at that constant.
        self.assertAlmostEqual(ind.last(ind.ema([7.0] * 20, 5)), 7.0)


class TestRSI(unittest.TestCase):
    def test_monotonic_series_pins_the_extremes(self):
        up = [float(i) for i in range(1, 40)]
        self.assertAlmostEqual(ind.last(ind.rsi(up, 14)), 100.0)
        self.assertAlmostEqual(ind.last(ind.rsi(up[::-1], 14)), 0.0)

    def test_flat_series_is_neutral_ish(self):
        # No gains and no losses: the guard returns 100 rather than dividing by zero.
        self.assertEqual(ind.last(ind.rsi([5.0] * 30, 14)), 100.0)

    def test_warmup_is_none(self):
        out = ind.rsi([float(i) for i in range(30)], 14)
        self.assertTrue(all(v is None for v in out[:14]))
        self.assertIsNotNone(out[14])


class TestRangeIndicators(unittest.TestCase):
    def test_true_range_uses_previous_close(self):
        highs, lows, closes = [10, 12], [9, 11], [9.5, 11.5]
        out = ind.true_range(highs, lows, closes)
        self.assertIsNone(out[0])
        self.assertAlmostEqual(out[1], 2.5)  # 12 - 9.5 beats 12 - 11

    def test_atr_of_constant_range_equals_that_range(self):
        n = 40
        closes = [100.0] * n
        highs = [101.0] * n
        lows = [99.0] * n
        self.assertAlmostEqual(ind.last(ind.atr(highs, lows, closes, 14)), 2.0)

    def test_donchian_excludes_the_current_bar(self):
        highs = [1, 2, 3, 4, 10]
        lows = [1, 1, 1, 1, 1]
        up, dn = ind.donchian(highs, lows, 4)
        self.assertAlmostEqual(up[4], 4.0)  # not 10 -- today is excluded
        self.assertAlmostEqual(dn[4], 1.0)


class TestVolumeAndMomentum(unittest.TestCase):
    def test_relative_volume(self):
        vols = [100.0] * 20 + [250.0]
        self.assertAlmostEqual(ind.last(ind.relative_volume(vols, 20)), 2.5)

    def test_relative_volume_handles_zero_average(self):
        self.assertIsNone(ind.last(ind.relative_volume([0.0] * 21, 20)))

    def test_roc(self):
        self.assertAlmostEqual(ind.last(ind.roc([100.0, 0, 0, 0, 0, 110.0], 5)), 10.0)

    def test_bollinger_bands_straddle_the_mean(self):
        closes = [100.0, 102.0] * 15
        up, mid, lo = ind.bollinger(closes, 20, 2.0)
        self.assertGreater(ind.last(up), ind.last(mid))
        self.assertLess(ind.last(lo), ind.last(mid))

    def test_macd_of_a_flat_series_is_zero(self):
        line, signal, hist = ind.macd([50.0] * 80)
        self.assertAlmostEqual(ind.last(line), 0.0)
        self.assertAlmostEqual(ind.last(hist), 0.0)


class TestHelpers(unittest.TestCase):
    def test_last_with_offset_and_out_of_range(self):
        self.assertEqual(ind.last([1, 2, 3]), 3)
        self.assertEqual(ind.last([1, 2, 3], 2), 1)
        self.assertIsNone(ind.last([1, 2, 3], 9))
        self.assertIsNone(ind.last([]))

    def test_swing_low_and_high_window(self):
        lows = [5, 4, 3, 9, 8, 7]
        self.assertEqual(ind.swing_low(lows, 3), 7)
        self.assertEqual(ind.swing_high([1, 9, 2, 3], 2), 3)


if __name__ == "__main__":
    unittest.main()
