"""Deterministic intraday encoding; no database, images, or cross-day scaling."""

from collections import Counter
from datetime import datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from .errors import PatternError
from .models import Bar, PatternConfig

IST = ZoneInfo("Asia/Kolkata")
HUNDRED = Decimal(100)


def band(value: Decimal, config: PatternConfig) -> str:
    for i, edge in enumerate(config.band_edges_pct, 1):
        if abs(value) <= edge:
            return f"Q{i}"
    return f"Q{len(config.band_edges_pct) + 1}"


def pct(value: Decimal, opening: Decimal) -> Decimal:
    return (value - opening) * HUNDRED / opening


def minute(bar: Bar) -> int:
    t = bar.timestamp_ist.astimezone(IST)
    return t.hour * 60 + t.minute - 555


def extrema(bars: list[Bar]):
    # Input is sorted; min/max select the earliest occurrence for equal prices.
    return max(bars, key=lambda x: x.high), min(bars, key=lambda x: x.low)


def bucket_record(bars: list[Bar], position: int):
    h, l = extrema(bars)
    return {
        "position": position,
        "start": bars[0].timestamp_ist.astimezone(IST).isoformat(),
        "end": datetime.combine(
            bars[0].timestamp_ist.astimezone(IST).date(),
            time(15, 30) if position == 7 else time((555 + position * 60) // 60, (555 + position * 60) % 60),
            IST,
        ).isoformat(),
        "open": str(bars[0].open), "high": str(h.high), "low": str(l.low), "close": str(bars[-1].close),
        "high_time": h.timestamp_ist.astimezone(IST).isoformat(),
        "low_time": l.timestamp_ist.astimezone(IST).isoformat(),
        "minute_count": len(bars),
    }


def encode_day(records: list[Bar | dict], config: PatternConfig, position: int = 6, complete: bool = False):
    if not 0 <= position <= 6 or (complete and position != 6):
        raise PatternError("invalid_position", "Positions are 0–6; complete sessions require position 6")
    bars = [x if isinstance(x, Bar) else Bar.model_validate(x) for x in records]
    dates = {b.timestamp_ist.astimezone(IST).date() for b in bars}
    if len(dates) != 1:
        raise PatternError("mixed_dates", "Supply exactly one IST trading date")
    day = dates.pop()
    session = sorted((b for b in bars if 0 <= minute(b) < 375), key=lambda b: b.timestamp_ist)
    required = 375 if complete else max(1, position * 60)
    used = [b for b in session if minute(b) < required]
    counts = Counter(minute(b) for b in used)
    duplicates = sorted(m for m, n in counts.items() if n > 1)
    missing = sorted(set(range(required)) - counts.keys())
    if duplicates or missing:
        raise PatternError(
            "incomplete_session" if missing else "duplicate_minutes",
            "Required source minutes are missing or duplicated; no bars are fabricated",
            {"required_minutes": required, "received_minutes": len(used),
             "missing_count": len(missing), "missing_minute_offsets": missing[:20],
             "duplicate_minute_offsets": duplicates[:20]},
        )
    opening = used[0].open
    running_high = running_low = opening
    tokens, buckets = ["0O"], []
    for p in range(1, position + 1):
        chunk = used[(p - 1) * 60:p * 60]
        h, l = extrema(chunk)
        H, L = h.high > running_high, l.low < running_low
        hi_band, lo_band = band(pct(h.high, opening), config), band(pct(l.low, opening), config)
        if H and L:
            symbol = "E" if minute(h) == minute(l) else "/" if minute(l) < minute(h) else "\\"
            bands = [hi_band, lo_band] if symbol == "\\" else [lo_band, hi_band]
        else:
            symbol = "H" if H else "L" if L else "X"
            bands = [hi_band] if H else [lo_band] if L else []
        token = f"{p}{symbol}" + ("[" + ",".join(bands) + "]" if bands else "")
        tokens.append(token)
        buckets.append({**bucket_record(chunk, p), "symbol": symbol, "token": token,
                        "prior_day_high": str(running_high), "prior_day_low": str(running_low),
                        "magnitude_bands": bands})
        running_high, running_low = max(running_high, h.high), min(running_low, l.low)
    result = {
        "trade_date": day.isoformat(), "symbol": "NIFTY", "config_id": config.identity,
        "through_position": position, "complete_session": complete, "open": str(opening),
        "tokens": tokens, "path_code": "|".join(tokens), "buckets": buckets,
        "out_of_session_rows_ignored": len(bars) - len(session),
        "future_session_rows_ignored": len(session) - len(used),
    }
    if not complete:
        result["close_code"] = None
        return result
    h, l = extrema(used)
    closing = used[-1].close
    close_code = "7C" if abs(closing - opening) <= config.close_tolerance_points else "7/" if closing > opening else "7\\"
    ht, lt = Decimal(minute(h)) / 60, Decimal(minute(l)) / 60
    hp, lp, cp = pct(h.high, opening), pct(l.low, opening), pct(closing, opening)
    points = {"O": {"t": 0.0, "pct": 0.0}, "H": {"t": float(ht), "pct": float(hp)},
              "L": {"t": float(lt), "pct": float(lp)}, "C": {"t": 6.25, "pct": float(cp)}}
    legs = {"OH": (ht, hp), "OL": (lt, lp), "HL": (abs(ht - lt), abs(hp - lp)),
            "HC": (Decimal("6.25") - ht, abs(cp - hp)), "LC": (Decimal("6.25") - lt, abs(cp - lp))}
    measurements = {
        "sequence": ["O", "E", "C"] if ht == lt else ["O", "H", "L", "C"] if ht < lt else ["O", "L", "H", "C"],
        "order_unknown": ht == lt, "points": points,
        "legs": {k: {"dt": float(dt), "dq": float(dq)} for k, (dt, dq) in legs.items()},
        "magnitude_bands": {k: band(dq, config) for k, (_, dq) in legs.items()},
    }
    outcomes = {}
    for p in range(7):
        cutoff = p * 60
        future = used[cutoff:]
        fh, fl = extrema(future)
        current = opening if p == 0 else used[cutoff - 1].close
        events = []
        if minute(h) >= cutoff:
            events.append((minute(h), "H", h.high))
        if minute(l) >= cutoff:
            events.append((minute(l), "L", l.low))
        events.append((375, "C", closing))
        events.sort(key=lambda e: e[0])
        next_t = events[0][0]
        tied = [e for e in events if e[0] == next_t]
        target_values = [pct(e[2], opening) - pct(current, opening) for e in tied]
        next_leg = {
            "event": "E" if len(tied) > 1 else tied[0][1],
            "dt_hours": (next_t - cutoff) / 60,
            "dq_pct": None if len(tied) > 1 else float(target_values[0]),
            "dq_low_pct": float(min(target_values)), "dq_high_pct": float(max(target_values)),
        }
        outcomes[str(p)] = {
            "close_code": close_code, "close_pct": float(cp),
            "remaining_high_pct": float(pct(fh.high, opening)),
            "remaining_low_pct": float(pct(fl.low, opening)), "next_leg": next_leg,
        }
    result.update({
        "high": str(h.high), "low": str(l.low), "close": str(closing),
        "high_time": h.timestamp_ist.astimezone(IST).isoformat(), "low_time": l.timestamp_ist.astimezone(IST).isoformat(),
        "close_code": close_code, "final_bucket": bucket_record(used[360:], 7),
        "measurements": measurements, "outcomes": outcomes,
    })
    return result


def encode_dataframe(frame, config: PatternConfig, position: int = 6, complete: bool = False):
    """Accept a pandas-compatible frame without imposing pandas on the API runtime."""
    if "timestamp_ist" not in frame.columns:
        index_name = frame.index.name or "index"
        frame = frame.reset_index().rename(columns={index_name: "timestamp_ist"})
    fields = ("timestamp_ist", "open", "high", "low", "close")
    if any(field not in frame.columns for field in fields):
        raise PatternError("invalid_dataframe_columns", "DataFrame requires timestamp_ist (or datetime index) and OHLC columns")
    records = [{field: row[field] for field in fields} for row in frame.to_dict(orient="records")]
    return encode_day(records, config, position, complete)
