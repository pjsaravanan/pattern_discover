"""Complete-token prefix backoff and empirical, unweighted outcomes."""

from collections import Counter, defaultdict
from statistics import mean


def percentiles(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    result = {}
    for p in (10, 25, 50, 75, 90):
        index = (len(values) - 1) * p / 100
        lo, hi = int(index), min(int(index) + 1, len(values) - 1)
        result[f"p{p}"] = values[lo] + (values[hi] - values[lo]) * (index - lo)
    return result


def distribution(members, position):
    outcomes = [x["outcomes"][str(position)] for x in members]
    counts = Counter(x["close_code"] for x in outcomes)
    n = len(outcomes)
    next_events = defaultdict(list)
    for outcome in outcomes:
        next_events[outcome["next_leg"]["event"]].append(outcome["next_leg"])
    by_event = {
        event: {
            "count": len(rows), "probability": len(rows) / n,
            **{key: percentiles(x[key] for x in rows)
               for key in ("dt_hours", "dq_pct", "dq_low_pct", "dq_high_pct")},
        }
        for event, rows in sorted(next_events.items())
    }
    event = max(by_event, key=lambda k: (by_event[k]["count"], k))
    primary = by_event[event]
    bands = {}
    for leg in ("OH", "OL", "HL", "HC", "LC"):
        c = Counter(x["measurements"]["magnitude_bands"][leg] for x in members)
        bands[leg] = {k: {"count": count, "probability": count / n} for k, count in sorted(c.items())}
    return {
        "p_close_above_open": counts["7/"] / n, "p_close_below_open": counts["7\\"] / n,
        "p_close_at_open": counts["7C"] / n,
        **{key: percentiles(x[key] for x in outcomes)
           for key in ("close_pct", "remaining_high_pct", "remaining_low_pct")},
        "next_leg": {
            "event": event, "event_probabilities": {k: v["probability"] for k, v in by_event.items()},
            "by_event": by_event, "dt_p50_hours": primary["dt_hours"]["p50"],
            "dq_p50_pct": primary["dq_pct"]["p50"] if primary["dq_pct"] else None,
        },
        "magnitude_band_distribution": bands,
    }


def predict(tokens, position, support_target, count_prefix, members_prefix):
    requested = "|".join(tokens[:position + 1])
    requested_count = count_prefix(requested)
    for selected_position in range(position, -1, -1):
        prefix = "|".join(tokens[:selected_position + 1])
        count = requested_count if selected_position == position else count_prefix(prefix)
        if count >= support_target:
            members = members_prefix(prefix)
            if len(members) < support_target:
                # Never leak a below-N distribution, even with a inconsistent adapter.
                continue
            return {
                "requested_prefix": requested, "requested_prefix_match_count": requested_count,
                "selected_prefix": prefix, "match_count": len(members),
                "backoff_positions": position - selected_position, "status": "estimated",
                "support_target": support_target, "estimate": distribution(members, position),
            }
    return {
        "requested_prefix": requested, "requested_prefix_match_count": requested_count,
        "selected_prefix": None, "match_count": 0, "backoff_positions": None,
        "status": "insufficient_support", "support_target": support_target, "estimate": None,
    }


class PrefixHistory:
    """Indexes complete tokens incrementally; only prior days are inserted."""

    def __init__(self):
        self.index = defaultdict(list)

    def add(self, pattern):
        for p in range(7):
            self.index["|".join(pattern["tokens"][:p + 1])].append(pattern)

    def count(self, prefix):
        return len(self.index.get(prefix, ()))

    def members(self, prefix):
        return self.index.get(prefix, [])
