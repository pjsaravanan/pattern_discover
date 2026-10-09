# Product Requirements Specification
## Intraday Trajectory Pattern Engine (NIFTY)

**Status:** v1 implemented (living document); v1 does not beat the current-side rule (§9.2); improvements tracked in §12
**Created:** 2026-10-08
**Updated:** 2026-10-08
**Owner:** Saravanan

This is the canonical living requirements document. Record approved clarifications and deviations here.

---

## 1. Purpose

Predict the probable direction and range of the current trading day by matching how the day has moved so far against how historical days moved, and reading the outcomes of the historical days that took the same path.

As the day advances, candidate historical paths narrow. The predictive value of each additional hour is evaluated during walk-forward validation.

## 2. Core idea

Every trading day is reduced to its structural movement: Open, the session's new high/low extremes, and Close, with the time each one happened. Intermediate fluctuations inside a leg are deliberately ignored. When a single hourly bucket makes both a new session high and a new session low, their order is retained.

Each day is encoded twice:

1. **Path code** – a string recording, hour by hour, whether the day made a new session high, a new session low, both (including which came first when known), or neither. Magnitude-band codes are included in v1 as defined in §5.1.
2. **Leg measurements** – the time (dt) and percentage displacement (dq) of each leg, computed from that day's own data. Raw measurements are retained alongside the magnitude bands.

Days that share a path code form a cluster automatically. A developing day is matched by the prefix of its path code, so candidates narrow with every completed hour without any similarity search.

## 3. Principles

- **Each day is unique.** Displacements are percentage from that day's Open. No cross-day scaling, no historical denominators, no normalisation beyond percent-from-open.
- **Time is part of the pattern.** A high in hour 1 and a high in hour 4 are different patterns. Time is never stretched or warped.
- **Quantum is preserved.** A +10% day and a +2% day with the same path are kept distinct through their leg measurements.
- **Simple first.** No image rendering, no DTW, no density clustering, no ML models in the first version.

## 4. Session and time buckets

NSE cash session: 09:15–15:30 IST.

One-minute NIFTY OHLC bars are the source data and are resampled into hourly pattern buckets. The API does not generate minute-level patterns or minute-level predictions.

Source timestamps mark each minute's start. Include only bars in `[09:15, 15:30)` IST; exclude the additional 09:14 row. Session Open is the 09:15 bar's `open`, and session Close is the 15:29 bar's `close`, representing the session ending at 15:30.

| Position | Window |
|---|---|
| 0 | Open (09:15) |
| 1 | 09:15–10:15 |
| 2 | 10:15–11:15 |
| 3 | 11:15–12:15 |
| 4 | 12:15–13:15 |
| 5 | 13:15–14:15 |
| 6 | 14:15–15:15 |
| 7 | Separate final 15-minute bucket, 15:15–15:30, and session Close |

Position 7 remains separate and is not merged into position 6. Its final close code is an outcome, not part of the matching prefix.

## 5. Path code

### 5.1 Format

A path string built up one position at a time, with complete tokens separated by `|`. A token is digit (position) + symbol, followed by magnitude bands where applicable. Band edges are required explicit API configuration, in ascending positive percentage points. `Q1` is at or below the first edge; `Q2` is above the first and at or below the second, and so on; the last band is above the last edge. An empty edge list is not allowed.

For H, band the absolute percentage displacement from session Open to the bucket High: `1H[Q2]`. For L, band the absolute displacement from Open to the bucket Low: `2L[Q1]`. For `/`, put Low then High bands, e.g. `3/[Q1,Q3]`; for `\`, put High then Low bands. For `E`, use the deterministic Low, High band order without claiming chronological order. X has no bands. Raw point and leg measurements remain available.

`path_code` covers positions 0–6 only. The position 7 close outcome is stored separately and is not part of the matching key. Examples below show the base event notation without the yet-to-be-defined magnitude-band code(s).

```
start  0O
t1     0O1H
t2     0O1H2X
t3     0O1H2X3/
t4     0O1H2X3/4\
t5     0O1H2X3/4\5L
t6     0O1H2X3/4\5L6H
close  close_code = 7/
```

The base event portion without separators has 14 characters for positions 0–6. Stored matching keys include separators and magnitude bands. For example: `0O|1H[Q2]|2X|3/[Q1,Q3]`. Prefixes always end on a complete token; position 7 is never appended.

### 5.2 Symbols for positions 1–6

| Symbol | Meaning |
|---|---|
| H | Made a new session high only (above Open and any earlier high) |
| L | Made a new session low only (below Open and any earlier low) |
| X | Neither: made no new session high or low |
| / | Made both a new session low and a new session high in this hourly bucket, **low first then high** |
| \ | Made both a new session high and a new session low in this hourly bucket, **high first then low** |
| E | Made both new daily extremes, but their order is unavailable because the bucket's High and Low share the same one-minute timestamp |

For each completed bucket in positions 1–6, compare that bucket's highest High and lowest Low with the day's running High and Low established **before the bucket**. Initialize both running references at the session Open. A strictly higher bucket High breaks the day's High; a strictly lower bucket Low breaks the day's Low. Equal values do not count as new extremes. Make both comparisons against the same pre-bucket references, then update the day's running High and Low to include the bucket.

When both extremes are broken, determine `/` or `\` from the timestamps of the bucket's highest High and lowest Low, using the one-minute source bars—not the timestamps of the first boundary crossings. If those timestamps are equal, use `E` provisionally; retain the bucket and day rather than flagging them as unusable or excluding them solely for unknown order. Do not infer an intraminute sequence from OHLC.

Recalculate the bucket's extrema and provisional symbol as further bars arrive. An early same-minute pair does not freeze the bucket as `E`; later extrema may establish `/` or `\`. Finalize the symbol only when the bucket closes. The `E` rule may be reviewed after examining practical historical examples; no alternative order rule is approved yet.

Read-only review of complete `NIFTY` / `NSE` / `1m` source sessions found these examples of same-minute final bucket extrema that both break the prior running daily extremes (all times IST):

| Date | Bucket | Bucket interval | Shared extreme timestamp | Bucket High | Bucket Low |
|---|---|---|---|---|---|
| 2020-01-29 | 5 | 13:15–14:15 | 13:30 | 12169.60 | 12103.80 |
| 2020-03-13 | 1 | 09:15–10:15 | 10:00 | 9166.90 | 8476.15 |

These are observations from the approved source, not independent validation of market prices or intraminute order. The second example's large one-minute range warrants source-quality review; retain the provisional `E` treatment rather than silently discarding or correcting the data.

These symbols describe the day's running extremes, not comparisons with the previous candle or the eventual end-of-day extremes. They replace the draft's order-specific `B` and `D` symbols. `E` applies to positions 1–6 and does not change the position 7 close codes.

Contingency only: if `/` and `\` cause implementation or search complications, use `U` for low-to-high and `D` for high-to-low instead. This fallback is not currently adopted; if used, its `D` meaning supersedes the old draft's `D` meaning.

### 5.3 Position 7 (outcome)

Position 7 is the **close outcome**, not part of the intraday matching key. It is stored separately in `close_code` (see §8).

| Code | Meaning |
|---|---|
| 7/ | Close above Open |
| 7\ | Close below Open |
| 7C | Absolute Close-minus-Open is at or below the caller's explicit `close_tolerance_points` |

Position 7 uses this close-relative-to-Open code in v1. `C` in `7C` means the close is at Open; `C` in the leg `sequence` in §6 denotes the Close event.

## 6. Leg measurements

Stored per day as JSONB, from that day's own data only.

```json
{
  "date": "2026-10-07",
  "open": 25100,
  "sequence": ["O", "H", "L", "C"],
  "points": {
    "O": {"t": 0.00, "pct": 0.00},
    "H": {"t": 1.35, "pct": 1.24},
    "L": {"t": 3.82, "pct": -0.73},
    "C": {"t": 6.25, "pct": 0.61}
  },
  "legs": {
    "OH": {"dt": 1.35, "dq": 1.24},
    "OL": {"dt": 3.82, "dq": -0.73},
    "HL": {"dt": 2.47, "dq": 1.97},
    "HC": {"dt": 4.90, "dq": 0.63},
    "LC": {"dt": 2.43, "dq": 1.34}
  },
  "magnitude_bands": {
    "OH": "<band-code>",
    "OL": "<band-code>",
    "HL": "<band-code>",
    "HC": "<band-code>",
    "LC": "<band-code>"
  }
}
```

- `t` is elapsed trading hours from 09:15 (fractional, e.g. 10:12 → 0.95).
- `sequence` is either O→H→L→C or O→L→H→C. When the final daily High and Low have the same one-minute timestamp, use O→E→C and retain both H/L points with `order_unknown: true`.
- Legs are derived from points; points are the source of truth.
- Raw `dt` and `dq` are retained. OH/OL use signed percentage displacement from Open; HL/HC/LC use absolute endpoint percentage differences. All `dt` values are absolute endpoint time differences. Band each leg's absolute `dq` using the same explicit band edges.

## 7. Matching and prediction

### 7.1 Matching

At the end of each completed hourly bucket, build the current day's path-code prefix through that position and select historical days whose path code starts with it (index prefix lookup). Compare only dates earlier than the current date. Only unique path codes are searched; there is no full-table similarity scan. Position 7 remains the separate final 15-minute bucket and is not merged into position 6 or added to the matching prefix.

### 7.2 Minimum sample and backoff

**N** is the required explicit `support_target` API input, with no numerical default. Band edges and close tolerance also have no defaults. Configurations can be compared during walk-forward evaluation before selecting fixed values. If the requested prefix has fewer than N historical matches, back off to the longest shorter prefix that meets N, including `0O` if necessary. Report the requested prefix and its match count, the selected prefix and its match count, and the backoff depth.

If the requested prefix has no matches, report `requested_prefix_match_count: 0`. A broader prefix may still be used if it meets N. If no prefix meets N, return `status: "insufficient_support"`, `selected_prefix: null`, `match_count: 0`, and no estimate. Do not calculate or return a low-support distribution below N.

### 7.3 Time weighting

Do not apply time weighting to v1 outcome distributions. Defer the proposed **w = 0.1 × position** (or any alternative weighting function) until walk-forward evaluation.

### 7.4 Outcome distribution

From the matched days, compute for the remaining day:

- P(close above Open), P(close below Open)
- Distribution of close %, remaining high %, remaining low %
- Distribution of dt and dq for the next expected leg

Use **empirical percentiles** (e.g. P10/P25/P50/P75/P90) of the matched days rather than normal-curve sigma bands, since intraday moves are skewed and fat-tailed.

Remaining extrema use only historical bars at or after the requested hour's cutoff, even when matching backs off to an earlier prefix. Percentages retain each historical day's own Open as denominator. The next structural leg targets the next final daily H/L event after the cutoff, or C if neither remains. Its dt is from the cutoff and dq is from that historical day's price at the cutoff; tied H/L targets are E, with separate low/high displacement bounds rather than an invented direction. Include P(close at Open), event probabilities, and empirical distributions by next-event type.

### 7.5 Output per hour

The Python API returns machine-readable JSON. A supported estimate includes the requested and selected prefixes, match counts, backoff depth, status, and nested `estimate` outcome distributions. Abbreviated example of the verified source-date match for 2026-10-06 at position 3 (the full response also includes next-leg and magnitude-band distributions):

```json
{
  "trade_date": "2026-10-06",
  "through_position": 3,
  "time": "12:15",
  "requested_prefix": "0O|1/[Q1,Q2]|2H[Q2]|3H[Q2]",
  "requested_prefix_match_count": 20,
  "selected_prefix": "0O|1/[Q1,Q2]|2H[Q2]|3H[Q2]",
  "match_count": 20,
  "backoff_positions": 0,
  "status": "estimated",
  "support_target": 10,
  "estimate": {
    "p_close_above_open": 0.95,
    "p_close_below_open": 0.05,
    "p_close_at_open": 0.0,
    "close_pct": {
      "p10": 0.0983431981,
      "p25": 0.2958049358,
      "p50": 0.4947391505,
      "p75": 0.6552922828,
      "p90": 0.8010549856
    }
  }
}
```

When no prefix meets N, return no estimate:

```json
{
  "requested_prefix_match_count": 0,
  "selected_prefix": null,
  "match_count": 0,
  "status": "insufficient_support",
  "estimate": null
}
```

The response may also include the selected days' magnitude-band distribution.

The JSON response replaces the draft's text-table example. Position 7 outcome uses `close_code` (e.g. `7/`, `7\`, or `7C`) and is not part of the matching prefix.

## 8. Data model (PostgreSQL)

The approved source is `public.price_data` in the user's VPS PostgreSQL database, selecting `symbol = 'NIFTY'`, `exchange = 'NSE'`, `timeframe = '1m'`, using `timestamp_ist` (timestamp with time zone) and its `open`, `high`, `low`, and `close` columns. VPS connectivity and these source fields have been verified read-only; data quality still requires review. Existing tables are read-only: the project may query them but must never insert, update, delete, alter, drop, or otherwise modify them. The project may create and write to new project-owned tables.

**Current-day source (2026-10-08):** `public.price_data` is loaded only after a session closes, so the developing session is read from the live feed `public.candles`: `symbol = 'NIFTY'`, `timeframe = '1m'` (its `exchange` is blank), using `ts` (timestamp with time zone, minute-start) and `open`, `high`, `low`, `close`. `candles` is equally read-only. A match without supplied bars reads `candles` when `trade_date` is the current IST date and `price_data` for any earlier date; the response's `bar_source` names the table used. History (build/sync) and evaluation use `price_data` only. Read-only comparison for 2026-10-06 found the same instrument and minute convention, but 146 of 374 overlapping minutes differ slightly (typically a few index points in open/high/low), and `candles` lacked one minute that day; a developing-day code from `candles` may therefore occasionally differ from the code later built from `price_data`. NIFTYFUT in `candles` is a separate instrument and is not used.

Initial read-only review found an additional 09:14 IST row in recent sampled sessions. Its exclusion and the start-stamped minute convention are approved as defined in §4.

The implemented storage supersedes the draft's unqualified `day_pattern` table. Four tables are created only in a new dedicated schema, `nifty_trajectory_v1`. Schema and tables carry an application ownership marker; initialization refuses any unowned or unmarked collision. Do not run these declarations manually against existing objects.

```sql
CREATE TABLE nifty_trajectory_v1.configurations (
    id TEXT PRIMARY KEY,
    settings JSONB NOT NULL
);
CREATE TABLE nifty_trajectory_v1.clusters (
    config_id TEXT NOT NULL REFERENCES nifty_trajectory_v1.configurations(id),
    path_code TEXT COLLATE "C" NOT NULL,
    PRIMARY KEY (config_id, path_code)
);
CREATE TABLE nifty_trajectory_v1.day_patterns (
    config_id TEXT NOT NULL,
    trade_date DATE NOT NULL,
    path_code TEXT COLLATE "C" NOT NULL,
    pattern JSONB NOT NULL,
    PRIMARY KEY (config_id, trade_date),
    FOREIGN KEY (config_id, path_code)
      REFERENCES nifty_trajectory_v1.clusters(config_id, path_code)
);
CREATE INDEX nifty_clusters_prefix_idx
  ON nifty_trajectory_v1.clusters(config_id, path_code text_pattern_ops);
CREATE INDEX nifty_days_cluster_date_idx
  ON nifty_trajectory_v1.day_patterns(config_id, path_code, trade_date);
CREATE TABLE nifty_trajectory_v1.processed_days (
    config_id TEXT NOT NULL REFERENCES nifty_trajectory_v1.configurations(id),
    trade_date DATE NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('processed', 'excluded')),
    reason TEXT,
    details JSONB NOT NULL DEFAULT '{}',
    processed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (config_id, trade_date)
);
```

`processed_days` is the per-configuration, per-date processing ledger (added 2026-10-08). Every source session date handled by a build or sync is flagged `processed` (stored in `day_patterns`) or `excluded` (with the exclusion reason and details). On an existing ownership-marked schema without this table, the next write operation creates and marks it, and flags every date already in `day_patterns` as `processed`; earlier excluded dates are re-examined once.

### 8.1 History sync and rebuild

Historical days are encoded once, stored, and then referenced by matching and evaluation; they are not re-encoded per request. **Sync** keeps a configuration current:

- It reads, read-only, the distinct source session dates up to the **last complete session**: today once it is 15:30 IST or later, otherwise the previous calendar date. A developing session is never flagged.
- Pending dates are source dates with no ledger flag, plus `excluded` dates among the three most recent source sessions (rechecked in case late source data completes them). The first run for a configuration therefore processes all available history; later runs process only new or gap dates.
- Pending dates are processed in chunks of consecutive source sessions spanning at most 366 calendar days. Each chunk's day patterns and ledger flags are written in one transaction, so an interrupted sync resumes at the next run.
- **Rebuild** deletes only that configuration's ledger flags, day patterns and clusters (never source data or other configurations), then syncs everything up to the last complete session. An interrupted rebuild is completed by a later plain sync.
- The explicit range build replaces stored rows and ledger flags for its inclusive range.

The complete JSONB pattern retains symbol, OHLC values, extrema timestamps, tokens, separate close code/final bucket, raw points/legs, magnitude bands, and outcomes at each hourly cutoff. Prices are decimal strings to retain precision. Configurations fingerprint the encoding version, band edges and close tolerance; N is a separate matching/evaluation input. Rebuilding a range replaces only derived rows for that configuration/range.

Query:

```sql
SELECT d.pattern
FROM nifty_trajectory_v1.clusters c
JOIN nifty_trajectory_v1.day_patterns d
  ON d.config_id = c.config_id AND d.path_code = c.path_code
WHERE c.config_id = :config_id
  AND (c.path_code = :complete_prefix
       OR c.path_code LIKE :complete_prefix_with_separator_and_percent ESCAPE '')
  AND d.trade_date < :as_of_date;
```

Notes:
- The LIKE parameter contains the complete prefix followed by `|%`; the equality branch handles a complete six-position key. Use parameterized queries, never interpolated path codes. This enforces complete-token boundaries.
- PostgreSQL's `ESCAPE ''` disables backslash escape handling for `LIKE`, so a prefix containing `\` is treated literally. Verify with `EXPLAIN` that the left-anchored prefix query continues to use the B-tree index. `ILIKE` or a leading `%` will not use that index.
- In JSON text, a single backslash is serialized as `\\`; normal JSON serialization/deserialization preserves the intended one-character code.
- A summary table (path prefix → member count, outcome percentiles) can be materialised for instant reads.
- Source transactions are PostgreSQL READ ONLY. Matching reads use a REPEATABLE READ, READ ONLY snapshot so concurrent project rebuilds cannot change support between counting and fetching members.

### 8.2 Indexes (2026-10-08)

- **Project-owned:** primary keys on all four tables, `nifty_clusters_prefix_idx` (prefix lookup) and `nifty_days_cluster_date_idx` (cluster join and date bound). Ledger reads/deletes and range reads use the `(config_id, trade_date)` primary keys. These indexes are created with the schema; on an existing owned schema, any missing one is recreated (`CREATE INDEX IF NOT EXISTS`) by the next build or sync.
- **Source (`public.price_data`):** read-only to the project, so the project never creates source indexes. Source reads filter on `symbol`, `exchange`, `timeframe` and a `timestamp_ist` range; an index leading with those columns, e.g. `(symbol, exchange, timeframe, timestamp_ist)`, is recommended for the database owner if absent.
- **Check operation:** `check` (CLI) / `GET /api/system/check` (API) is read-only. It lists source indexes, runs plain `EXPLAIN` (no execution) on the session-read and session-date queries to report whether they avoid sequential scans, and reports project schema/table ownership and missing owned indexes. Status is `attention` when a source read uses a sequential scan or an unmarked project-schema collision exists.

## 9. Validation

- **Walk-forward only.** For each test day, build buckets solely from days before it.
- **Baseline.** Compare each prefix's outcome distribution against the unconditional distribution of all prior days.
- **Current-side baseline (added 2026-10-08).** Also compare against the rule "the close ends on the same side of the open as price is at the cutoff". This is the primary benchmark: the engine adds value only where it beats this rule (see §9.2).
- **Success criterion.** Prefix-conditioned outcomes differ meaningfully from the baseline at hours 2–4, and the difference grows as the day progresses.
- Report hit rate, calibration of P(Up), and percentile coverage per hour.

### 9.1 Verified implementation and trial results

- Automated checks cover encoding, all event symbols, close tolerance, magnitude edges, complete-token backoff, zero/support-shortage results, future-bar/date exclusion, API-key enforcement, JSON serialization, and source/project write boundaries.
- Live builds from 2020-01-01 through 2026-10-06 produced 1,655 complete sessions and 1,339 unique banded patterns. Fifteen observed incomplete/invalid sessions were excluded with explicit reasons; E days were retained. Existing source tables were queried only.
- Trial configuration: band edges `[0.25, 0.5, 1, 2]` percentage points and zero-point close tolerance, evaluated at N=5, 10 and 20. These are trial inputs, not API defaults or selected production parameters.
- April–June 2020 walk-forward evaluation (59 test days) did not show a probability-score advantage over the unconditional baseline for these trials.
- July–October 2026 evaluation (68 test days), trained only on earlier dates, showed positive Brier-score improvement at hours 2–4 for all three trial support targets. At N=20, improvements were approximately 0.0975, 0.1087 and 0.1044 respectively. Improvements were not monotonic with hour, and performance differs by period; the research success criterion is not established universally.
- Full JSON results include direction hit rates, P(Up) calibration bins, P10–P90 coverage, and backoff/support metrics in `validation/walk_forward.json` and `validation/full_history_walk_forward.json`.
- A live literal-backslash prefix lookup returned exactly the expected member count; normal EXPLAIN used an Index Only Scan on the unique-cluster B-tree.

No profitability claim or fixed production configuration follows from these limited evaluations. Up/Down/At-open probabilities use the configured close-tolerance categories.

### 9.2 Benchmark against the current-side rule (2026-10-08)

External review, verified against the stored history (1,656 days through 2026-10-07) and `validation/full_history_walk_forward.json`: the current-side rule beats the engine's direction hit rate at every hour.

| Hour | Engine hit, N=5/10/20 (Jul–Oct 2026) | Mean backoff, N=20 | Rule, same 68 days | Rule, all 1,656 days |
|---|---|---|---|---|
| 1 | 0.66 / 0.68 / 0.68 | 0.04 | 0.74 | 0.69 |
| 2 | 0.66 / 0.68 / 0.66 | 0.31 | 0.71 | 0.73 |
| 3 | 0.66 / 0.65 / 0.66 | 1.03 | 0.76 | 0.77 |
| 4 | 0.65 / 0.65 / 0.66 | 1.97 | 0.82 | 0.81 |
| 5 | 0.65 / 0.65 / 0.66 | 2.97 | 0.87 | 0.87 |
| 6 | 0.65 / 0.65 / 0.66 | 3.97 | 0.91 | 0.96 |

Findings:
- Banded codes are near-unique (1,340 codes for 1,656 days), so backoff grows by about one position per hour; afternoon estimates rest on the first one or two hours.
- Outcome distributions are close-from-Open percentages of matched days, so after backoff the current day's price at the cutoff is not used at all. This, more than sparsity alone, is why the engine is flat (~0.66) while the rule rises to 0.96.
- Close-percentile 80% coverage at N=20 is 0.84–0.90 (too wide early).

Conclusion: v1 does not add directional value over the current-side rule. Remedies are tracked in §12 (B1, B2 first).

## 10. Scope

**In scope (v1):** NIFTY index; one-minute OHLC source data resampled into hourly path buckets; a Python API only (no UI); historical build; hourly prefix matching; magnitude-band coding; JSON output; PostgreSQL storage in new project-owned tables; and walk-forward evaluation.

**Implementation target:** Replace the existing Express/TypeScript API Server artifact with Python rather than create a separate service or project. Preserve `GET /api/healthz` returning `{"status":"ok"}`.

**Deviation (2026-10-08):** The repository is Python-only. All TypeScript/Node workspace packages (image-analysis web artifact, mockup sandbox, generated TS/Zod clients, Drizzle DB package, pnpm workspace) and Replit scaffolding were removed.

**Layout and configuration (2026-10-08):** Installable `src` layout: package `src/nifty_api/`, tests `tests/` (pytest), scripts `scripts/`, exported contract `docs/openapi.json`, validation reports `validation/`. Python 3.14 via `uv` (`requires-python >= 3.13`). Settings come from environment variables, seeded by the nearest `.env` file (`.env.example` documents them; real environment variables take precedence): `VPS_DATABASE_URL`, `NIFTY_API_KEY` (optional; the Replit `SESSION_SECRET` fallback was removed), and `PORT`. The first full history load is run from the command line (`nifty sync`).

**Deployment (2026-10-08):** Railway is the target host, configured from the repository. A single service: `railway.json` runs the API (`nifty serve --host 0.0.0.0`, health check `/api/healthz`). Settings are Railway service variables rather than a `.env` file. A publicly exposed API service must set `NIFTY_API_KEY`.

**Daily sync scheduler (2026-10-08, supersedes the separate cron service to avoid a second service):** when both `SYNC_AT` (`HH:MM`, IST) and `SYNC_CONFIG` (configuration JSON) are set, the API server runs the §8.1 sync in-process once per day at `SYNC_AT`; `price_data` normally completes by 22:00 IST, so 22:30 is the intended value. Neither set disables it; one set or an invalid value fails server startup. A server started after the day's slot whose sync has not run in that process runs it immediately; a failed run retries hourly; failures are recorded by error code without exception text. Because sync is idempotent (§8.1), catch-up or repeated runs only rescan source dates. `check` reports the scheduler's configuration, next run and last result. The scheduler requires an awake, single-replica server.

**HTTP framework:** FastAPI is approved. HTTP request/response contracts are implemented and documented in `/api/docs`, `/api/openapi.json`, and the exported OpenAPI file. Errors use a consistent JSON envelope containing `error`, `message`, and `details`, without credential or raw-input echoes.

**Interfaces (2026-10-08):** Every operation is available both as a command-line command (`nifty <command>`, equivalently `python -m nifty_api <command>`) and as an API endpoint, sharing the same service code and JSON results. Commands: `serve`, `check`, `encode`, `build`, `sync` (with `--rebuild`), `status`, `match`, `evaluate`; endpoints are listed in `/api/docs`. The command line runs under the operator's own database credentials and needs no API key; it applies the same validation and write boundaries.

Approved completion boundary: implement JSON operations for historical build, history sync/rebuild (§8.1), encoding supplied one-minute bars, developing-day matching, and walk-forward evaluation. Require explicit `band_edges_pct`, `close_tolerance_points`, and, where estimates are requested, `support_target`; there are no guessed numerical defaults. API contracts are documented in `/api/docs` and `/api/openapi.json`. **Deviation (2026-10-08):** the API key is optional because use is internal and experimental. When `NIFTY_API_KEY` is set, data and build/evaluation endpoints require it in `X-API-Key`; when unset they are open, and `serve` warns if bound beyond loopback (default host `127.0.0.1`). Health and contract documentation remain public.

Data quality: exclude incomplete or invalid historical sessions and report dates and reasons; do not fill or fabricate bars. Require 375 unique session minutes for a historical day. A developing-day request requires all minutes through its requested completed bucket, not a complete future session. Ignore out-of-session rows such as 09:14 and ignore future bars when encoding a requested prefix. E alone never excludes a day.

Storage: use only newly created, ownership-marked tables in a dedicated project schema, refusing collisions with unmarked existing objects. Store separate configuration identities so records built with different band edges or close tolerances cannot be mixed.

**Out of scope (v1):** image rendering, DTW, HDBSCAN or K-Means, neural embeddings, ML models, other symbols, order execution.

## 11. Open questions

1. Select preferred numerical configurations after reviewing walk-forward results; these are explicit API inputs, not blockers or implicit defaults.
2. Whether to introduce time weighting after walk-forward evaluation; no weighting is applied in v1.
3. Review unusual source-price observations and the provisional E rule with further historical evidence; no source rows are modified or silently corrected.
4. Should estimates be anchored at the cutoff price (matched days supply the remaining move), replacing close-from-Open distributions? (§9.2, §12 B1)
5. What code granularity (bands or none, number of bands) keeps afternoon prefixes supported without backoff? (§12 B2)
6. Is ML (v2) in scope once the current-side benchmark is in place? v1 excludes ML (§10). (§12 C)

## 12. Ideas and action items (tracked)

Living backlog for this project. Every idea or action item raised in discussion, review or operations is recorded here with its source and status, and updated as it is decided or done. Status: `idea` (not yet agreed), `open` (agreed, not started), `in progress`, `done`, `dropped`. Requirement changes that follow from an item are still written into the relevant section above.

### A. Validation

| ID | Item | Source | Status |
|---|---|---|---|
| A1 | Adopt the current-side rule as the primary baseline in walk-forward reports (§9) | Review 2026-10-08 | open |
| A2 | Full-history walk-forward 2021–2026, per hour, with bootstrap confidence intervals and breakdown by year and VIX regime | Discussion 2026-10-08 | open |
| A3 | Score ranges with pinball loss / CRPS, not only P10–P90 coverage | Discussion 2026-10-08 | idea |
| A4 | Log every live hourly forecast for a genuine out-of-sample record | Discussion 2026-10-08 | idea |

### B. Engine (within v1 principles unless noted)

| ID | Item | Source | Status |
|---|---|---|---|
| B1 | Anchor at the cutoff: matched days supply the remaining move from the cutoff, added to the current day's level; P(up) from that | Review 2026-10-08 | open (first) |
| B2 | Coarser code (no bands, or two) so afternoon prefixes keep support | Review 2026-10-08 | open |
| B3 | Merge neighbouring bands before dropping hours when support is short | Discussion 2026-10-08 | idea |
| B4 | Shrink low-support estimates toward the baseline instead of a hard N cutoff | Discussion 2026-10-08 | idea |
| B5 | Context keys/filters: opening gap, previous day's code and close, INDIA VIX, expiry day, day of week | Discussion 2026-10-08 | idea |
| B6 | Volatility-relative bands (deviates from §3 "no cross-day scaling"; test as an alternative) | Discussion 2026-10-08 | idea |
| B7 | Time weighting (§7.3) once A2 exists | PRS §7.3 | idea |
| B8 | "Range from here" (remaining high/low) as a primary output, with B2 | Review 2026-10-08 | idea |

### C. ML track (v2; outside v1 scope)

| ID | Item | Source | Status |
|---|---|---|---|
| C1 | Nearest neighbours on time-aligned trajectories (% from open, 5-minute steps; no time warping) | Discussion 2026-10-08 | idea |
| C2 | LightGBM quantile benchmark on cutoff features | Discussion 2026-10-08 | idea |
| C3 | Trajectory clustering for discovery (shape and magnitude separately) | Discussion 2026-10-08 | idea |
| C4 | Metric learning / embeddings, only after C1–C2 | Discussion 2026-10-08 | idea |
| C5 | Images for visualisation only (today's path over matched days, fan chart) | Discussion 2026-10-08 | idea |

### D. Data and operations

| ID | Item | Source | Status |
|---|---|---|---|
| D1 | Missed `price_data` day (2026-10-07): filled via chronosdata; reliability follow-ups tracked in chronosdata `NOTES.md` | Operations 2026-10-08 | done (fill); follow-ups open there |
| D2 | Review `candles` vs `price_data` differences and the 15 excluded days (§8, §11.3) | Operations 2026-10-08 | open |
| D3 | Redirect `/` and `/docs` to `/api/docs` | User 2026-10-08 | idea |
| D4 | Rotate the VPS database password (it appeared in a working session) | Operations 2026-10-08 | open |
| D5 | Railway API key set and verified (401 without key) | Operations 2026-10-08 | done |
| D6 | Daily in-process sync live on Railway at 22:30 IST; confirm first run result | Operations 2026-10-08 | done (first run 2026-10-08 22:30, synced, 0 pending because `price_data` lacked 2026-10-08) |
| D7 | `price_data` missed 2026-10-08 as well (chronosdata 22:03 run fetched nothing again); second occurrence of chronosdata N2 | Operations 2026-10-09 | done (filled 2026-10-09 via chronosdata incremental; synced, 1,657 days through 2026-10-08); root cause tracked as chronosdata N2 |

### E. Cross-project

| ID | Item | Source | Status |
|---|---|---|---|
| E1 | gocharting comparison (2026-10-08): the "price vs open" rule beats the engine at every hour (0.74 vs 0.66 at 10:15; 0.91 vs 0.65 at 15:15, Jul–Oct 2026); gocharting would use the engine only once A1/B2/B8 show value — cross-ref gocharting `BACKLOG.md` A17 | Review 2026-10-08 (gocharting session) | idea |
