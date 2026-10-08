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
CORE_TABLES = ("configurations", "clusters", "day_patterns")
LEDGER = "processed_days"
TABLES = (*CORE_TABLES, LEDGER)
SOURCE = ("NIFTY", "NSE", "1m")
# Project-owned indexes beyond primary keys. Ledger reads and deletes use its (config_id, trade_date) key.
OWNED_INDEXES = {
    "nifty_clusters_prefix_idx":
        "CREATE INDEX IF NOT EXISTS nifty_clusters_prefix_idx ON nifty_trajectory_v1.clusters (config_id,path_code text_pattern_ops)",
    "nifty_days_cluster_date_idx":
        "CREATE INDEX IF NOT EXISTS nifty_days_cluster_date_idx ON nifty_trajectory_v1.day_patterns (config_id,path_code,trade_date)",
}
# Recommended for the read-only source table; reported by `check`, never created by this project.
SOURCE_INDEX_ADVICE = ("CREATE INDEX ON public.price_data (symbol, exchange, timeframe, timestamp_ist) "
                       "-- to be created by the database owner, not this project")
SESSION_FILTER = """symbol=%s AND exchange=%s AND timeframe=%s
                 AND (timestamp_ist AT TIME ZONE 'Asia/Kolkata')::time >= TIME '09:15'
                 AND (timestamp_ist AT TIME ZONE 'Asia/Kolkata')::time < TIME '15:30'"""


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
            f"""SELECT timestamp_ist,open,high,low,close
               FROM public.price_data
               WHERE {SESSION_FILTER}
                 AND timestamp_ist >= %s AND timestamp_ist < %s
               ORDER BY timestamp_ist""",
            (*SOURCE, lower, upper),
        ).fetchall()
    groups = defaultdict(list)
    for row in rows:
        groups[row["timestamp_ist"].astimezone(IST).date()].append(row)
    return groups


def source_dates(through: date):
    """Distinct IST session dates with in-session source rows, up to and including `through`."""
    upper = datetime.combine(through + timedelta(days=1), time(0), IST)
    with connection() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '120s'")
        rows = conn.execute(
            f"""SELECT DISTINCT (timestamp_ist AT TIME ZONE 'Asia/Kolkata')::date AS trade_date
               FROM public.price_data
               WHERE {SESSION_FILTER} AND timestamp_ist < %s
               ORDER BY 1""",
            (*SOURCE, upper),
        ).fetchall()
    return [r["trade_date"] for r in rows]


def _scans(plan):
    """Every table/index access node, including partitions and bitmap scans."""
    found = []
    if "Relation Name" in plan or "Index Name" in plan:
        found.append({"node_type": plan["Node Type"], "relation": plan.get("Relation Name"),
                      "index": plan.get("Index Name")})
    for child in plan.get("Plans", []):
        found += _scans(child)
    return found


def check_database():
    """Read-only diagnosis of source access paths and project-owned storage. Never creates anything."""
    lower = datetime.combine(date.today(), time(9, 15), IST)
    with connection() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '30s'")
        source_indexes = [r["indexdef"] for r in conn.execute(
            "SELECT indexdef FROM pg_indexes WHERE schemaname='public' AND tablename='price_data' ORDER BY indexname",
        ).fetchall()]
        plans = {}
        for name, sql, params in (
            ("session_read", f"""SELECT timestamp_ist,open,high,low,close FROM public.price_data
                WHERE {SESSION_FILTER} AND timestamp_ist >= %s AND timestamp_ist < %s ORDER BY timestamp_ist""",
             (*SOURCE, lower, lower + timedelta(days=1))),
            ("session_dates", f"""SELECT DISTINCT (timestamp_ist AT TIME ZONE 'Asia/Kolkata')::date FROM public.price_data
                WHERE {SESSION_FILTER} AND timestamp_ist < %s""", (*SOURCE, lower)),
        ):
            # Plain EXPLAIN plans the query without executing it.
            plan = conn.execute("EXPLAIN (FORMAT JSON) " + sql, params).fetchone()["QUERY PLAN"][0]["Plan"]
            scans = _scans(plan)
            plans[name] = {"scans": scans, "uses_index": bool(scans) and all(s["node_type"] != "Seq Scan" for s in scans)}
        schema = conn.execute(
            "SELECT obj_description(oid,'pg_namespace') AS marker FROM pg_namespace WHERE nspname=%s", (SCHEMA,),
        ).fetchone()
        tables = {r["relname"]: r["marker"] == MARKER for r in conn.execute(
            """SELECT c.relname,obj_description(c.oid,'pg_class') AS marker
               FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
               WHERE n.nspname=%s AND c.relkind='r'""", (SCHEMA,),
        ).fetchall()}
        owned_indexes = {r["indexname"] for r in conn.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname=%s", (SCHEMA,),
        ).fetchall()}
    source_ok = all(p["uses_index"] for p in plans.values())
    project = {
        "schema_present": schema is not None, "schema_owned": bool(schema) and schema["marker"] == MARKER,
        "tables": {t: ("owned" if tables[t] else "unmarked") if t in tables else "absent" for t in TABLES},
        "missing_indexes": sorted(set(OWNED_INDEXES) - owned_indexes) if schema else sorted(OWNED_INDEXES),
    }
    # Absent storage or indexes are created by the next build/sync; only collisions need attention.
    collision = project["schema_present"] and (not project["schema_owned"] or "unmarked" in project["tables"].values())
    return {
        "status": "ok" if source_ok and not collision else "attention",
        "source": {"table": "public.price_data", "indexes": source_indexes, "plans": plans,
                   "index_recommendation": None if source_ok else SOURCE_INDEX_ADVICE},
        "project": project,
        "notes": "Read-only check. Missing project storage or indexes are created by the next build or sync; "
                 "source indexes are never created by this project.",
    }


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
        """Require the owned schema and core tables; return {table: marker} for every present table."""
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
        found = {r["relname"]: r["marker"] for r in rows}
        # The ledger may be absent on a pre-ledger schema, but never present and unmarked.
        if any(found.get(t) != MARKER for t in CORE_TABLES) or found.get(LEDGER, MARKER) != MARKER:
            raise PatternError("storage_collision", "Refusing writes to absent or unmarked existing tables", status=409)
        return found

    def _create_ledger(self, backfill):
        self.conn.execute("""
            CREATE TABLE nifty_trajectory_v1.processed_days (
                config_id TEXT NOT NULL REFERENCES nifty_trajectory_v1.configurations(id),
                trade_date DATE NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('processed','excluded')),
                reason TEXT,
                details JSONB NOT NULL DEFAULT '{}',
                processed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (config_id,trade_date)
            )""")
        self.conn.execute("COMMENT ON TABLE nifty_trajectory_v1.processed_days IS 'nifty-trajectory-engine:owned:v1'")
        if backfill:
            # Upgrade of a pre-ledger schema: days already stored count as processed.
            self.conn.execute("""
                INSERT INTO nifty_trajectory_v1.processed_days(config_id,trade_date,status)
                SELECT config_id,trade_date,'processed' FROM nifty_trajectory_v1.day_patterns""")

    def initialize(self):
        # Serializes initial schema creation and all project builds, not source tables.
        self.conn.execute("SELECT pg_advisory_xact_lock(761204831)")
        if self._schema() is not None:
            if LEDGER not in self.verify_owned():
                self._create_ledger(backfill=True)
            for statement in OWNED_INDEXES.values():
                self.conn.execute(statement)  # No-op when present; restores a dropped owned index.
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
        for statement in OWNED_INDEXES.values():
            self.conn.execute(statement)
        for table in CORE_TABLES:
            # Identifiers come exclusively from the fixed allowlist, never requests.
            self.conn.execute(f"COMMENT ON TABLE {SCHEMA}.{table} IS 'nifty-trajectory-engine:owned:v1'")
        self._create_ledger(backfill=False)
        return True

    def register(self, config):
        created = self.initialize()
        self.conn.execute(
            "INSERT INTO nifty_trajectory_v1.configurations(id,settings) VALUES (%s,%s) ON CONFLICT (id) DO NOTHING",
            (config.identity, Jsonb(config.canonical())),
        )
        return created

    def save(self, config, patterns, exclusions, start, end):
        """Replace derived rows and ledger flags for a whole inclusive date range."""
        return self._replace(config, patterns, exclusions, "trade_date BETWEEN %s AND %s", (start, end))

    def save_dates(self, config, patterns, exclusions, dates):
        """Replace derived rows and ledger flags for exactly these dates."""
        return self._replace(config, patterns, exclusions, "trade_date = ANY(%s)", (list(dates),))

    def _replace(self, config, patterns, exclusions, where, params):
        # `where` is one of the fixed clauses above, never request text.
        created = self.register(config)
        # Rebuilds may remove stale derived records for excluded days, only in owned tables.
        for table in ("day_patterns", LEDGER):
            self.conn.execute(
                f"DELETE FROM {SCHEMA}.{table} WHERE config_id=%s AND {where}", (config.identity, *params),
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
            cursor.executemany(
                """INSERT INTO nifty_trajectory_v1.processed_days(config_id,trade_date,status,reason,details)
                   VALUES (%s,%s,%s,%s,%s)""",
                [(config.identity, p["trade_date"], "processed", None, Jsonb({})) for p in patterns]
                + [(config.identity, e["trade_date"], "excluded", e["reason"], Jsonb(e["details"])) for e in exclusions],
            )
        return created

    def reset(self, config):
        """Delete one configuration's derived rows and ledger flags; source data is never touched."""
        created = self.register(config)
        for table in (LEDGER, "day_patterns", "clusters"):
            self.conn.execute(f"DELETE FROM {SCHEMA}.{table} WHERE config_id=%s", (config.identity,))
        return created

    def ledger(self, config_id):
        return {r["trade_date"]: r["status"] for r in self.conn.execute(
            "SELECT trade_date,status FROM nifty_trajectory_v1.processed_days WHERE config_id=%s", (config_id,),
        ).fetchall()}

    def require_configuration(self, config_id):
        """Open a read-only snapshot; return whether the ledger table exists yet."""
        self.conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        self.conn.execute("SET LOCAL statement_timeout = '30s'")
        found = self.verify_owned()
        if not self.conn.execute("SELECT 1 FROM nifty_trajectory_v1.configurations WHERE id=%s", (config_id,)).fetchone():
            raise PatternError("configuration_not_built", "Build history with this exact configuration first", status=409)
        return LEDGER in found

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

    def status(self, config_id, ledger=True):
        result = self.conn.execute("""
            SELECT COUNT(*) AS stored_days,MIN(trade_date)::text AS first_date,
                   MAX(trade_date)::text AS last_date,COUNT(DISTINCT path_code) AS unique_patterns
            FROM nifty_trajectory_v1.day_patterns WHERE config_id=%s""", (config_id,),
        ).fetchone()
        if not ledger:
            # Pre-ledger schema that no write operation has upgraded yet.
            return {**result, "processed_days": None, "excluded_days": None, "last_processed_date": None}
        flags = self.conn.execute("""
            SELECT COUNT(*) FILTER (WHERE status='processed') AS processed_days,
                   COUNT(*) FILTER (WHERE status='excluded') AS excluded_days,
                   MAX(trade_date)::text AS last_processed_date
            FROM nifty_trajectory_v1.processed_days WHERE config_id=%s""", (config_id,),
        ).fetchone()
        return {**result, **flags}
