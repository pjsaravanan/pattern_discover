"""Authenticated JSON operations, without a custom frontend."""

import hmac
import os

from fastapi import APIRouter, Depends, Query, Security
from fastapi.security import APIKeyHeader

from .engine import encode_day
from .errors import PatternError
from .evaluation import walk_forward
from .matching import predict
from .models import (
    BuildRequest, BuildResponse, EncodeRequest, ErrorResponse, EvaluationRequest,
    EvaluationResponse, HistoryStatus, MatchRequest, PatternResponse, PredictionResponse,
)
from .storage import PatternStore, build_patterns, read_source

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
    This endpoint never connects to the database.
    """
    return encode_day(request.bars, request.config, request.through_position, request.complete_session)


@router.post("/history/build", response_model=BuildResponse, operation_id="buildHistory", tags=["history"])
def build_history(request: BuildRequest):
    """Read NIFTY/NSE/1m source bars and persist only project-owned derived records.

    Ranges are inclusive and bounded to 366 calendar days per request; build
    successive ranges for longer history. Rebuilding replaces only derived
    records for this configuration/date range, never source records.
    Incomplete/invalid sessions are reported, not repaired. E days are retained.
    Dates with no source rows are not assumed to be trading sessions.
    """
    groups = read_source(request.start_date, request.end_date)
    patterns, excluded = build_patterns(groups, request.config)
    with PatternStore() as store:
        created = store.save(request.config, patterns, request.start_date, request.end_date)
    return {
        "status": "built", "config_id": request.config.identity, "source": "public.price_data:NIFTY/NSE/1m",
        "start_date": request.start_date, "end_date": request.end_date,
        "source_days": len(groups), "stored_days": len(patterns),
        "unique_patterns": len({p["path_code"] for p in patterns}),
        "excluded_sessions": excluded, "created_project_storage": created,
    }


@router.get("/history/status", response_model=HistoryStatus, operation_id="historyStatus", tags=["history"])
def history_status(config_id: str = Query(min_length=64, max_length=64, pattern="^[0-9a-f]{64}$")):
    """Report stored coverage for a previously built configuration."""
    with PatternStore() as store:
        store.require_configuration(config_id)
        return {"config_id": config_id, **store.status(config_id)}


@router.post("/patterns/match", response_model=PredictionResponse, operation_id="matchPattern", tags=["patterns"])
def match_pattern(request: MatchRequest):
    """Match completed hourly tokens using strictly earlier historical dates.

    Supply bars, or omit them to load the requested developing day from the
    approved source. Backoff never returns a distribution with fewer than N
    members. Outcome windows always start at the requested hour, not the
    shorter selected prefix. Position 7 is never a matching key.
    """
    bars = request.bars
    if bars is None:
        bars = read_source(request.trade_date, request.trade_date, request.through_position).get(request.trade_date, [])
    if not bars:
        raise PatternError("source_day_not_found", "No source bars exist for the requested date", status=404)
    encoded = encode_day(bars, request.config, request.through_position)
    if encoded["trade_date"] != request.trade_date.isoformat():
        raise PatternError("date_mismatch", "Supplied bars must belong to trade_date")
    with PatternStore() as store:
        store.require_configuration(request.config.identity)
        result = predict(
            encoded["tokens"], request.through_position, request.support_target,
            lambda prefix: store.count_prefix(request.config.identity, prefix, request.trade_date),
            lambda prefix: store.members(request.config.identity, prefix, request.trade_date),
        )
    minutes = 555 + request.through_position * 60
    return {
        **result, "trade_date": request.trade_date, "config_id": request.config.identity,
        "through_position": request.through_position, "time": f"{minutes // 60:02}:{minutes % 60:02}",
    }


@router.post("/evaluation/walk-forward", response_model=EvaluationResponse,
             operation_id="evaluateWalkForward", tags=["evaluation"])
def evaluate(request: EvaluationRequest):
    """Evaluate built history chronologically against all-prior-days outcomes.

    Returns direction hit rate, three-class Brier score, P(Up) calibration,
    empirical P10–P90 coverage and support/backoff by hour. Conditional and
    baseline comparisons use the same supported test days. No evaluation
    writes, time weighting, fitted ML, or future-day training occurs.
    """
    with PatternStore() as store:
        store.require_configuration(request.config.identity)
        patterns = store.range(request.config.identity, request.history_start_date, request.end_date)
    result = walk_forward(patterns, request.test_start_date, request.end_date, request.support_target, request.positions)
    return {"config_id": request.config.identity, **result}
