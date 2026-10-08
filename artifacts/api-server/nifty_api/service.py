"""Operations shared by the HTTP routes and the command line; inputs are validated request models."""

from .engine import encode_day
from .errors import PatternError
from .evaluation import walk_forward
from .matching import predict
from .storage import PatternStore, build_patterns, read_source
from .sync import sync_history


def encode(request):
    return encode_day(request.bars, request.config, request.through_position, request.complete_session)


def build(request):
    groups = read_source(request.start_date, request.end_date)
    patterns, excluded = build_patterns(groups, request.config)
    with PatternStore() as store:
        created = store.save(request.config, patterns, excluded, request.start_date, request.end_date)
    return {
        "status": "built", "config_id": request.config.identity, "source": "public.price_data:NIFTY/NSE/1m",
        "start_date": request.start_date, "end_date": request.end_date,
        "source_days": len(groups), "stored_days": len(patterns),
        "unique_patterns": len({p["path_code"] for p in patterns}),
        "excluded_sessions": excluded, "created_project_storage": created,
    }


def sync(request):
    return sync_history(request.config, request.rebuild)


def status(config_id):
    with PatternStore() as store:
        has_ledger = store.require_configuration(config_id)
        return {"config_id": config_id, **store.status(config_id, ledger=has_ledger)}


def match(request):
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


def evaluate(request):
    with PatternStore() as store:
        store.require_configuration(request.config.identity)
        patterns = store.range(request.config.identity, request.history_start_date, request.end_date)
    result = walk_forward(patterns, request.test_start_date, request.end_date, request.support_target, request.positions)
    return {"config_id": request.config.identity, **result}
