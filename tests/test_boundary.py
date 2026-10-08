import unittest
from datetime import date
from unittest.mock import patch

from nifty_api.errors import PatternError
from nifty_api.storage import MARKER, PatternStore, TABLES, build_patterns, read_source
from nifty_api.engine import encode_day
from fixtures import CONFIG, bars


class Result:
    def __init__(self, one=None, rows=None):
        self.one, self.rows = one, rows or []
    def fetchone(self):
        return self.one
    def fetchall(self):
        return self.rows


class Recorder:
    def __init__(self, schema=None, tables=None):
        self.commands = []
        self.schema = schema
        self.tables = tables
    def execute(self, sql, params=None):
        self.commands.append((sql, params))
        if "FROM pg_namespace" in sql:
            return Result(self.schema)
        if "FROM pg_class" in sql:
            return Result(rows=self.tables or [])
        if "COUNT(*) AS n" in sql:
            return Result({"n": 0})
        return Result()
    def cursor(self):
        return self
    def executemany(self, sql, params):
        self.commands.append((sql, params))
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False


class DatabaseBoundaryTests(unittest.TestCase):
    def test_source_transaction_is_read_only(self):
        fake = Recorder()
        with patch("nifty_api.storage.connection", return_value=fake):
            self.assertEqual(read_source(date(2026, 1, 1), date(2026, 1, 2)), {})
        self.assertEqual(fake.commands[0][0], "SET TRANSACTION READ ONLY")
        self.assertTrue(any("FROM public.price_data" in s for s, _ in fake.commands))
        self.assertFalse(any(s.lstrip().split()[0] in {"INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP"} for s, _ in fake.commands))

    def test_all_writes_target_only_new_owned_schema(self):
        store = PatternStore()
        store.conn = Recorder()
        pattern = encode_day(bars(), CONFIG, complete=True)
        store.save(CONFIG, [pattern], [], date(2026, 1, 1), date(2026, 1, 6))
        writes = [s for s, _ in store.conn.commands if s.lstrip().split()[0] in {"INSERT", "DELETE", "CREATE", "COMMENT"}]
        self.assertGreater(len(writes), 5)
        for sql in writes:
            self.assertIn("nifty_trajectory_v1", sql)
            self.assertNotIn("public.", sql)
            self.assertNotIn("price_data", sql)
            self.assertNotIn("candles", sql)

    def test_unmarked_existing_schema_is_never_changed(self):
        store = PatternStore()
        store.conn = Recorder(schema={"marker": None, "owned": True})
        with self.assertRaises(PatternError) as error:
            store.initialize()
        self.assertEqual(error.exception.code, "storage_collision")
        self.assertTrue(all(s.lstrip().startswith("SELECT") for s, _ in store.conn.commands))

    def test_unmarked_or_missing_table_cannot_be_overwritten(self):
        store = PatternStore()
        store.conn = Recorder(schema={"marker": MARKER, "owned": True},
                              tables=[{"relname": t, "marker": None} for t in TABLES])
        with self.assertRaises(PatternError):
            store.initialize()
        self.assertTrue(all(s.lstrip().startswith("SELECT") for s, _ in store.conn.commands))

    def test_backslash_lookup_is_literal_parameterized_and_date_bounded(self):
        store = PatternStore()
        store.conn = Recorder()
        prefix = "0O|1\\[Q2,Q1]"
        store.count_prefix(CONFIG.identity, prefix, date(2026, 1, 5))
        sql, params = store.conn.commands[-1]
        self.assertIn("ESCAPE ''", sql)
        self.assertIn("d.trade_date < %s", sql)
        self.assertNotIn(prefix, sql)
        self.assertEqual(params[2], prefix + "|%")

    def test_estimate_reads_use_a_consistent_read_only_snapshot(self):
        store = PatternStore()
        store.conn = Recorder(schema={"marker": MARKER, "owned": True},
                              tables=[{"relname": t, "marker": MARKER} for t in TABLES])
        with self.assertRaises(PatternError):  # No configuration row in this fixture.
            store.require_configuration(CONFIG.identity)
        self.assertEqual(store.conn.commands[0][0], "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")

    def test_build_reports_incomplete_days_and_keeps_e_days(self):
        groups = {date(2026, 1, 5): bars(), date(2026, 1, 6): bars("2026-01-06")[:17]}
        patterns, excluded = build_patterns(groups, CONFIG)
        self.assertEqual(len(patterns), 1)
        self.assertIn("E", patterns[0]["path_code"])
        self.assertEqual(excluded[0]["trade_date"], "2026-01-06")
        self.assertEqual(excluded[0]["reason"], "incomplete_session")
