"""Incremental history sync: each source session is processed once, flagged, and resumable."""

from datetime import datetime, time, timedelta

from .engine import IST
from .storage import PatternStore, build_patterns, read_source, source_dates

SESSION_CLOSE = time(15, 30)
RECHECK_SESSIONS = 3
MAX_SPAN_DAYS = 365  # Inclusive 366 calendar days, as for explicit range builds.


def last_complete_session(now=None):
    """Today once the session has closed, otherwise the previous calendar date."""
    now = (now or datetime.now(IST)).astimezone(IST)
    return now.date() if now.time() >= SESSION_CLOSE else now.date() - timedelta(days=1)


def pending_dates(sessions, ledger, recheck=RECHECK_SESSIONS):
    """Unflagged sessions, plus excluded ones among the most recent (late source data)."""
    recent = set(sessions[-recheck:]) if recheck else set()
    return [d for d in sessions if d not in ledger or (ledger[d] == "excluded" and d in recent)]


def chunks(sessions, pending):
    """Runs of consecutive source sessions, each spanning at most 366 calendar days."""
    index = {d: i for i, d in enumerate(sessions)}
    runs = []
    for d in pending:
        if runs and index[d] == index[runs[-1][-1]] + 1 and (d - runs[-1][0]).days <= MAX_SPAN_DAYS:
            runs[-1].append(d)
        else:
            runs.append([d])
    return runs


def sync_history(config, rebuild=False, now=None):
    through = last_complete_session(now)
    sessions = source_dates(through)
    with PatternStore() as store:
        created = store.reset(config) if rebuild else store.register(config)
        ledger = {} if rebuild else store.ledger(config.identity)
    pending = pending_dates(sessions, ledger)
    stored, excluded, batches = 0, [], []
    for run in chunks(sessions, pending):
        wanted = set(run)
        groups = {d: rows for d, rows in read_source(run[0], run[-1]).items() if d in wanted}
        patterns, exclusions = build_patterns(groups, config)
        # A date seen by the source scan but absent on re-read is flagged, never assumed complete.
        exclusions += [{"trade_date": d.isoformat(), "reason": "no_session_rows", "details": {}}
                       for d in run if d not in groups]
        # One transaction per run: an interrupted sync resumes from the next unflagged date.
        with PatternStore() as store:
            store.save_dates(config, patterns, exclusions, run)
        stored += len(patterns)
        excluded += exclusions
        batches.append({"start_date": run[0], "end_date": run[-1], "days": len(run)})
    return {
        "status": "synced", "config_id": config.identity, "source": "public.price_data:NIFTY/NSE/1m",
        "rebuild": rebuild, "through_date": through, "source_days": len(sessions),
        "already_processed_days": len(sessions) - len(pending), "pending_days": len(pending),
        "stored_days": stored, "excluded_sessions": excluded, "batches": batches,
        "created_project_storage": created,
    }
