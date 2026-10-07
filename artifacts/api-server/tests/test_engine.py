import json
import unittest
from datetime import timedelta
from decimal import Decimal

from pydantic import ValidationError

from nifty_api.engine import band, encode_dataframe, encode_day
from nifty_api.errors import PatternError
from nifty_api.models import PatternConfig
from fixtures import CONFIG, bars


class EncodingTests(unittest.TestCase):
    def test_all_symbols_and_separate_final_bucket(self):
        result = encode_day(bars(), CONFIG, complete=True)
        self.assertEqual([b["symbol"] for b in result["buckets"]], ["\\", "H", "L", "E", "/", "X"])
        self.assertEqual(len(result["tokens"]), 7)
        self.assertTrue(result["path_code"].startswith("0O|1\\["))
        self.assertNotIn("7/", result["path_code"])
        self.assertEqual(result["close_code"], "7/")
        self.assertEqual(result["final_bucket"]["minute_count"], 15)
        self.assertEqual(result["high"], "107")
        self.assertEqual(result["buckets"][-1]["high"], "100")

    def test_compare_with_day_not_previous_bucket(self):
        rows = bars()
        rows[60:180] = [x.model_copy(update={"high": Decimal(102), "low": Decimal(98)}) for x in rows[60:180]]
        rows[120] = rows[120].model_copy(update={"high": Decimal("102.5"), "low": Decimal("97.5")})
        result = encode_day(rows, CONFIG, position=3)
        self.assertEqual([b["symbol"] for b in result["buckets"]], ["\\", "X", "X"])

    def test_initial_same_minute_pair_is_recomputed_at_close(self):
        rows = bars()
        provisional = encode_day(rows[:60], CONFIG, position=1)
        self.assertEqual(provisional["buckets"][0]["symbol"], "\\")
        self.assertEqual(provisional["buckets"][0]["high_time"][11:16], "09:20")
        self.assertEqual(provisional["buckets"][0]["low_time"][11:16], "09:30")

    def test_equal_extrema_are_not_new(self):
        rows = bars()
        rows[60] = rows[60].model_copy(update={"high": Decimal(103), "low": Decimal(97)})
        self.assertEqual(encode_day(rows, CONFIG, position=2)["buckets"][1]["symbol"], "X")

    def test_ignore_0914_and_future_without_leaking_outcomes(self):
        rows = bars()
        extra = rows[0].model_copy(update={"timestamp_ist": rows[0].timestamp_ist - timedelta(minutes=1),
                                          "open": Decimal(90), "high": Decimal(110), "low": Decimal(80)})
        result = encode_day([extra] + rows, CONFIG, position=2)
        self.assertEqual(result["open"], "100")
        self.assertEqual(result["out_of_session_rows_ignored"], 1)
        self.assertEqual(result["future_session_rows_ignored"], 255)
        self.assertIsNone(result["close_code"])
        self.assertNotIn("outcomes", result)
        changed = [x.model_copy(update={"high": Decimal(500)}) if i >= 120 else x for i, x in enumerate(rows)]
        self.assertEqual(result["path_code"], encode_day(changed, CONFIG, position=2)["path_code"])

    def test_missing_and_duplicate_minutes_are_explicit(self):
        for rows in (bars()[1:], bars() + [bars()[0]]):
            with self.assertRaises(PatternError) as error:
                encode_day(rows, CONFIG, complete=True)
            self.assertIn(error.exception.code, ("incomplete_session", "duplicate_minutes"))
        result = encode_day(bars()[:120], CONFIG, position=2)
        self.assertFalse(result["complete_session"])

    def test_mixed_dates_and_invalid_ohlc_rejected(self):
        with self.assertRaises(PatternError):
            encode_day([bars()[0], bars("2026-01-06")[0]], CONFIG, position=0)
        bad = bars()[0].model_dump()
        bad["high"] = 50
        with self.assertRaises(ValidationError):
            encode_day([bad], CONFIG, position=0)

    def test_bands_and_configuration_identity(self):
        self.assertEqual(band(Decimal(".25"), CONFIG), "Q1")
        self.assertEqual(band(Decimal("-.251"), CONFIG), "Q2")
        self.assertEqual(band(Decimal(3), CONFIG), "Q5")
        equivalent = PatternConfig(band_edges_pct=[".2500", ".500", "1.00", "2.0"], close_tolerance_points="0.00")
        self.assertEqual(equivalent.identity, CONFIG.identity)
        for edges in ([], [0], [1, 1], [2, 1], ["NaN"]):
            with self.subTest(edges=edges), self.assertRaises(ValidationError):
                PatternConfig(band_edges_pct=edges, close_tolerance_points=0)
        with self.assertRaises(ValidationError):
            PatternConfig()

    def test_close_tolerance_and_decimal_precision(self):
        config = PatternConfig(band_edges_pct=[1], close_tolerance_points="3")
        self.assertEqual(encode_day(bars(), config, complete=True)["close_code"], "7C")
        rows = bars()
        rows[-1] = rows[-1].model_copy(update={"high": Decimal(100), "low": Decimal(99), "close": Decimal(99)})
        self.assertEqual(encode_day(rows, CONFIG, complete=True)["close_code"], "7\\")

    def test_remaining_range_uses_only_future_minutes(self):
        result = encode_day(bars(), CONFIG, complete=True)
        outcome = result["outcomes"]["6"]
        self.assertEqual(outcome["remaining_low_pct"], 0)
        self.assertEqual(outcome["remaining_high_pct"], 7)
        self.assertEqual(outcome["next_leg"]["event"], "H")
        self.assertAlmostEqual(outcome["next_leg"]["dt_hours"], 10 / 60)
        self.assertEqual(outcome["next_leg"]["dq_pct"], 7)
        self.assertEqual(result["measurements"]["points"]["C"]["t"], 6.25)

    def test_backslash_json_roundtrip(self):
        pattern = encode_day(bars(), CONFIG, complete=True)
        self.assertEqual(json.loads(json.dumps(pattern))["path_code"], pattern["path_code"])

    def test_dataframe_adapter_ignores_non_ohlc_metadata(self):
        class Frame:
            columns = ["timestamp_ist", "open", "high", "low", "close", "volume", "symbol"]
            def to_dict(self, orient):
                return [{**row.model_dump(), "volume": 0, "symbol": "NIFTY"} for row in bars()]
        self.assertEqual(encode_dataframe(Frame(), CONFIG, complete=True)["path_code"],
                         encode_day(bars(), CONFIG, complete=True)["path_code"])

    def test_global_same_minute_extrema_preserve_unknown_order(self):
        rows = bars()
        rows[180] = rows[180].model_copy(update={"high": Decimal(120), "low": Decimal(80)})
        pattern = encode_day(rows, CONFIG, complete=True)
        self.assertEqual(pattern["measurements"]["sequence"], ["O", "E", "C"])
        self.assertTrue(pattern["measurements"]["order_unknown"])
        self.assertEqual(pattern["outcomes"]["1"]["next_leg"]["event"], "E")
        self.assertIsNone(pattern["outcomes"]["1"]["next_leg"]["dq_pct"])
