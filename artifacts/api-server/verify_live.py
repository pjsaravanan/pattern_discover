"""Verify the running API through the local shared proxy, without exposing keys."""

from datetime import date
import argparse
import json
import os
from pathlib import Path

import httpx

from nifty_api.storage import PatternStore


def full_history():
    config = {"band_edges_pct": ["0.25", "0.5", "1", "2"], "close_tolerance_points": "0"}
    headers = {"X-API-Key": os.environ.get("NIFTY_API_KEY") or os.environ["SESSION_SECRET"]}
    report = {"trial_configuration": config, "build_batches": [], "evaluations": {},
              "warning": "Explicit trial configuration only. No fitted defaults or profitability claim."}
    with httpx.Client(base_url="http://localhost:80", headers=headers, timeout=180) as client:
        for year in range(2020, 2027):
            end = f"{year}-12-31" if year < 2026 else "2026-10-06"
            response = client.post("/api/history/build", json={
                "config": config, "start_date": f"{year}-01-01", "end_date": end,
            })
            assert response.status_code == 200, f"Build failed: {response.status_code}; {response.text}"
            result = response.json()
            report["build_batches"].append(result)
            print(json.dumps({"year": year, "stored_days": result["stored_days"],
                              "excluded_days": len(result["excluded_sessions"])}), flush=True)
        cid = result["config_id"]
        response = client.get("/api/history/status", params={"config_id": cid})
        assert response.status_code == 200
        report["coverage"] = response.json()
        print(json.dumps({"full_history_coverage": report["coverage"]}), flush=True)
        for n in (5, 10, 20):
            response = client.post("/api/evaluation/walk-forward", json={
                "config": config, "support_target": n, "history_start_date": "2020-01-01",
                "test_start_date": "2026-07-01", "end_date": "2026-10-06",
            })
            assert response.status_code == 200, f"Evaluation failed: {response.status_code}; {response.text}"
            result = response.json()
            report["evaluations"][str(n)] = result
            print(json.dumps({"support_target": n, "recent_per_hour": {
                p: {"tested": x["tested_days"], "estimated": x["estimated_days"],
                    "backoff": x["mean_backoff_positions"], "brier_improvement": x["brier_improvement_vs_baseline"]}
                for p, x in result["per_hour"].items()
            }}), flush=True)
        response = client.post("/api/patterns/match", json={
            "config": config, "support_target": 10, "trade_date": "2026-10-06", "through_position": 3,
        })
        assert response.status_code == 200
        report["latest_source_match"] = response.json()
        print(json.dumps({"latest_source_match": {k: v for k, v in response.json().items() if k != "estimate"}}), flush=True)
    folder = Path(__file__).resolve().parent / "validation"
    folder.mkdir(exist_ok=True)
    (folder / "full_history_walk_forward.json").write_text(json.dumps(report, indent=2) + "\n")
    print("Saved validation/full_history_walk_forward.json; no credentials included.", flush=True)


def main():
    config = {"band_edges_pct": ["0.25", "0.5", "1", "2"], "close_tolerance_points": "0"}
    headers = {"X-API-Key": os.environ.get("NIFTY_API_KEY") or os.environ["SESSION_SECRET"]}
    report = {"trial_configuration": config,
              "warning": "Explicit trial values, not API defaults or production recommendations."}
    # This is the shared reverse proxy, not the service port. Keep keys on loopback.
    with httpx.Client(base_url="http://localhost:80", headers=headers, timeout=120) as client:
        health = client.get("/api/healthz")
        assert health.status_code == 200 and health.json() == {"status": "ok"}
        built = client.post("/api/history/build", json={
            "config": config, "start_date": "2020-01-01", "end_date": "2020-06-30",
        })
        assert built.status_code == 200, f"Build failed: {built.status_code}; {built.text}"
        report["build"] = built.json()
        print(json.dumps({"historical_build": report["build"]}, indent=2))
        cid = built.json()["config_id"]
        status = client.get("/api/history/status", params={"config_id": cid})
        assert status.status_code == 200
        print(json.dumps({"history_status": status.json()}))
        report["evaluations"] = {}
        for n in (5, 10, 20):
            response = client.post("/api/evaluation/walk-forward", json={
                "config": config, "support_target": n, "history_start_date": "2020-01-01",
                "test_start_date": "2020-04-01", "end_date": "2020-06-30",
            })
            assert response.status_code == 200, f"Evaluation failed: {response.status_code}; {response.text}"
            value = response.json()
            report["evaluations"][str(n)] = value
            print(json.dumps({"support_target": n, "per_hour": {
                p: {"tested_days": x["tested_days"], "estimated_days": x["estimated_days"],
                    "mean_backoff_positions": x["mean_backoff_positions"],
                    "brier_improvement": x["brier_improvement_vs_baseline"]}
                for p, x in value["per_hour"].items()
            }}, indent=2))
        matches = {}
        for day, n in (("2020-01-01", 5), ("2020-06-30", 10)):
            response = client.post("/api/patterns/match", json={
                "config": config, "support_target": n, "trade_date": day, "through_position": 3,
            })
            assert response.status_code == 200, f"Match failed: {response.status_code}; {response.text}"
            matches[day] = response.json()
            print(json.dumps({"match": {k: v for k, v in response.json().items() if k != "estimate"}}, indent=2))
        assert matches["2020-01-01"]["match_count"] == 0 and matches["2020-01-01"]["estimate"] is None
        report["live_matches"] = matches
        with PatternStore() as store:
            store.require_configuration(cid)
            patterns = store.range(cid, date(2020, 1, 1), date(2020, 6, 30))
            example = next(p for p in patterns if "\\" in p["path_code"])
            prefix = "|".join(example["tokens"][:3])
            expected = sum(p["path_code"] == prefix or p["path_code"].startswith(prefix + "|") for p in patterns)
            actual = store.count_prefix(cid, prefix, date(2020, 7, 1))
            assert actual == expected
            plan = store.conn.execute(
                """EXPLAIN (FORMAT JSON) SELECT path_code FROM nifty_trajectory_v1.clusters
                   WHERE config_id=%s AND path_code LIKE %s ESCAPE ''""",
                (cid, prefix + "|%"),
            ).fetchone()["QUERY PLAN"]
            report["backslash_lookup"] = {"prefix": prefix, "expected_count": expected,
                                          "actual_count": actual, "normal_plan": plan}
            print(json.dumps({"backslash_lookup": report["backslash_lookup"]}, indent=2))
        report["checks"] = {"health_exact": True, "earliest_day_no_future_matches": True,
                            "backslash_literal_lookup": True, "stored_days": len(patterns)}
    folder = Path(__file__).resolve().parent / "validation"
    folder.mkdir(exist_ok=True)
    (folder / "walk_forward.json").write_text(json.dumps(report, indent=2) + "\n")
    print("Saved validation/walk_forward.json; no credentials included.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full-history", action="store_true", help="Build 2020–2026 history and evaluate recent sessions")
    args = parser.parse_args()
    full_history() if args.full_history else main()
