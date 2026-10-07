# Product Requirements Specification
## Intraday Trajectory Pattern Engine (NIFTY)

**Status:** Draft v0.1
**Date:** 2026-10-08
**Owner:** Saravanan

---

## 1. Purpose

Predict the probable direction and range of the current trading day by matching how the day has moved so far against how historical days moved, and reading the outcomes of the historical days that took the same path.

The more of the day has elapsed, the fewer historical paths remain consistent with it, and the more weight that evidence carries.

## 2. Core idea

Every trading day is reduced to its structural movement: Open, the first extreme, the second extreme and Close, with the time each one happened. Intermediate fluctuations inside a leg are deliberately ignored.

Each day is encoded twice:

1. **Path code** – a short string recording, hour by hour, whether the day made a new high, a new low, both, or neither. This is the matching key and the cluster.
2. **Leg measurements** – the time (dt) and percentage displacement (dq) of each leg, computed from that day's own data. These give the magnitude distribution within a cluster.

Days that share a path code form a cluster automatically. A developing day is matched by the prefix of its path code, so candidates narrow with every hour without any similarity search.

## 3. Principles

- **Each day is unique.** Displacements are percentage from that day's Open. No cross-day scaling, no historical denominators, no normalisation beyond percent-from-open.
- **Time is part of the pattern.** A high in hour 1 and a high in hour 4 are different patterns. Time is never stretched or warped.
- **Quantum is preserved.** A +10% day and a +2% day with the same path are kept distinct through their leg measurements.
- **Simple first.** No image rendering, no DTW, no density clustering, no ML models in the first version.

## 4. Session and time buckets

NSE cash session: 09:15–15:30 IST.

| Position | Window |
|---|---|
| 0 | Open (09:15) |
| 1 | 09:15–10:15 |
| 2 | 10:15–11:15 |
| 3 | 11:15–12:15 |
| 4 | 12:15–13:15 |
| 5 | 13:15–14:15 |
| 6 | 14:15–15:15 |
| 7 | 15:15–15:30 and Close |

## 5. Path code

### 5.1 Format

A string built up one position at a time: digit (position) + symbol.

```
start  0O
t1     0O1H
t2     0O1H2X
t3     0O1H2X3X
t4     0O1H2X3X4L
t5     0O1H2X3X4L5L
t6     0O1H2X3X4L5L6H
close  0O1H2X3X4L5L6H7H
```

Full length is 16 characters.

### 5.2 Symbols for positions 1–6

| Symbol | Meaning |
|---|---|
| H | Made a new high of the day (above Open and above any earlier high) |
| L | Made a new low of the day (below Open and below any earlier low) |
| X | Neither: stayed inside the existing range |
| B | Made both, **high first then low** |
| D | Made both, **low first then high** |

B and D are required. Hour 1 almost always trades on both sides of the Open, and later hours can sweep both sides; without them, materially different days would share a code.

### 5.3 Position 7 (outcome)

Position 7 is the **outcome being predicted**, not part of the intraday matching key. It is stored in its own column (see §8).

| Symbol | Close location |
|---|---|
| H | At the session high |
| L | At the session low |
| M | Between high and low |

Optional second attribute, close relative to Open: **A** above, **B** below, **E** at Open. (Letters differ from the original draft to avoid the clash between "C = between" and "C = at Open".)

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
  }
}
```

- `t` is elapsed trading hours from 09:15 (fractional, e.g. 10:12 → 0.95).
- `sequence` is either O→H→L→C or O→L→H→C.
- Legs are derived from points; points are the source of truth.

## 7. Matching and prediction

### 7.1 Matching

At the end of each hour, build the current day's path-code prefix and select historical days whose path code starts with it (index prefix lookup). Only unique path codes are searched; there is no full-table similarity scan.

### 7.2 Minimum sample and backoff

Late prefixes will have few members. If the full prefix has fewer than **N** members (default 15–20, to be tuned), fall back to the longest shorter prefix that meets N, and report which prefix was used.

### 7.3 Time weighting

Evidence is weighted by the hour at which it occurs, not used as an averaging mechanism. Default: **w = 0.1 × position** (hour 1 → 0.1, hour 5 → 0.5). To be validated later by measuring how much each additional hour tightens the outcome spread.

### 7.4 Outcome distribution

From the matched days, compute for the remaining day:

- P(close above Open), P(close below Open)
- Distribution of close %, remaining high %, remaining low %
- Distribution of dt and dq for the next expected leg

Use **empirical percentiles** (e.g. P10/P25/P50/P75/P90) of the matched days rather than normal-curve sigma bands, since intraday moves are skewed and fat-tailed.

### 7.5 Output per hour

```
Time        11:15
Prefix      0O1H2X
Members     64   (backoff: none)
Weight      0.2
P(Up)       68%
Close %     P25 +0.12 | P50 +0.41 | P75 +0.78
Next leg    L expected: dt P50 1.6h, dq P50 -0.55%
```

## 8. Data model (PostgreSQL)

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
    path_code     TEXT        NOT NULL,  -- positions 0–6, e.g. 0O1H2X3X4L5L6H
    close_code    TEXT        NOT NULL,  -- position 7, e.g. HA
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
  AND path_code LIKE '0O1H2X%'
  AND trade_date < :as_of_date;
```

Notes:
- Use left-anchored `LIKE`. `ILIKE` or a leading `%` will not use the B-tree index.
- A summary table (path prefix → member count, outcome percentiles) can be materialised for instant reads.

## 9. Validation

- **Walk-forward only.** For each test day, build buckets solely from days before it.
- **Baseline.** Compare each prefix's outcome distribution against the unconditional distribution of all prior days.
- **Success criterion.** Prefix-conditioned outcomes differ meaningfully from the baseline at hours 2–4, and the difference grows as the day progresses.
- Report hit rate, calibration of P(Up), and percentile coverage per hour.

## 10. Scope

**In scope (v1):** NIFTY index, 1-minute source data, historical build, hourly prefix matching, outcome distributions, PostgreSQL storage, walk-forward evaluation.

**Out of scope (v1):** image rendering, DTW, HDBSCAN or K-Means, neural embeddings, ML models, other symbols, order execution.

## 11. Open questions

1. Tolerance for "at session high / low / Open" on the close (e.g. within 0.05%).
2. Whether position 7 (15-minute stub) should be merged into position 6.
3. Minimum sample size N and backoff rule details.
4. Whether a magnitude band character is needed if within-bucket dq spread turns out too wide.
5. Final time-weight function after validation.
