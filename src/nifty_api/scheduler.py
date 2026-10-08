"""Optional in-process daily sync, so a single API service keeps history current.

Enabled only when both SYNC_AT ("HH:MM", IST) and SYNC_CONFIG (PatternConfig JSON) are set.
Sync is idempotent, so a catch-up run on startup or a repeated run only costs a source scan.
"""

from datetime import datetime, time, timedelta
import json
import logging
import os
import threading

import psycopg

from .engine import IST
from .errors import PatternError
from .models import PatternConfig
from .sync import sync_history

RETRY_AFTER = timedelta(hours=1)
log = logging.getLogger("nifty_api.scheduler")
state = {"enabled": False, "sync_at_ist": None, "config_id": None, "running": False,
         "last_started": None, "last_finished": None, "last_result": None, "next_run": None}


def settings():
    """(time, PatternConfig) from the environment, None when disabled; raises on partial/invalid settings."""
    at, config = os.environ.get("SYNC_AT"), os.environ.get("SYNC_CONFIG")
    if not at and not config:
        return None
    if not at or not config:
        raise ValueError("Set both SYNC_AT and SYNC_CONFIG to enable the daily sync, or neither")
    try:
        hour, minute = (int(x) for x in at.split(":"))
        when = time(hour, minute)
    except ValueError:
        raise ValueError("SYNC_AT must be HH:MM (IST), e.g. 22:30") from None
    return when, PatternConfig.model_validate(json.loads(config))


def next_run(now, when, ran_today):
    """Today's slot if it has passed and has not run yet (catch-up), else the next future slot."""
    today = datetime.combine(now.date(), when, IST)
    if now >= today and not ran_today:
        return now
    return today if now < today else today + timedelta(days=1)


def run_once(config):
    state["last_started"] = datetime.now(IST).isoformat()
    try:
        result = sync_history(config)
        state["last_result"] = {"status": "synced", "pending_days": result["pending_days"],
                                "stored_days": result["stored_days"],
                                "excluded_days": len(result["excluded_sessions"]),
                                "through_date": result["through_date"].isoformat()}
        return True
    except PatternError as e:
        state["last_result"] = {"status": "failed", "error": e.code}
    except psycopg.Error as e:
        # Never log exception text: connection failures may contain credential data.
        state["last_result"] = {"status": "failed", "error": "database_unavailable", "sqlstate": e.sqlstate}
    except Exception as e:
        state["last_result"] = {"status": "failed", "error": type(e).__name__}
    finally:
        state["last_finished"] = datetime.now(IST).isoformat()
    log.error("Scheduled sync failed: %s", state["last_result"])
    return False


def loop(when, config, stop):
    ran_on = None  # IST date whose slot has completed successfully
    while not stop.is_set():
        now = datetime.now(IST)
        due = next_run(now, when, ran_on == now.date())
        state["next_run"] = due.isoformat()
        if stop.wait(max(0.0, (due - now).total_seconds())):
            return
        if run_once(config):
            ran_on = datetime.now(IST).date()
        else:
            state["next_run"] = (datetime.now(IST) + RETRY_AFTER).isoformat()
            if stop.wait(RETRY_AFTER.total_seconds()):
                return


def start():
    """Start the daily sync thread if configured; returns a stop event (or None when disabled)."""
    configured = settings()
    if configured is None:
        return None
    when, config = configured
    state.update(enabled=True, sync_at_ist=when.strftime("%H:%M"), config_id=config.identity, running=True)
    stop = threading.Event()
    threading.Thread(target=loop, args=(when, config, stop), name="nifty-daily-sync", daemon=True).start()
    log.info("Daily sync enabled at %s IST for configuration %s", state["sync_at_ist"], config.identity)
    return stop


def describe():
    """Scheduler state for `check`; outside the server process only the configuration is known."""
    try:
        configured = settings()
    except Exception as e:
        return {**state, "configuration_error": str(e)}
    if configured and not state["running"]:
        when, config = configured
        return {**state, "enabled": True, "sync_at_ist": when.strftime("%H:%M"), "config_id": config.identity,
                "note": "Runs inside the API server process; this process is not the server."}
    return dict(state)
