"""Command line for every API operation: `python -m nifty_api <command> --help`.

Inputs pass through the same request models as the API and results are the same
JSON. The CLI uses the operator's own VPS_DATABASE_URL and needs no API key.
Settings are read from the environment, seeded by the nearest .env file.
"""

import argparse
import csv
import json
import logging
import os
from pathlib import Path
import sys

import psycopg
from pydantic import ValidationError

from . import service
from .errors import PatternError
from .models import (
    BuildRequest, BuildResponse, CheckResponse, EncodeRequest, EvaluationRequest, EvaluationResponse,
    HistoryStatus, MatchRequest, PatternConfig, PatternResponse, PredictionResponse,
    SyncRequest, SyncResponse,
)
from .settings import get_port, load_environment


def load_config(args):
    if args.config:
        return json.loads(Path(args.config).read_text())
    if args.band_edges is None or args.close_tolerance is None:
        raise PatternError("missing_configuration",
                           "Pass --config FILE, or both --band-edges and --close-tolerance; there are no defaults")
    return {"band_edges_pct": [x.strip() for x in args.band_edges.split(",")],
            "close_tolerance_points": args.close_tolerance}


def load_bars(path):
    """JSON array of bar objects, or CSV with a timestamp_ist,open,high,low,close header."""
    text = Path(path).read_text()
    if Path(path).suffix.lower() == ".csv":
        return list(csv.DictReader(text.splitlines()))
    return json.loads(text)


def positions(text):
    return [int(x) for x in text.split(",")]


def request_body(args):
    body = {"config": load_config(args)}
    if args.command == "encode":
        body |= {"bars": load_bars(args.bars), "through_position": args.through_position,
                 "complete_session": args.complete_session}
    elif args.command == "build":
        body |= {"start_date": args.start_date, "end_date": args.end_date}
    elif args.command == "sync":
        body |= {"rebuild": args.rebuild}
    elif args.command == "match":
        body |= {"support_target": args.support_target, "trade_date": args.trade_date,
                 "through_position": args.through_position}
        if args.bars:
            body["bars"] = load_bars(args.bars)
    elif args.command == "evaluate":
        body |= {"support_target": args.support_target, "history_start_date": args.history_start_date,
                 "test_start_date": args.test_start_date, "end_date": args.end_date}
        if args.positions:
            body["positions"] = args.positions
    return body


# command -> (request model, operation, response model, drop null fields like the API)
OPERATIONS = {
    "encode": (EncodeRequest, service.encode, PatternResponse, True),
    "build": (BuildRequest, service.build, BuildResponse, False),
    "sync": (SyncRequest, service.sync, SyncResponse, False),
    "match": (MatchRequest, service.match, PredictionResponse, False),
    "evaluate": (EvaluationRequest, service.evaluate, EvaluationResponse, False),
}


def run(args):
    if args.command == "check":
        return CheckResponse.model_validate(service.check()).model_dump(mode="json")
    if args.command == "status":
        config_id = args.config_id or PatternConfig.model_validate(load_config(args)).identity
        return HistoryStatus.model_validate(service.status(config_id)).model_dump(mode="json")
    request_model, operation, response_model, exclude_none = OPERATIONS[args.command]
    result = operation(request_model.model_validate(request_body(args)))
    return response_model.model_validate(result).model_dump(mode="json", exclude_none=exclude_none)


def add_config(parser):
    group = parser.add_argument_group("configuration (required, no defaults)")
    group.add_argument("--config", help="JSON file with band_edges_pct and close_tolerance_points")
    group.add_argument("--band-edges", help="Comma-separated ascending percentage points, e.g. 0.25,0.5,1,2")
    group.add_argument("--close-tolerance", help="Close-at-open tolerance in index points, e.g. 0")


def parser():
    root = argparse.ArgumentParser(prog="python -m nifty_api", description="NIFTY intraday trajectory pattern engine")
    root.add_argument("--output", help="Write the JSON result to this file instead of stdout")
    commands = root.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="Run the HTTP API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=None, help="Defaults to the PORT environment variable")

    commands.add_parser("check", help="Read-only check of source indexes and project storage")

    encode = commands.add_parser("encode", help="Encode supplied one-minute bars (no database)")
    add_config(encode)
    encode.add_argument("--bars", required=True, help="JSON or CSV file of one-minute bars")
    encode.add_argument("--through-position", type=int, default=6)
    encode.add_argument("--complete-session", action="store_true")

    build = commands.add_parser("build", help="Build an explicit inclusive date range (at most 366 days)")
    add_config(build)
    build.add_argument("--start-date", required=True)
    build.add_argument("--end-date", required=True)

    sync = commands.add_parser("sync", help="Process all unflagged sessions through the last complete session")
    add_config(sync)
    sync.add_argument("--rebuild", action="store_true",
                      help="Delete this configuration's derived records and flags, then reprocess everything")

    status = commands.add_parser("status", help="Stored coverage and processed-day flags")
    add_config(status)
    status.add_argument("--config-id", help="Configuration identity, instead of configuration arguments")

    match = commands.add_parser("match", help="Match a developing day against strictly earlier days")
    add_config(match)
    match.add_argument("--support-target", type=int, required=True)
    match.add_argument("--trade-date", required=True)
    match.add_argument("--through-position", type=int, required=True)
    match.add_argument("--bars", help="JSON or CSV bars; omitted to read the day from the source")

    evaluate = commands.add_parser("evaluate", help="Walk-forward evaluation against the all-prior-days baseline")
    add_config(evaluate)
    evaluate.add_argument("--support-target", type=int, required=True)
    evaluate.add_argument("--history-start-date", required=True)
    evaluate.add_argument("--test-start-date", required=True)
    evaluate.add_argument("--end-date", required=True)
    evaluate.add_argument("--positions", type=positions, help="Comma-separated hours, default 1-6")
    return root


def fail(code, message, details=None, exit_code=2):
    # Same envelope as the API, on stderr; never echoes raw inputs or credentials.
    print(json.dumps({"error": code, "message": message, "details": details or {}}), file=sys.stderr)
    return exit_code


def main(argv=None):
    args = parser().parse_args(argv)
    load_environment()
    if args.command == "serve":
        import uvicorn
        try:
            port = get_port(args.port)
        except ValueError as e:
            return fail("invalid_port", str(e))
        if not os.environ.get("NIFTY_API_KEY") and args.host not in ("127.0.0.1", "localhost", "::1"):
            logging.getLogger("nifty_api").warning(
                "NIFTY_API_KEY is not set: data endpoints are open to anyone who can reach %s", args.host)
        uvicorn.run("nifty_api.main:app", host=args.host, port=port, access_log=False)
        return 0
    try:
        result = run(args)
    except PatternError as e:
        return fail(e.code, e.message, e.details)
    except ValidationError as e:
        return fail("invalid_request", "Request validation failed", {"errors": [
            {"field": list(item["loc"]), "type": item["type"], "message": item["msg"]} for item in e.errors()]})
    except (OSError, json.JSONDecodeError) as e:
        return fail("invalid_input_file", "An input file could not be read as JSON or CSV",
                    {"file": getattr(e, "filename", None)})
    except psycopg.Error as e:
        logging.getLogger("nifty_api").error("Database operation failed; SQLSTATE=%s", e.sqlstate or "unavailable")
        return fail("database_unavailable", "Database operation failed; check connectivity and permissions",
                    exit_code=3)
    text = json.dumps(result, indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
