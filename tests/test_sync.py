import contextlib
from datetime import date, datetime, timedelta
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from nifty_api.__main__ import main
from nifty_api.engine import IST
from nifty_api.main import app
from nifty_api.storage import CORE_TABLES, LEDGER, MARKER, PatternStore
from nifty_api.sync import chunks, last_complete_session, pending_dates, sync_history
from fixtures import CONFIG, bars
from test_boundary import Recorder

D = [date(2026, 1, 5) + timedelta(days=i) for i in range(10)]


class FakeStore:
    """Records sync writes; the ledger is shared across store instances like a database."""
    ledger_rows, saves, resets = {}, [], []

    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def register(self, config):
        return False
    def reset(self, config):
        FakeStore.resets.append(config.identity)
        FakeStore.ledger_rows.clear()
        return False
    def ledger(self, config_id):
        return dict(FakeStore.ledger_rows)
    def save_dates(self, config, patterns, exclusions, dates):
        FakeStore.saves.append(list(dates))
        for p in patterns:
            FakeStore.ledger_rows[date.fromisoformat(p["trade_date"])] = "processed"
        for e in exclusions:
            FakeStore.ledger_rows[date.fromisoformat(e["trade_date"])] = "excluded"


def source(start, end, position=None):
    return {d: bars(d.isoformat()) for d in D if start <= d <= end and d != D[3]}  # D[3] vanished on re-read


class SyncPlanTests(unittest.TestCase):
    def test_developing_session_is_never_eligible(self):
        self.assertEqual(last_complete_session(datetime(2026, 10, 8, 15, 29, tzinfo=IST)), date(2026, 10, 7))
        self.assertEqual(last_complete_session(datetime(2026, 10, 8, 15, 30, tzinfo=IST)), date(2026, 10, 8))

    def test_first_load_incremental_and_recent_excluded_recheck(self):
        self.assertEqual(pending_dates(D, {}), D)
        ledger = {d: "processed" for d in D[:8]} | {D[1]: "excluded", D[8]: "excluded"}
        # Old exclusion D[1] stays flagged; recent exclusion D[8] and unflagged D[9] are pending.
        self.assertEqual(pending_dates(D, ledger), [D[8], D[9]])

    def test_chunks_follow_consecutive_sessions_and_span_limit(self):
        self.assertEqual(chunks(D, [D[0], D[1], D[4], D[5]]), [[D[0], D[1]], [D[4], D[5]]])
        long = [date(2020, 1, 1) + timedelta(days=i) for i in range(400)]
        runs = chunks(long, long)
        self.assertEqual([len(r) for r in runs], [366, 34])


class SyncRunTests(unittest.TestCase):
    def setUp(self):
        FakeStore.ledger_rows, FakeStore.saves, FakeStore.resets = {}, [], []
        self.patches = [patch("nifty_api.sync.PatternStore", FakeStore),
                        patch("nifty_api.sync.read_source", side_effect=source),
                        patch("nifty_api.sync.source_dates", return_value=D)]
        for p in self.patches:
            p.start()
        self.now = datetime(2026, 1, 20, 16, 0, tzinfo=IST)
    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_first_run_flags_everything_and_second_run_is_a_no_op(self):
        first = sync_history(CONFIG, now=self.now)
        self.assertEqual((first["pending_days"], first["stored_days"]), (10, 9))
        self.assertEqual(first["excluded_sessions"][0]["reason"], "no_session_rows")
        self.assertEqual(len(FakeStore.ledger_rows), 10)
        FakeStore.saves.clear()
        second = sync_history(CONFIG, now=self.now)
        self.assertEqual((second["pending_days"], second["already_processed_days"]), (0, 10))
        self.assertEqual(FakeStore.saves, [])

    def test_rebuild_resets_only_then_reprocesses_all(self):
        sync_history(CONFIG, now=self.now)
        result = sync_history(CONFIG, rebuild=True, now=self.now)
        self.assertEqual(FakeStore.resets, [CONFIG.identity])
        self.assertEqual(result["pending_days"], 10)


class LedgerStorageTests(unittest.TestCase):
    def test_pre_ledger_schema_is_upgraded_and_backfilled(self):
        store = PatternStore()
        store.conn = Recorder(schema={"marker": MARKER, "owned": True},
                              tables=[{"relname": t, "marker": MARKER} for t in CORE_TABLES])
        store.initialize()
        sql = [s for s, _ in store.conn.commands]
        self.assertTrue(any("CREATE TABLE nifty_trajectory_v1.processed_days" in s for s in sql))
        self.assertTrue(any("SELECT config_id,trade_date,'processed' FROM nifty_trajectory_v1.day_patterns" in s
                            for s in sql))

    def test_unmarked_ledger_is_a_collision(self):
        store = PatternStore()
        store.conn = Recorder(schema={"marker": MARKER, "owned": True},
                              tables=[{"relname": t, "marker": MARKER} for t in CORE_TABLES]
                              + [{"relname": LEDGER, "marker": None}])
        with self.assertRaises(Exception):
            store.initialize()
        self.assertTrue(all(s.lstrip().startswith("SELECT") for s, _ in store.conn.commands))

    def test_date_saves_flag_processed_and_excluded_in_owned_tables(self):
        store = PatternStore()
        store.conn = Recorder()
        exclusion = {"trade_date": "2026-01-06", "reason": "incomplete_session", "details": {}}
        from nifty_api.engine import encode_day
        store.save_dates(CONFIG, [encode_day(bars(), CONFIG, complete=True)], [exclusion], D[:2])
        sql, rows = next((s, p) for s, p in store.conn.commands if "INSERT INTO nifty_trajectory_v1.processed_days" in s)
        self.assertEqual([r[2] for r in rows], ["processed", "excluded"])
        deletes = [s for s, _ in store.conn.commands if s.lstrip().startswith("DELETE")]
        self.assertTrue(deletes and all("nifty_trajectory_v1." in s and "trade_date = ANY" in s for s in deletes))


class IndexTests(unittest.TestCase):
    def test_existing_schema_restores_missing_owned_indexes(self):
        store = PatternStore()
        store.conn = Recorder(schema={"marker": MARKER, "owned": True},
                              tables=[{"relname": t, "marker": MARKER} for t in (*CORE_TABLES, LEDGER)])
        store.initialize()
        created = [s for s, _ in store.conn.commands if s.startswith("CREATE INDEX IF NOT EXISTS")]
        self.assertEqual(len(created), 2)
        self.assertTrue(all("nifty_trajectory_v1." in s for s in created))

    def test_check_flags_sequential_source_scan_and_never_writes(self):
        seq = {"QUERY PLAN": [{"Plan": {"Node Type": "Sort", "Plans": [
            {"Node Type": "Seq Scan", "Relation Name": "price_data"}]}}]}
        idx = {"QUERY PLAN": [{"Plan": {"Node Type": "Bitmap Heap Scan", "Relation Name": "price_data", "Plans": [
            {"Node Type": "Bitmap Index Scan", "Index Name": "price_data_lookup"}]}}]}

        class Conn(Recorder):
            plan = seq
            def execute(self, sql, params=None):
                self.commands.append((sql, params))
                if sql.startswith("EXPLAIN"):
                    return type("R", (), {"fetchone": lambda _: Conn.plan})()
                if "FROM pg_namespace" in sql:
                    return type("R", (), {"fetchone": lambda _: None})()
                return type("R", (), {"fetchall": lambda _: [], "fetchone": lambda _: None})()

        from nifty_api.storage import check_database
        for plan, expected in ((seq, "attention"), (idx, "ok")):
            Conn.plan = plan
            fake = Conn()
            with patch("nifty_api.storage.connection", return_value=fake):
                result = check_database()
            self.assertEqual(result["status"], expected)
            self.assertEqual(result["project"]["tables"][LEDGER], "absent")
            self.assertEqual(fake.commands[0][0], "SET TRANSACTION READ ONLY")
            self.assertFalse(any(s.lstrip().split()[0] in {"INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP"}
                                 for s, _ in fake.commands))
        self.assertIsNone(result["source"]["index_recommendation"])


class InterfaceTests(unittest.TestCase):
    def setUp(self):
        # Never let a developer's real .env leak into tests.
        self.env = patch.dict(os.environ, {"NIFTY_ENV_FILE": os.devnull})
        self.env.start()
    def tearDown(self):
        self.env.stop()

    def test_env_file_seeds_but_never_overrides_environment(self):
        from nifty_api.settings import load_environment
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, ".env")
            with open(path, "w") as f:
                f.write("NIFTY_TEST_A=from_file\nNIFTY_TEST_B=from_file\n")
            with patch.dict(os.environ, {"NIFTY_ENV_FILE": path, "NIFTY_TEST_B": "from_env"}):
                load_environment()
                self.assertEqual((os.environ["NIFTY_TEST_A"], os.environ["NIFTY_TEST_B"]), ("from_file", "from_env"))

    def test_cli_encode_matches_api_result(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "bars.json")
            with open(path, "w") as f:
                json.dump([b.model_dump(mode="json") for b in bars()], f)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["encode", "--band-edges", "0.25,0.5,1,2", "--close-tolerance", "0",
                             "--bars", path, "--complete-session"])
        self.assertEqual(code, 0)
        result = json.loads(out.getvalue())
        self.assertEqual(result["config_id"], CONFIG.identity)
        self.assertEqual(result["close_code"], "7/")

    def test_cli_requires_explicit_configuration(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = main(["sync"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(err.getvalue())["error"], "missing_configuration")

    def test_sync_endpoint_is_documented_and_protected(self):
        with TestClient(app) as client:
            paths = client.get("/api/openapi.json").json()["paths"]
        self.assertTrue(paths["/api/history/sync"]["post"]["security"])
