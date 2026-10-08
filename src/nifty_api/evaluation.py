"""Walk-forward evaluation against an all-prior-days baseline."""

from datetime import date
from statistics import mean

from .matching import PrefixHistory, distribution, predict


def score(estimate, actual):
    probabilities = [estimate["p_close_above_open"], estimate["p_close_below_open"], estimate["p_close_at_open"]]
    codes = ["7/", "7\\", "7C"]
    brier = sum((p - int(actual["close_code"] == c)) ** 2 for p, c in zip(probabilities, codes))
    return {
        "brier": brier,
        "hit": int(codes[max(range(3), key=lambda i: probabilities[i])] == actual["close_code"]),
        "probability_up": probabilities[0], "actual_up": int(actual["close_code"] == "7/"),
        **{key + "_coverage": int(estimate[key]["p10"] <= actual[key] <= estimate[key]["p90"])
           for key in ("close_pct", "remaining_high_pct", "remaining_low_pct")},
    }


def summarize(rows):
    if not rows:
        return {"sample_count": 0, "brier_score": None, "direction_hit_rate": None,
                "percentile_coverage_80": None, "up_probability_calibration": []}
    calibration = []
    for b in range(10):
        members = [r for r in rows if min(int(r["probability_up"] * 10), 9) == b]
        if members:
            calibration.append({"bin_low": b / 10, "bin_high": (b + 1) / 10, "count": len(members),
                                "mean_predicted": mean(r["probability_up"] for r in members),
                                "observed_frequency": mean(r["actual_up"] for r in members)})
    return {
        "sample_count": len(rows), "brier_score": mean(r["brier"] for r in rows),
        "direction_hit_rate": mean(r["hit"] for r in rows),
        "percentile_coverage_80": {key: mean(r[key + "_coverage"] for r in rows)
                                   for key in ("close_pct", "remaining_high_pct", "remaining_low_pct")},
        "up_probability_calibration": calibration,
    }


def walk_forward(patterns, test_start: date, end: date, support_target: int, positions):
    history = PrefixHistory()
    results = {p: {"tested": 0, "unsupported": 0, "conditional": [], "baseline": [], "baseline_all": [], "backoffs": []}
               for p in positions}
    seen = set()
    for pattern in sorted(patterns, key=lambda x: x["trade_date"]):
        day = date.fromisoformat(pattern["trade_date"])
        if day in seen:
            raise ValueError("Duplicate dates are not valid walk-forward input")
        seen.add(day)
        if test_start <= day <= end:
            for position in positions:
                out = results[position]
                out["tested"] += 1
                actual = pattern["outcomes"][str(position)]
                baseline = distribution(history.members("0O"), position) if history.count("0O") else None
                if baseline:
                    out["baseline_all"].append(score(baseline, actual))
                prediction = predict(pattern["tokens"], position, support_target, history.count, history.members)
                if prediction["estimate"] is None:
                    out["unsupported"] += 1
                else:
                    out["conditional"].append(score(prediction["estimate"], actual))
                    out["baseline"].append(score(baseline, actual))
                    out["backoffs"].append(prediction["backoff_positions"])
        # The test day's outcome enters the index only AFTER its forecasts are scored.
        if day <= end:
            history.add(pattern)
    per_hour = {}
    for p, out in results.items():
        conditional, baseline = summarize(out["conditional"]), summarize(out["baseline"])
        per_hour[str(p)] = {
            "tested_days": out["tested"], "estimated_days": len(out["conditional"]),
            "insufficient_support_days": out["unsupported"],
            "estimate_rate": len(out["conditional"]) / out["tested"] if out["tested"] else 0,
            "mean_backoff_positions": mean(out["backoffs"]) if out["backoffs"] else None,
            "conditional": conditional, "baseline_on_same_days": baseline,
            "baseline_all_test_days": summarize(out["baseline_all"]),
            "brier_improvement_vs_baseline": baseline["brier_score"] - conditional["brier_score"]
            if conditional["sample_count"] else None,
        }
    return {
        "status": "evaluated" if any(x["tested_days"] for x in per_hour.values()) else "no_test_days",
        "method": "walk_forward_all_prior_dates_only", "support_target": support_target,
        "test_start_date": test_start.isoformat(), "end_date": end.isoformat(),
        "per_hour": per_hour,
        "interpretation": "Positive Brier improvement is better than baseline. Measurements do not establish trading profitability.",
    }
