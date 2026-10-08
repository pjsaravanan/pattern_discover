"""Authenticated JSON operations, without a custom frontend. Logic lives in `service`."""

import hmac
import os

from fastapi import APIRouter, Depends, Query, Security
from fastapi.security import APIKeyHeader

from . import service
from .errors import PatternError
from .models import (
    BuildRequest, BuildResponse, EncodeRequest, ErrorResponse, EvaluationRequest,
    EvaluationResponse, HistoryStatus, MatchRequest, PatternResponse, PredictionResponse,
    SyncRequest, SyncResponse,
)

key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def authorize(key: str | None = Security(key_header)):
    expected = os.environ.get("NIFTY_API_KEY") or os.environ.get("SESSION_SECRET")
    if not expected:
        raise PatternError("authentication_not_configured", "Configure NIFTY_API_KEY or SESSION_SECRET", status=503)
    if not key or not hmac.compare_digest(key.encode(), expected.encode()):
        raise PatternError("unauthorized", "A valid X-API-Key is required", status=401)


router = APIRouter(
    prefix="/api", dependencies=[Depends(authorize)],
    responses={code: {"model": ErrorResponse} for code in (401, 404, 409, 422, 503)},
)


@router.post("/patterns/encode", response_model=PatternResponse, response_model_exclude_none=True,
             operation_id="encodePattern", tags=["patterns"])
def encode_pattern(request: EncodeRequest):
    """Encode supplied one-minute OHLC bars. Future bars are ignored for prefixes.

    All statistical configuration is explicit. Set complete_session=true to
    require all 375 minutes and return the separate final bucket and outcomes.
    This endpoint never connects to the database. CLI: `encode`.
    """
    return service.encode(request)


@router.post("/history/build", response_model=BuildResponse, operation_id="buildHistory", tags=["history"])
def build_history(request: BuildRequest):
    """Read NIFTY/NSE/1m source bars and persist only project-owned derived records.

    Ranges are inclusive and bounded to 366 calendar days per request; use
    history/sync to process all history without manual ranges. Rebuilding
    replaces derived records and processed-day flags for this configuration/
    date range, never source records. Incomplete/invalid sessions are reported
    and flagged, not repaired. E days are retained. Dates with no source rows
    are not assumed to be trading sessions. CLI: `build`.
    """
    return service.build(request)


@router.post("/history/sync", response_model=SyncResponse, operation_id="syncHistory", tags=["history"])
def sync_history(request: SyncRequest):
    """Process every source session not yet flagged for this configuration.

    The first sync processes all available history; later syncs process only
    new or gap dates, plus excluded dates among the three most recent sessions.
    Only sessions through the last complete session (15:30 IST) are processed.
    rebuild=true deletes this configuration's derived records and flags, then
    reprocesses everything. Each batch commits separately, so an interrupted
    sync resumes on the next call. Long-running on a first load. CLI: `sync`.
    """
    return service.sync(request)


@router.get("/history/status", response_model=HistoryStatus, operation_id="historyStatus", tags=["history"])
def history_status(config_id: str = Query(min_length=64, max_length=64, pattern="^[0-9a-f]{64}$")):
    """Report stored coverage and processed-day flags for a built configuration. CLI: `status`."""
    return service.status(config_id)


@router.post("/patterns/match", response_model=PredictionResponse, operation_id="matchPattern", tags=["patterns"])
def match_pattern(request: MatchRequest):
    """Match completed hourly tokens using strictly earlier historical dates.

    Supply bars, or omit them to load the requested developing day from the
    approved source. Backoff never returns a distribution with fewer than N
    members. Outcome windows always start at the requested hour, not the
    shorter selected prefix. Position 7 is never a matching key. CLI: `match`.
    """
    return service.match(request)


@router.post("/evaluation/walk-forward", response_model=EvaluationResponse,
             operation_id="evaluateWalkForward", tags=["evaluation"])
def evaluate(request: EvaluationRequest):
    """Evaluate built history chronologically against all-prior-days outcomes.

    Returns direction hit rate, three-class Brier score, P(Up) calibration,
    empirical P10–P90 coverage and support/backoff by hour. Conditional and
    baseline comparisons use the same supported test days. No evaluation
    writes, time weighting, fitted ML, or future-day training occurs. CLI: `evaluate`.
    """
    return service.evaluate(request)
