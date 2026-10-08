# NIFTY Intraday Trajectory Pattern Engine

Python API and command line. The [PRS](attached_assets/Product_Requirements_Specification_1791405602882.md) is the living requirements source of truth; this file covers running and calling the implementation.

## Layout

| Path | Contents |
|---|---|
| `src/nifty_api/` | Package: engine, matching, evaluation, storage, sync, service, FastAPI app, CLI (`__main__.py`) |
| `tests/` | pytest suite (no database needed) |
| `scripts/` | `verify_live.py` (end-to-end check against a running server), `export_openapi.py` |
| `docs/openapi.json` | Exported API contract |
| `validation/` | Checked-in walk-forward reports |

## Setup and run

```sh
uv sync                      # Python 3.14 (.python-version), installs the package and dev tools
cp .env.example .env         # then fill in VPS_DATABASE_URL (NIFTY_API_KEY optional)
uv run nifty check           # read-only check of database, source indexes and project storage
uv run nifty serve           # API on 127.0.0.1:$PORT (or --host/--port)
uv run pytest                # tests
```

`nifty` and `python -m nifty_api` are the same command. Settings come from the environment, seeded by the nearest `.env` (searching up from the working directory, or `NIFTY_ENV_FILE`); real environment variables win.

- `VPS_DATABASE_URL`: existing VPS PostgreSQL connection, used privately.
- `NIFTY_API_KEY`: optional. When set, API data endpoints require it in the `X-API-Key` header; when unset they are open (internal use), and `serve` warns if bound beyond `127.0.0.1`. Not used by the command line. Never exposed in responses or documentation.
- `PORT`: port for `nifty serve` when `--port` is not given.
- Health (`/api/healthz`), interactive contract (`/api/docs`) and OpenAPI (`/api/openapi.json`) are public. `/api` redirects to the docs.

## Operations

Every operation is available both as an API endpoint and as a `nifty <command>`. Both share the same code and return the same JSON. The CLI uses `VPS_DATABASE_URL` directly and needs no API key; errors go to stderr in the API's error format.

| Method | Path | CLI | Purpose |
|---|---|---|---|
| GET | `/api/system/check` | `check` | Read-only check: source-table indexes and query plans, project tables/indexes |
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
uv run nifty sync --config trial.json            # first run: all history; later: only new/gap days
uv run nifty sync --config trial.json --rebuild  # reprocess this configuration from scratch
```

Use the command line for a first full load; over HTTP it is one long request. Each source session is flagged `processed` or `excluded` (with reason) in `processed_days`, so it is not encoded again. Sync never processes the developing session (only through 15:30 IST), rechecks exclusions among the three most recent sessions, and commits per batch, so an interrupted run resumes on the next sync. Matching and evaluation read the stored days; they never re-encode history. API: `POST /api/history/sync` with `{"config": {...}, "rebuild": false}`.

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

`uv run python scripts/verify_live.py [--base-url http://127.0.0.1:8080]` verifies a running API end to end, including a database-backed build and evaluation. It writes only project-owned derived tables. `--full-history` syncs the documented trial configuration across all history; it is not a read-only command.

## Deploying on Railway

One service, configured by `railway.json`: `nifty serve --host 0.0.0.0`, health check `/api/healthz`. No `.env` file is used there: set **Variables** on the service (Raw Editor accepts `.env` contents). Railway supplies `PORT`.

| Variable | Purpose |
|---|---|
| `VPS_DATABASE_URL` | Required |
| `NIFTY_API_KEY` | Set it whenever the service has a public domain; otherwise every data endpoint, including `sync` with `rebuild`, is open to the internet |
| `SYNC_AT`, `SYNC_CONFIG` | Optional daily sync inside the server, e.g. `SYNC_AT=22:30` (IST) and `SYNC_CONFIG={"band_edges_pct":["0.25","0.5","1","2"],"close_tolerance_points":"0"}` |

**Daily sync.** With both sync variables set, the server runs the same sync as `nifty sync` every day at `SYNC_AT` IST (`price_data` normally finishes loading by 22:00). If the server starts after that time and today's sync has not run, it runs immediately; a failed run retries hourly. Sync only processes unflagged days, so repeats cost a source-date scan. `GET /api/system/check` shows the scheduler's next run and last result. Setting only one of the two variables, or an invalid value, stops the server at startup. Keep Railway's App Sleeping off and run one replica, or the schedule will not fire (or will fire per replica, which is harmless but redundant).

The VPS PostgreSQL must accept connections from Railway's outbound addresses, which are dynamic unless static IPs are enabled. Railpack builds with `uv sync --locked`, so keep `uv.lock` in sync with `pyproject.toml`.

## Indexes

Project-owned indexes (prefix lookup on clusters, cluster/date on day patterns, plus primary keys) are created with the schema and restored by the next build or sync if missing. The source table `public.price_data` is read-only to this project: `nifty check` reports whether source reads use an index and, if not, recommends one for the database owner to create. The project never creates it.

## Contract synchronization

After API model/route changes:

```sh
uv run python scripts/export_openapi.py
```

This writes `docs/openapi.json`, using `/api` as its server prefix and preserving `healthCheck` / `HealthStatus`. There is no frontend.
