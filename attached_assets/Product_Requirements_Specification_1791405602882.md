# Product Requirements Specification
## Intraday Trajectory Pattern Engine (NIFTY)

**Status:** Draft v0.2 (living document)
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

1. **Path code** – a string recording, hour by hour, whether the day made a new session high, a new session low, both (including which came first), or neither. Magnitude-band codes are also included in v1; their exact boundaries and placement remain to be defined.
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

A path string built up one position at a time. The base event notation is digit (position) + symbol. V1 also includes magnitude-band code(s) for applicable leg displacement(s); the exact band labels and token placement are open questions in §11. Matching must use complete position tokens, not partial tokens.

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

The base event portion has 14 characters for positions 0–6. The full stored path length depends on the magnitude-band encoding.

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
| 7C | Close at Open; tolerance is to be determined after reviewing sample data |

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
- `sequence` is either O→H→L→C or O→L→H→C.
- Legs are derived from points; points are the source of truth.
- Raw `dt` and `dq` are retained. V1 assigns magnitude-band code(s) to applicable leg displacements; band boundaries and exact encoding remain open pending data review.

## 7. Matching and prediction

### 7.1 Matching

At the end of each completed hourly bucket, build the current day's path-code prefix through that position and select historical days whose path code starts with it (index prefix lookup). Compare only dates earlier than the current date. Only unique path codes are searched; there is no full-table similarity scan. Position 7 remains the separate final 15-minute bucket and is not merged into position 6 or added to the matching prefix.

### 7.2 Minimum sample and backoff

**N** is the support target and will be chosen after reviewing the data and walk-forward results; 15–20 is not yet a fixed value. If the requested prefix has fewer than N historical matches, back off to the longest shorter prefix that meets N. Report the requested prefix and its match count, the selected prefix and its match count, and the backoff depth.

If the requested prefix has no matches, report `requested_prefix_match_count: 0`. A broader prefix may still be used if it meets N. If no prefix meets N, return `status: "insufficient_support"`, `selected_prefix: null`, `match_count: 0`, and no estimate. Do not calculate or return a low-support distribution below N.

### 7.3 Time weighting

Do not apply time weighting to v1 outcome distributions. Defer the proposed **w = 0.1 × position** (or any alternative weighting function) until walk-forward evaluation.

### 7.4 Outcome distribution

From the matched days, compute for the remaining day:

- P(close above Open), P(close below Open)
- Distribution of close %, remaining high %, remaining low %
- Distribution of dt and dq for the next expected leg

Use **empirical percentiles** (e.g. P10/P25/P50/P75/P90) of the matched days rather than normal-curve sigma bands, since intraday moves are skewed and fat-tailed.

### 7.5 Output per hour

The Python API returns machine-readable JSON. A supported estimate includes the requested and selected prefixes, match counts, backoff depth, status, and outcome distributions. For example:

```json
{
  "time": "11:15",
  "requested_prefix": "0O1H2X",
  "requested_prefix_match_count": 0,
  "selected_prefix": "0O1H",
  "match_count": 64,
  "backoff_positions": 1,
  "status": "estimated",
  "p_close_above_open": 0.68,
  "p_close_below_open": 0.32,
  "close_pct": {
    "p25": 0.12,
    "p50": 0.41,
    "p75": 0.78
  },
  "next_leg": {
    "event": "L",
    "dt_p50_hours": 1.6,
    "dq_p50_pct": -0.55
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

Initial read-only review found an additional 09:14 IST row in recent sampled sessions. Its exclusion and the start-stamped minute convention are approved as defined in §4.

```sql
CREATE TABLE day_pattern (
    id            BIGSERIAL PRIMARY KEY,
    symbol        TEXT        NOT NULL,
    trade_date    DATE        NOT NULL,
    open          NUMERIC     NOT NULL,
    high          NUMERIC     NOT NULL,
    low           NUMERIC     NOT NULL,
    close         NUMERIC     NOT NULL,
    high_time     TIMESTAMPTZ NOT NULL,
    low_time      TIMESTAMPTZ NOT NULL,
    path_code     TEXT        NOT NULL,  -- positions 0–6; event and magnitude-band codes
    close_code    TEXT        NOT NULL,  -- position 7: 7/, 7\, or 7C
    legs          JSONB       NOT NULL,  -- §6
    UNIQUE (symbol, trade_date)
);

CREATE INDEX day_pattern_path_idx
    ON day_pattern (symbol, path_code text_pattern_ops);
```

Query:

```sql
SELECT *
FROM day_pattern
WHERE symbol = 'NIFTY'
  AND path_code LIKE :path_prefix_pattern ESCAPE ''
  AND trade_date < :as_of_date;
```

Notes:
- `:path_prefix_pattern` is a parameter containing the complete prefix followed by `%`. Use a parameterized query; do not interpolate path codes into SQL.
- PostgreSQL's `ESCAPE ''` disables backslash escape handling for `LIKE`, so a prefix containing `\` is treated literally. Verify with `EXPLAIN` that the left-anchored prefix query continues to use the B-tree index. `ILIKE` or a leading `%` will not use that index.
- In JSON text, a single backslash is serialized as `\\`; normal JSON serialization/deserialization preserves the intended one-character code.
- A summary table (path prefix → member count, outcome percentiles) can be materialised for instant reads.

## 9. Validation

- **Walk-forward only.** For each test day, build buckets solely from days before it.
- **Baseline.** Compare each prefix's outcome distribution against the unconditional distribution of all prior days.
- **Success criterion.** Prefix-conditioned outcomes differ meaningfully from the baseline at hours 2–4, and the difference grows as the day progresses.
- Report hit rate, calibration of P(Up), and percentile coverage per hour.

## 10. Scope

**In scope (v1):** NIFTY index; one-minute OHLC source data resampled into hourly path buckets; a Python API only (no UI); historical build; hourly prefix matching; magnitude-band coding; JSON output; PostgreSQL storage in new project-owned tables; and walk-forward evaluation.

**Implementation target:** Replace the existing Express/TypeScript API Server artifact with Python rather than create a separate service or project. Preserve `GET /api/healthz` returning `{"status":"ok"}` and leave the image-analysis web artifact unchanged.

**HTTP framework:** FastAPI is approved. HTTP responses are JSON; exact pattern-operation request/response contracts remain to be finalized.

**Out of scope (v1):** image rendering, DTW, HDBSCAN or K-Means, neural embeddings, ML models, other symbols, order execution.

## 11. Open questions

1. Tolerance for classifying position 7 as `7C` (close at Open).
2. Support target N, to be selected after reviewing the historical data and walk-forward results.
3. Magnitude-band boundaries, labels, and exact placement in path-code tokens.
4. Whether to introduce time weighting after walk-forward evaluation; no weighting is applied in v1.
5. Complete one-minute data quality review and agree on handling incomplete or ambiguous sessions for `NIFTY` / `NSE` / `1m` in `public.price_data`.
6. Exact pattern-operation request/response contracts; FastAPI HTTP JSON transport is approved.
