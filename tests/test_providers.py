import json
import unittest
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from swingtrader.providers import finviz, morningstar, prices_yahoo
from swingtrader.providers.base import Fundamental, History, ProviderStatus
from swingtrader.universe import merge_fundamentals


def chart_payload(days=5, symbol="TEST", with_nulls=False):
    base = 1_700_000_000
    stamps = [base + i * 86_400 for i in range(days)]
    closes = [100.0 + i for i in range(days)]
    if with_nulls:
        closes[2] = None
    return {
        "chart": {
            "error": None,
            "result": [{
                "meta": {"currency": "USD", "symbol": symbol, "fullExchangeName": "NasdaqGS"},
                "timestamp": stamps,
                "indicators": {"quote": [{
                    "open": [c - 0.5 if c else None for c in closes],
                    "high": [c + 1 if c else None for c in closes],
                    "low": [c - 1 if c else None for c in closes],
                    "close": closes,
                    "volume": [1_000_000] * days,
                }]},
            }],
        }
    }


class TestYahooParsing(unittest.TestCase):
    def test_parses_a_normal_payload(self):
        hist = prices_yahoo._parse("TEST", chart_payload(5))
        self.assertEqual(len(hist), 5)
        self.assertEqual(hist.currency, "USD")
        self.assertEqual(hist.exchange, "NasdaqGS")
        self.assertLess(hist.bars[0].day, hist.bars[-1].day)  # oldest first

    def test_null_padded_sessions_are_dropped(self):
        hist = prices_yahoo._parse("TEST", chart_payload(5, with_nulls=True))
        self.assertEqual(len(hist), 4)

    def test_upstream_error_becomes_a_value_error(self):
        payload = {"chart": {"error": {"description": "No data found"}, "result": None}}
        with self.assertRaises(ValueError):
            prices_yahoo._parse("BAD", payload)

    def test_empty_result_raises(self):
        with self.assertRaises(ValueError):
            prices_yahoo._parse("BAD", {"chart": {"result": []}})

    def test_range_selection_covers_the_lookback(self):
        self.assertEqual(prices_yahoo._range_for(5), "5d")
        self.assertEqual(prices_yahoo._range_for(90), "3mo")
        self.assertEqual(prices_yahoo._range_for(400), "2y")
        self.assertEqual(prices_yahoo._range_for(2000), "5y")

    def test_it_retries_the_other_host_on_a_rate_limit(self):
        from swingtrader.http import HttpError

        calls = []

        def fake_get_json(url, **kwargs):
            calls.append(url)
            if len(calls) == 1:
                raise HttpError(url, 429, "Too Many Requests")
            return chart_payload(3)

        with mock.patch.object(prices_yahoo, "get_json", fake_get_json), \
             mock.patch("time.sleep", lambda *_: None):
            hist = prices_yahoo.fetch_history("TEST", 30)
        self.assertEqual(len(hist), 3)
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(calls[0].split("/")[2], calls[1].split("/")[2])


class TestFinvizParsing(unittest.TestCase):
    def test_numbers_with_magnitude_suffixes(self):
        self.assertAlmostEqual(finviz._num("1.23B"), 1.23e9)
        self.assertAlmostEqual(finviz._num("456.7M"), 4.567e8)
        self.assertAlmostEqual(finviz._num("12.5K"), 12_500)
        self.assertAlmostEqual(finviz._num("1,234.5"), 1234.5)
        self.assertIsNone(finviz._num("-"))
        self.assertIsNone(finviz._num(""))
        self.assertIsNone(finviz._num("N/A"))
        self.assertIsNone(finviz._num("wat"))

    def test_percentages_become_fractions(self):
        self.assertAlmostEqual(finviz._pct("3.4%"), 0.034)
        self.assertIsNone(finviz._pct("-"))

    def test_earnings_dates_land_in_a_sensible_window(self):
        parsed = finviz._earnings("Nov 20/a")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.month, 11)
        self.assertEqual(parsed.day, 20)
        self.assertLess(abs((parsed - date.today()).days), 320)
        self.assertIsNone(finviz._earnings("-"))
        self.assertIsNone(finviz._earnings(None))

    def test_rows_become_symbols_and_fundamentals(self):
        rows = [
            {"Ticker": "aapl", "Sector": "Technology", "Market Cap": "3.1B",
             "Analyst Recom": "1.8", "Target Price": "250", "Short Float": "1.2%"},
            {"Ticker": "AAPL", "Sector": "Technology"},   # duplicate
            {"Ticker": "", "Sector": "Nothing"},          # blank
        ]
        self.assertEqual(finviz.symbols(rows), ["AAPL"])
        funds = finviz.to_fundamentals(rows)
        self.assertIn("AAPL", funds)
        self.assertAlmostEqual(funds["AAPL"].market_cap, 3.1e9)
        self.assertAlmostEqual(funds["AAPL"].short_float, 0.012)

    def test_a_sparse_duplicate_row_does_not_clobber_a_richer_one(self):
        rows = [
            {"Ticker": "AAPL", "Market Cap": "3.1B", "Sector": "Technology"},
            {"Ticker": "AAPL"},
        ]
        funds = finviz.to_fundamentals(rows)
        self.assertAlmostEqual(funds["AAPL"].market_cap, 3.1e9)

    def test_screen_without_a_token_raises_rather_than_returning_nothing(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(RuntimeError):
                finviz.screen("cap_large")

    def test_html_response_is_reported_as_a_bad_token(self):
        with mock.patch.dict("os.environ", {"FINVIZ_AUTH_TOKEN": "x"}), \
             mock.patch.object(finviz, "get_text", lambda *a, **k: "<html>login</html>"):
            with self.assertRaises(RuntimeError) as cm:
                finviz.screen("cap_large")
        self.assertIn("token", str(cm.exception))


class TestMorningstarIngest(unittest.TestCase):
    def test_reads_an_export_with_a_preamble(self):
        with TemporaryDirectory() as tmp:
            Path(tmp, "export.csv").write_text(
                "Morningstar Screener Export\n"
                "Generated 2026-09-01\n"
                "Ticker,Star Rating,Fair Value,Economic Moat,Sector\n"
                "AAPL,4,220.50,Wide,Technology\n"
                "MSFT,3,480.00,Wide,Technology\n"
            )
            funds = morningstar.load_exports(tmp)
        self.assertEqual(set(funds), {"AAPL", "MSFT"})
        self.assertAlmostEqual(funds["AAPL"].star_rating, 4.0)
        self.assertAlmostEqual(funds["AAPL"].fair_value, 220.50)
        self.assertEqual(funds["AAPL"].economic_moat, "Wide")

    def test_header_aliases_are_matched_loosely(self):
        with TemporaryDirectory() as tmp:
            Path(tmp, "x.csv").write_text(
                "Symbol,Morningstar Rating,Price/Fair Value,Moat\nNVDA,5,0.85,Wide\n"
            )
            funds = morningstar.load_exports(tmp)
        self.assertAlmostEqual(funds["NVDA"].star_rating, 5.0)
        self.assertAlmostEqual(funds["NVDA"].price_to_fair_value, 0.85)

    def test_missing_directory_is_empty_not_an_error(self):
        self.assertEqual(morningstar.load_exports("/nope/nowhere"), {})

    def test_a_file_with_no_ticker_column_is_skipped(self):
        with TemporaryDirectory() as tmp:
            Path(tmp, "junk.csv").write_text("Name,Value\nfoo,1\n")
            self.assertEqual(morningstar.load_exports(tmp), {})

    def test_direct_without_a_token_raises(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(RuntimeError):
                morningstar.fetch_direct(["AAPL"])


class TestMergeFundamentals(unittest.TestCase):
    def test_later_layers_only_fill_gaps(self):
        first = {"AAPL": Fundamental(symbol="AAPL", source="a", sector="Tech")}
        second = {"AAPL": Fundamental(symbol="AAPL", source="b", sector="Wrong",
                                      analyst_mean_target=250.0)}
        merged = merge_fundamentals(first, second)
        self.assertEqual(merged["AAPL"].sector, "Tech")          # not overwritten
        self.assertAlmostEqual(merged["AAPL"].analyst_mean_target, 250.0)  # gap filled
        self.assertIn("+", merged["AAPL"].source)

    def test_merging_does_not_mutate_the_inputs(self):
        first = {"AAPL": Fundamental(symbol="AAPL", source="a")}
        merge_fundamentals(first, {"AAPL": Fundamental(symbol="AAPL", source="b", sector="Tech")})
        self.assertIsNone(first["AAPL"].sector)


class TestProviderStatus(unittest.TestCase):
    def test_usable_reflects_availability(self):
        self.assertTrue(ProviderStatus("x", "live").usable)
        self.assertTrue(ProviderStatus("x", "configured").usable)
        self.assertFalse(ProviderStatus("x", "unconfigured").usable)
        self.assertFalse(ProviderStatus("x", "unavailable").usable)

    def test_every_shipped_provider_reports_a_status(self):
        from swingtrader.engine import provider_statuses

        for status in provider_statuses():
            self.assertTrue(status.name)
            self.assertTrue(status.detail, f"{status.name} has no explanation")
            self.assertIn(
                status.availability, ("live", "configured", "unconfigured", "unavailable")
            )


if __name__ == "__main__":
    unittest.main()
