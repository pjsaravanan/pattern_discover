import unittest

from nifty_api.engine import encode_day
from nifty_api.evaluation import walk_forward
from nifty_api.matching import PrefixHistory, percentiles, predict
from fixtures import CONFIG, bars
from datetime import date


class MatchingTests(unittest.TestCase):
    def history(self, count=3):
        h = PrefixHistory()
        for d in range(1, count + 1):
            h.add(encode_day(bars(f"2026-01-{d:02}"), CONFIG, complete=True))
        return h

    def test_exact_match_and_unweighted_probabilities(self):
        h = self.history()
        pattern = encode_day(bars("2026-01-05"), CONFIG)
        result = predict(pattern["tokens"], 6, 3, h.count, h.members)
        self.assertEqual(result["backoff_positions"], 0)
        self.assertEqual(result["match_count"], 3)
        self.assertEqual(result["estimate"]["p_close_above_open"], 1)
        self.assertEqual(result["estimate"]["close_pct"]["p50"], 3)

    def test_unseen_full_prefix_backoff_to_complete_token(self):
        h = self.history()
        tokens = list(h.members("0O")[0]["tokens"])
        tokens[-1] = "6H[Q1]"
        result = predict(tokens, 6, 3, h.count, h.members)
        self.assertEqual(result["requested_prefix_match_count"], 0)
        self.assertEqual(result["backoff_positions"], 1)
        self.assertEqual(result["selected_prefix"], "|".join(tokens[:-1]))
        # Backoff does not move the outcome window back from hour 6 to hour 5.
        self.assertEqual(result["estimate"]["remaining_low_pct"]["p50"], 0)

    def test_no_prefix_reaches_support_returns_zero_and_no_estimate(self):
        h = self.history(2)
        result = predict(h.members("0O")[0]["tokens"], 6, 3, h.count, h.members)
        self.assertEqual(result["status"], "insufficient_support")
        self.assertEqual(result["requested_prefix_match_count"], 2)
        self.assertEqual(result["match_count"], 0)
        self.assertIsNone(result["selected_prefix"])
        self.assertIsNone(result["estimate"])

    def test_empty_history_is_handled(self):
        h = PrefixHistory()
        result = predict(["0O"], 0, 1, h.count, h.members)
        self.assertEqual(result["requested_prefix_match_count"], 0)
        self.assertEqual(result["match_count"], 0)
        self.assertIsNone(result["estimate"])

    def test_adapter_cannot_return_a_below_support_distribution(self):
        h = self.history(2)
        result = predict(["0O"], 0, 3, lambda prefix: 3, h.members)
        self.assertEqual(result["status"], "insufficient_support")
        self.assertIsNone(result["estimate"])

    def test_root_backoff_and_token_boundaries(self):
        h = self.history()
        result = predict(["0O", "1H[Q1]"], 1, 2, h.count, h.members)
        self.assertEqual(result["selected_prefix"], "0O")
        self.assertEqual(h.count("0O|1"), 0)

    def test_percentiles_are_empirical_linear_not_sigma(self):
        self.assertEqual(percentiles([0, 10])["p25"], 2.5)
        self.assertIsNone(percentiles([]))

    def test_walk_forward_cannot_train_on_current_or_future_dates(self):
        patterns = [encode_day(bars(f"2026-01-{d:02}"), CONFIG, complete=True) for d in range(1, 5)]
        result = walk_forward(patterns, date(2026, 1, 1), date(2026, 1, 4), 3, [2, 4])
        for hour in result["per_hour"].values():
            self.assertEqual(hour["tested_days"], 4)
            self.assertEqual(hour["insufficient_support_days"], 3)
            self.assertEqual(hour["estimated_days"], 1)
            self.assertEqual(hour["conditional"]["brier_score"], 0)
            self.assertEqual(hour["baseline_on_same_days"]["sample_count"], 1)
            self.assertEqual(hour["conditional"]["percentile_coverage_80"]["close_pct"], 1)
        earlier = walk_forward(patterns, date(2026, 1, 1), date(2026, 1, 2), 3, [1])
        self.assertEqual(earlier["per_hour"]["1"]["estimated_days"], 0)

    def test_duplicate_dates_not_allowed_in_walk_forward(self):
        p = encode_day(bars(), CONFIG, complete=True)
        with self.assertRaises(ValueError):
            walk_forward([p, p], date(2026, 1, 1), date(2026, 1, 6), 1, [1])
