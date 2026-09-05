import os
import tempfile
import unittest
from pathlib import Path

from swingtrader.config import Config, _mini_yaml, _scalar


MINIMAL = {"universe": {"watchlist": ["AAPL"]}}


class TestDefaults(unittest.TestCase):
    def test_no_path_and_no_file_falls_back_to_defaults(self):
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                cfg = Config.load()
            finally:
                os.chdir(cwd)
        self.assertEqual(cfg.account.equity, 100_000.0)

    def test_explicit_missing_path_raises(self):
        with self.assertRaises(FileNotFoundError):
            Config.load("/nope/swingtrader.yml")

    def test_shipped_config_is_valid(self):
        path = Path("config/swingtrader.yml")
        if not path.exists():
            self.skipTest("shipped config not present")
        cfg = Config.load(path)
        self.assertGreater(cfg.account.equity, 0)
        self.assertTrue(cfg.universe.watchlist)
        self.assertTrue(cfg.setups.enabled)


class TestValidation(unittest.TestCase):
    def test_negative_equity_is_rejected(self):
        with self.assertRaises(ValueError) as cm:
            Config.from_dict({**MINIMAL, "account": {"equity": -5}})
        self.assertIn("equity", str(cm.exception))

    def test_heat_cap_below_per_trade_risk_is_rejected(self):
        with self.assertRaises(ValueError) as cm:
            Config.from_dict(
                {**MINIMAL, "account": {"risk_per_trade_pct": 3.0, "max_open_risk_pct": 1.0}}
            )
        self.assertIn("max_open_risk_pct", str(cm.exception))

    def test_inverted_atr_band_is_rejected(self):
        with self.assertRaises(ValueError):
            Config.from_dict({**MINIMAL, "filters": {"min_atr_pct": 9.0, "max_atr_pct": 2.0}})

    def test_bad_regime_action_is_rejected(self):
        with self.assertRaises(ValueError):
            Config.from_dict({**MINIMAL, "regime": {"risk_off_action": "panic"}})

    def test_empty_universe_is_rejected(self):
        with self.assertRaises(ValueError):
            Config.from_dict({"universe": {"watchlist": []}})

    def test_a_typo_in_a_key_is_not_silently_ignored(self):
        with self.assertRaises(ValueError) as cm:
            Config.from_dict({**MINIMAL, "account": {"eqity": 1000}})
        self.assertIn("eqity", str(cm.exception))


class TestNormalisation(unittest.TestCase):
    def test_symbols_are_upper_cased(self):
        cfg = Config.from_dict({"universe": {"watchlist": ["aapl", "Msft"]}})
        self.assertEqual(cfg.universe.watchlist, ["AAPL", "MSFT"])

    def test_screens_become_dataclasses(self):
        cfg = Config.from_dict(
            {**MINIMAL, "universe": {"watchlist": ["A"], "finviz_screens": [
                {"name": "s1", "filters": "cap_large", "limit": 5}]}}
        )
        self.assertEqual(cfg.universe.finviz_screens[0].name, "s1")
        self.assertEqual(cfg.universe.finviz_screens[0].limit, 5)


class TestEnvExpansion(unittest.TestCase):
    def test_env_placeholder_is_expanded(self):
        os.environ["SWINGTRADER_TEST_SYM"] = "TSLA"
        try:
            cfg = Config.from_dict({"universe": {"watchlist": ["${SWINGTRADER_TEST_SYM}"]}})
            self.assertEqual(cfg.universe.watchlist, ["TSLA"])
        finally:
            del os.environ["SWINGTRADER_TEST_SYM"]

    def test_default_is_used_when_unset(self):
        os.environ.pop("SWINGTRADER_ABSENT", None)
        cfg = Config.from_dict({"universe": {"watchlist": ["${SWINGTRADER_ABSENT:-QQQ}"]}})
        self.assertEqual(cfg.universe.watchlist, ["QQQ"])


class TestMiniYaml(unittest.TestCase):
    """The fallback parser used when PyYAML is not installed."""

    def test_nested_mappings_and_lists(self):
        parsed = _mini_yaml(
            "account:\n"
            "  equity: 5000\n"
            "  currency: USD\n"
            "universe:\n"
            "  watchlist:\n"
            "    - AAPL\n"
            "    - MSFT\n"
        )
        self.assertEqual(parsed["account"]["equity"], 5000)
        self.assertEqual(parsed["account"]["currency"], "USD")
        self.assertEqual(parsed["universe"]["watchlist"], ["AAPL", "MSFT"])

    def test_scalars(self):
        self.assertIs(_scalar("true"), True)
        self.assertIs(_scalar("false"), False)
        self.assertIsNone(_scalar("null"))
        self.assertEqual(_scalar("12"), 12)
        self.assertEqual(_scalar("1.5"), 1.5)
        self.assertEqual(_scalar("[a, b]"), ["a", "b"])
        self.assertEqual(_scalar("[]"), [])
        self.assertEqual(_scalar('"hi"'), "hi")

    def test_list_of_mappings(self):
        parsed = _mini_yaml(
            "universe:\n"
            "  finviz_screens:\n"
            "    - name: one\n"
            "      filters: cap_large\n"
            "      limit: 10\n"
            "    - name: two\n"
            "      filters: cap_small\n"
        )
        screens = parsed["universe"]["finviz_screens"]
        self.assertEqual(len(screens), 2)
        self.assertEqual(screens[0], {"name": "one", "filters": "cap_large", "limit": 10})
        self.assertEqual(screens[1]["name"], "two")

    def test_list_at_the_keys_own_indent(self):
        parsed = _mini_yaml("formats:\n- html\n- json\n")
        self.assertEqual(parsed["formats"], ["html", "json"])

    def test_a_url_scalar_is_not_split_as_a_pair(self):
        parsed = _mini_yaml("links:\n  - https://example.com/x\n")
        self.assertEqual(parsed["links"], ["https://example.com/x"])

    def test_it_parses_the_shipped_config_the_same_way_pyyaml_does(self):
        path = Path("config/swingtrader.yml")
        if not path.exists():
            self.skipTest("shipped config not present")
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        text = path.read_text()
        self.assertEqual(_mini_yaml(text), yaml.safe_load(text))

    def test_comments_are_ignored(self):
        parsed = _mini_yaml("# top\naccount:\n  equity: 1\n  # inline note\n")
        self.assertEqual(parsed["account"]["equity"], 1)


if __name__ == "__main__":
    unittest.main()
