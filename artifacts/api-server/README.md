# NIFTY Python API

The original [PRS](../../attached_assets/Product_Requirements_Specification_1791405602882.md) remains the requirements source of truth. This file covers running and calling the implementation.

## Run and verify

From the workspace root:

```sh
uv sync --frozen
PORT=8080 PYTHONPATH=artifacts/api-server uv run --frozen python artifacts/api-server/run.py
PYTHONPATH=artifacts/api-server uv run --frozen python -m unittest discover -s artifacts/api-server/tests -v
```

Runtime dependencies are in the root `pyproject.toml` / `uv.lock`.

- `PORT`: required service port.
- `VPS_DATABASE_URL`: existing VPS PostgreSQL connection, used privately by the server.
- `NIFTY_API_KEY`: optional dedicated API key; otherwise the existing `SESSION_SECRET` is used. No key is exposed in responses or documentation.
- All data operations require the `X-API-Key` header. Health and API documentation are public.
- Health: `/api/healthz`. Interactive contract: `/api/docs`. OpenAPI: `/api/openapi.json`.
- `/api` redirects to the documentation, not the old image lab.

## Operations

Every operation is available both as an API endpoint and as `python -m nifty_api <command>` (run from `artifacts/api-server`, or with it on `PYTHONPATH`). Both share the same code and return the same JSON. The CLI uses `VPS_DATABASE_URL` directly and needs no API key; errors go to stderr in the API's error format.

| Method | Path | CLI | Purpose |
|---|---|---|---|
| POST | `/api/history/sync` | `sync [--rebuild]` | Process every source session not yet flagged; first run loads all history |
| POST | `/api/history/build` | `build --start-date --end-date` | Build an explicit inclusive range (≤366 days) |
| POST | `/api/patterns/encode` | `encode --bars FILE [--complete-session]` | Encode supplied bars without accessing the database |
| POST | `/api/patterns/match` | `match --support-target --trade-date --through-position [--bars FILE]` | Match supplied bars or a source date against strictly earlier days |
| GET | `/api/history/status?config_id=…` | `status` | Stored coverage and processed/excluded day flags |
| POST | `/api/evaluation/walk-forward` | `evaluate --support-target --history-start-date --test-start-date --end-date` | Evaluate stored patterns against the all-prior-days baseline |
| — | — | `serve [--host] [--port]` | Run the HTTP API |

Each CLI data command needs the configuration: `--config FILE` (the JSON below) or `--band-edges 0.25,0.5,1,2 --close-tolerance 0`. `--bars` accepts a JSON array or a CSV with a `timestamp_ist,open,high,low,close` header. `--output FILE` writes the result to a file.

### Keeping history current

```sh
python -m nifty_api sync --config trial.json            # first run: all history; later: only new/gap days
python -m nifty_api sync --config trial.json --rebuild  # reprocess this configuration from scratch
```

Each source session is flagged `processed` or `excluded` (with reason) in `processed_days`, so it is not encoded again. Sync never processes the developing session (only through 15:30 IST), rechecks exclusions among the three most recent sessions, and commits per batch, so an interrupted run resumes on the next sync. Matching and evaluation read the stored days; they never re-encode history. API: `POST /api/history/sync` with `{"config": {...}, "rebuild": false}`.

Example configuration **for experimentation only**, not an API default:

```json
{
  "band_edges_pct": ["0.25", "0.5", "1", "2"],
  "close_tolerance_points": "0"
}
```

Build body:

```json
{
  "config": {"band_edges_pct": ["0.25", "0.5", "1", "2"], "close_tolerance_points": "0"},
  "start_date": "2020-01-01",
  "end_date": "2020-06-30"
}
```

Match body:

```json
{
  "config": {"band_edges_pct": ["0.25", "0.5", "1", "2"], "close_tolerance_points": "0"},
  "support_target": 10,
  "trade_date": "2020-06-30",
  "through_position": 3
}
```

For supplied bars add `bars`, containing `timestamp_ist`, `open`, `high`, `low`, `close`. Timestamps require an offset (e.g. `2020-06-30T09:15:00+05:30`); decimal strings avoid client-side floating-point rounding. DataFrames can call `nifty_api.engine.encode_dataframe(frame, config, position, complete)`.

Evaluation body:

```json
{
  "config": {"band_edges_pct": ["0.25", "0.5", "1", "2"], "close_tolerance_points": "0"},
  "support_target": 10,
  "history_start_date": "2020-01-01",
  "test_start_date": "2020-04-01",
  "end_date": "2020-06-30",
  "positions": [1, 2, 3, 4, 5, 6]
}
```

Build (or sync) identical configurations before matching/evaluating. Different edge values or close tolerances create separate configuration identities; N does not change encoding and can be varied without rebuilding. Build ranges are inclusive and limited to 366 calendar days; successive ranges accumulate history. A rebuild replaces only project-derived records for its exact configuration/date range.

## Data safety and interpretation

Source queries run in PostgreSQL `READ ONLY` transactions. Persistent writes are restricted to four newly created, ownership-marked tables in `nifty_trajectory_v1`: configurations, unique clusters, day patterns, and the processed-days ledger. A pre-ledger schema gains the ledger on its next write. Existing unmarked schema/table collisions cause a failure rather than an overwrite. No operation modifies `public.price_data` or any other pre-existing table.

Incomplete/invalid historical sessions are reported and excluded without filling bars. Same-minute extreme-order ambiguity is retained as `E`. Missing developing-prefix bars produce a structured error; unsupported matches return `match_count: 0, estimate: null`. All supported outcome distributions meet the requested N.

Outcome forecasts are empirical measurements, not a guarantee of predictive advantage or profitability. Review per-hour walk-forward results before choosing numerical configuration or relying on forecasts.

The checked-in validation reports contain the initial 59-day evaluation and a recent 68-day evaluation using 1,655 built sessions through 2026-10-06. Performance differs by period. These are trial configurations, not fitted production defaults.

`verify_live.py` verifies the running API through the local shared proxy, including an actual database-backed build and evaluation. Running it writes only project-owned derived tables. `--full-history` rebuilds the documented 2020–2026 trial configuration; it is not a read-only command.

## Contract synchronization

After API model/route changes:

```sh
PYTHONPATH=artifacts/api-server uv run --frozen python artifacts/api-server/export_openapi.py
```

This writes `artifacts/api-server/openapi.json`, using `/api` as its server prefix and preserving `healthCheck` / `HealthStatus`. There is no frontend.
