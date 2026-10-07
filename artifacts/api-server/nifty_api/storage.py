"""Read-only source access and separately guarded project-owned persistence."""

from collections import defaultdict
from datetime import date, datetime, time, timedelta
import os

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from .engine import IST, encode_day
from .errors import PatternError
from .models import Bar

SCHEMA = "nifty_trajectory_v1"
MARKER = "nifty-trajectory-engine:owned:v1"
TABLES = ("configurations", "clusters", "day_patterns")


def connection():
    url = os.environ.get("VPS_DATABASE_URL")
    if not url:
        raise PatternError("database_not_configured", "VPS_DATABASE_URL is not configured", status=503)
    return psycopg.connect(url, connect_timeout=12, row_factory=dict_row, application_name="nifty_pattern_api")


def read_source(start: date, end: date, position: int | None = None):
    lower = datetime.combine(start, time(9, 15), IST)
    upper = datetime.combine(end + timedelta(days=1), time(0), IST)
    if position is not None:
        upper = lower + timedelta(minutes=max(1, position * 60))
    with connection() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '30s'")
        rows = conn.execute(
            """SELECT timestamp_ist,open,high,low,close
               FROM public.price_data
               WHERE symbol=%s AND exchange=%s AND timeframe=%s
                 AND timestamp_ist >= %s AND timestamp_ist < %s
                 AND (timestamp_ist AT TIME ZONE 'Asia/Kolkata')::time >= TIME '09:15'
                 AND (timestamp_ist AT TIME ZONE 'Asia/Kolkata')::time < TIME '15:30'
               ORDER BY timestamp_ist""",
            ("NIFTY", "NSE", "1m", lower, upper),
        ).fetchall()
    groups = defaultdict(list)
    for row in rows:
        groups[row["timestamp_ist"].astimezone(IST).date()].append(row)
    return groups


def build_patterns(groups, config):
    patterns, exclusions = [], []
    for day, rows in sorted(groups.items()):
        try:
            patterns.append(encode_day(rows, config, complete=True))
        except PatternError as e:
            exclusions.append({"trade_date": day.isoformat(), "reason": e.code, "details": e.details})
        except ValidationError:
            exclusions.append({"trade_date": day.isoformat(), "reason": "invalid_source_ohlc",
                               "details": {"message": "Null, nonpositive, unaligned, or inconsistent source bars"}})
    return patterns, exclusions


class PatternStore:
    def __enter__(self):
        self.conn = connection()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.conn.__exit__(exc_type, exc, tb)

    def _schema(self):
        return self.conn.execute(
            """SELECT obj_description(oid,'pg_namespace') AS marker,
                      nspowner=(SELECT oid FROM pg_roles WHERE rolname=current_user) AS owned
               FROM pg_namespace WHERE nspname=%s""", (SCHEMA,),
        ).fetchone()

    def verify_owned(self):
        schema = self._schema()
        if schema is None:
            raise PatternError("history_not_built", "Build historical patterns before matching or evaluation", status=409)
        if schema["marker"] != MARKER or not schema["owned"]:
            raise PatternError("storage_collision", "Refusing access to an existing unowned project schema", status=409)
        rows = self.conn.execute(
            """SELECT c.relname,obj_description(c.oid,'pg_class') AS marker
               FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
               WHERE n.nspname=%s AND c.relname = ANY(%s) AND c.relkind='r'""",
            (SCHEMA, list(TABLES)),
        ).fetchall()
        if {r["relname"] for r in rows if r["marker"] == MARKER} != set(TABLES):
            raise PatternError("storage_collision", "Refusing writes to absent or unmarked existing tables", status=409)

    def initialize(self):
        # Serializes initial schema creation and all project builds, not source tables.
        self.conn.execute("SELECT pg_advisory_xact_lock(761204831)")
        if self._schema() is not None:
            self.verify_owned()
            return False
        self.conn.execute("CREATE SCHEMA nifty_trajectory_v1")
        self.conn.execute("COMMENT ON SCHEMA nifty_trajectory_v1 IS 'nifty-trajectory-engine:owned:v1'")
        self.conn.execute("""
            CREATE TABLE nifty_trajectory_v1.configurations (
                id TEXT PRIMARY KEY, settings JSONB NOT NULL
            )""")
        self.conn.execute("""
            CREATE TABLE nifty_trajectory_v1.clusters (
                config_id TEXT NOT NULL REFERENCES nifty_trajectory_v1.configurations(id),
                path_code TEXT COLLATE "C" NOT NULL,
                PRIMARY KEY (config_id,path_code)
            )""")
        self.conn.execute("""
            CREATE TABLE nifty_trajectory_v1.day_patterns (
                config_id TEXT NOT NULL,
                trade_date DATE NOT NULL,
                path_code TEXT COLLATE "C" NOT NULL,
                pattern JSONB NOT NULL,
                PRIMARY KEY (config_id,trade_date),
                FOREIGN KEY (config_id,path_code) REFERENCES nifty_trajectory_v1.clusters(config_id,path_code)
            )""")
        self.conn.execute("CREATE INDEX nifty_clusters_prefix_idx ON nifty_trajectory_v1.clusters (config_id,path_code text_pattern_ops)")
        self.conn.execute("CREATE INDEX nifty_days_cluster_date_idx ON nifty_trajectory_v1.day_patterns (config_id,path_code,trade_date)")
        for table in TABLES:
            # Identifiers come exclusively from the fixed allowlist, never requests.
            self.conn.execute(f"COMMENT ON TABLE {SCHEMA}.{table} IS 'nifty-trajectory-engine:owned:v1'")
        return True

    def save(self, config, patterns, start, end):
        created = self.initialize()
        self.conn.execute(
            "INSERT INTO nifty_trajectory_v1.configurations(id,settings) VALUES (%s,%s) ON CONFLICT (id) DO NOTHING",
            (config.identity, Jsonb(config.canonical())),
        )
        # Rebuilds may remove stale derived records for excluded days, only in owned tables.
        self.conn.execute(
            "DELETE FROM nifty_trajectory_v1.day_patterns WHERE config_id=%s AND trade_date BETWEEN %s AND %s",
            (config.identity, start, end),
        )
        with self.conn.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO nifty_trajectory_v1.clusters(config_id,path_code) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                [(config.identity, p["path_code"]) for p in patterns],
            )
            cursor.executemany(
                """INSERT INTO nifty_trajectory_v1.day_patterns(config_id,trade_date,path_code,pattern)
                   VALUES (%s,%s,%s,%s)""",
                [(config.identity, p["trade_date"], p["path_code"], Jsonb(p)) for p in patterns],
            )
        return created

    def require_configuration(self, config_id):
        self.conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        self.conn.execute("SET LOCAL statement_timeout = '30s'")
        self.verify_owned()
        if not self.conn.execute("SELECT 1 FROM nifty_trajectory_v1.configurations WHERE id=%s", (config_id,)).fetchone():
            raise PatternError("configuration_not_built", "Build history with this exact configuration first", status=409)

    def count_prefix(self, config_id, prefix, as_of):
        return self.conn.execute("""
            SELECT COUNT(*) AS n FROM nifty_trajectory_v1.day_patterns d
            JOIN nifty_trajectory_v1.clusters c ON c.config_id=d.config_id AND c.path_code=d.path_code
            WHERE c.config_id=%s AND (c.path_code=%s OR c.path_code LIKE %s ESCAPE '')
              AND d.trade_date < %s""", (config_id, prefix, prefix + "|%", as_of),
        ).fetchone()["n"]

    def members(self, config_id, prefix, as_of):
        return [r["pattern"] for r in self.conn.execute("""
            SELECT d.pattern FROM nifty_trajectory_v1.clusters c
            JOIN nifty_trajectory_v1.day_patterns d ON d.config_id=c.config_id AND d.path_code=c.path_code
            WHERE c.config_id=%s AND (c.path_code=%s OR c.path_code LIKE %s ESCAPE '')
              AND d.trade_date < %s ORDER BY d.trade_date""",
            (config_id, prefix, prefix + "|%", as_of),
        ).fetchall()]

    def range(self, config_id, start, end):
        return [r["pattern"] for r in self.conn.execute(
            """SELECT pattern FROM nifty_trajectory_v1.day_patterns
               WHERE config_id=%s AND trade_date BETWEEN %s AND %s ORDER BY trade_date""",
            (config_id, start, end),
        ).fetchall()]

    def status(self, config_id):
        return self.conn.execute("""
            SELECT COUNT(*) AS stored_days,MIN(trade_date)::text AS first_date,
                   MAX(trade_date)::text AS last_date,COUNT(DISTINCT path_code) AS unique_patterns
            FROM nifty_trajectory_v1.day_patterns WHERE config_id=%s""", (config_id,),
        ).fetchone()
