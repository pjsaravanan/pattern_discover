"""Explicit API inputs. No statistical parameter has a guessed default."""

from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class PatternConfig(StrictModel):
    band_edges_pct: list[Decimal] = Field(min_length=1, max_length=20)
    close_tolerance_points: Decimal = Field(ge=0)

    @model_validator(mode="after")
    def validate_edges(self):
        if any(x <= 0 for x in self.band_edges_pct):
            raise ValueError("Band edges must be positive percentage points")
        if self.band_edges_pct != sorted(set(self.band_edges_pct)):
            raise ValueError("Band edges must be strictly increasing")
        return self

    def canonical(self):
        return {
            "engine_version": 1,
            "band_edges_pct": [str(x.normalize()) for x in self.band_edges_pct],
            "close_tolerance_points": str(self.close_tolerance_points.normalize()),
        }

    @property
    def identity(self):
        text = json.dumps(self.canonical(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode()).hexdigest()


class Bar(StrictModel):
    timestamp_ist: datetime
    open: Decimal = Field(gt=0)
    high: Decimal = Field(gt=0)
    low: Decimal = Field(gt=0)
    close: Decimal = Field(gt=0)

    @model_validator(mode="after")
    def validate_bar(self):
        if self.timestamp_ist.utcoffset() is None:
            raise ValueError("Timestamp must include its UTC offset")
        if self.timestamp_ist.second or self.timestamp_ist.microsecond:
            raise ValueError("Source timestamps must be aligned to minute starts")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("OHLC bounds are inconsistent")
        return self


class EncodeRequest(StrictModel):
    config: PatternConfig
    bars: list[Bar] = Field(min_length=1, max_length=1000)
    through_position: int = Field(default=6, ge=0, le=6)
    complete_session: bool = False

    @model_validator(mode="after")
    def complete_position(self):
        if self.complete_session and self.through_position != 6:
            raise ValueError("A complete session must encode all six hourly positions")
        return self


class BuildRequest(StrictModel):
    config: PatternConfig
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def bounded_dates(self):
        if not 0 <= (self.end_date - self.start_date).days <= 365:
            raise ValueError("Date range must be ordered and at most 366 calendar days")
        return self


class SyncRequest(StrictModel):
    config: PatternConfig
    rebuild: bool = False


class MatchRequest(StrictModel):
    config: PatternConfig
    support_target: int = Field(ge=1, le=100000)
    trade_date: date
    through_position: int = Field(ge=0, le=6)
    bars: list[Bar] | None = Field(default=None, min_length=1, max_length=1000)


class EvaluationRequest(StrictModel):
    config: PatternConfig
    support_target: int = Field(ge=1, le=100000)
    history_start_date: date
    test_start_date: date
    end_date: date
    positions: list[int] = Field(default_factory=lambda: [1, 2, 3, 4, 5, 6], min_length=1, max_length=6)

    @model_validator(mode="after")
    def validate_evaluation(self):
        if not self.history_start_date < self.test_start_date <= self.end_date:
            raise ValueError("Require history_start_date < test_start_date <= end_date")
        if (self.end_date - self.history_start_date).days > 3650:
            raise ValueError("Evaluation range cannot exceed ten years")
        if len(set(self.positions)) != len(self.positions) or any(p not in range(1, 7) for p in self.positions):
            raise ValueError("Positions must be distinct integers from 1 through 6")
        return self


class Percentiles(BaseModel):
    p10: float
    p25: float
    p50: float
    p75: float
    p90: float


class NextEventDistribution(BaseModel):
    count: int
    probability: float
    dt_hours: Percentiles
    dq_pct: Percentiles | None
    dq_low_pct: Percentiles
    dq_high_pct: Percentiles


class NextLegEstimate(BaseModel):
    event: Literal["H", "L", "C", "E"]
    event_probabilities: dict[str, float]
    by_event: dict[str, NextEventDistribution]
    dt_p50_hours: float
    dq_p50_pct: float | None


class BandCount(BaseModel):
    count: int
    probability: float


class Estimate(BaseModel):
    p_close_above_open: float
    p_close_below_open: float
    p_close_at_open: float
    close_pct: Percentiles
    remaining_high_pct: Percentiles
    remaining_low_pct: Percentiles
    next_leg: NextLegEstimate
    magnitude_band_distribution: dict[str, dict[str, BandCount]]


class PredictionResponse(BaseModel):
    trade_date: date
    config_id: str
    through_position: int
    time: str
    bar_source: str
    requested_prefix: str
    requested_prefix_match_count: int
    selected_prefix: str | None
    match_count: int
    backoff_positions: int | None
    status: Literal["estimated", "insufficient_support"]
    support_target: int
    estimate: Estimate | None


class PatternResponse(BaseModel):
    trade_date: date
    symbol: str
    config_id: str
    through_position: int
    complete_session: bool
    open: str
    tokens: list[str]
    path_code: str
    buckets: list[dict[str, Any]]
    out_of_session_rows_ignored: int
    future_session_rows_ignored: int
    high: str | None = None
    low: str | None = None
    close: str | None = None
    high_time: str | None = None
    low_time: str | None = None
    close_code: str | None = None
    final_bucket: dict[str, Any] | None = None
    measurements: dict[str, Any] | None = None
    outcomes: dict[str, Any] | None = None


class ExcludedSession(BaseModel):
    trade_date: date
    reason: str
    details: dict[str, Any]


class BuildResponse(BaseModel):
    status: Literal["built"]
    config_id: str
    source: str
    start_date: date
    end_date: date
    source_days: int
    stored_days: int
    unique_patterns: int
    excluded_sessions: list[ExcludedSession]
    created_project_storage: bool


class SyncBatch(BaseModel):
    start_date: date
    end_date: date
    days: int


class SyncResponse(BaseModel):
    status: Literal["synced"]
    config_id: str
    source: str
    rebuild: bool
    through_date: date
    source_days: int
    already_processed_days: int
    pending_days: int
    stored_days: int
    excluded_sessions: list[ExcludedSession]
    batches: list[SyncBatch]
    created_project_storage: bool


class HistoryStatus(BaseModel):
    config_id: str
    stored_days: int
    first_date: date | None
    last_date: date | None
    unique_patterns: int
    processed_days: int | None
    excluded_days: int | None
    last_processed_date: date | None


class EvaluationResponse(BaseModel):
    status: Literal["evaluated", "no_test_days"]
    config_id: str
    method: str
    support_target: int
    test_start_date: date
    end_date: date
    per_hour: dict[str, dict[str, Any]]
    interpretation: str


class CheckResponse(BaseModel):
    status: Literal["ok", "attention"]
    source: dict[str, Any]
    project: dict[str, Any]
    scheduler: dict[str, Any]
    notes: str


class ErrorResponse(BaseModel):
    error: str
    message: str
    details: dict[str, Any]
