from datetime import date, datetime, time
import json
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from nifty_api import scheduler
from nifty_api.engine import IST
from nifty_api.main import app
from fixtures import CONFIG

AT = time(22, 30)
CONFIG_JSON = json.dumps(CONFIG.model_dump(mode="json"))


class SchedulerTests(unittest.TestCase):
    def test_disabled_unless_both_settings_are_present(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(scheduler.settings())
        with patch.dict(os.environ, {"SYNC_AT": "22:30"}, clear=True), self.assertRaises(ValueError):
            scheduler.settings()
        with patch.dict(os.environ, {"SYNC_AT": "10pm", "SYNC_CONFIG": CONFIG_JSON}, clear=True), \
                self.assertRaises(ValueError):
            scheduler.settings()
        with patch.dict(os.environ, {"SYNC_AT": "22:30", "SYNC_CONFIG": CONFIG_JSON}, clear=True):
            when, config = scheduler.settings()
        self.assertEqual((when, config.identity), (AT, CONFIG.identity))

    def test_next_run_catches_up_once_then_waits_for_the_next_slot(self):
        before = datetime(2026, 10, 8, 21, 0, tzinfo=IST)
        after = datetime(2026, 10, 8, 23, 5, tzinfo=IST)
        self.assertEqual(scheduler.next_run(before, AT, False), datetime(2026, 10, 8, 22, 30, tzinfo=IST))
        self.assertEqual(scheduler.next_run(after, AT, False), after)  # restart after the slot: run now
        self.assertEqual(scheduler.next_run(after, AT, True), datetime(2026, 10, 9, 22, 30, tzinfo=IST))

    def test_failures_are_recorded_without_exception_text(self):
        with patch("nifty_api.scheduler.sync_history", side_effect=RuntimeError("postgresql://secret@host")):
            self.assertFalse(scheduler.run_once(CONFIG))
        self.assertEqual(scheduler.state["last_result"], {"status": "failed", "error": "RuntimeError"})
        result = {"pending_days": 1, "stored_days": 1, "excluded_sessions": [], "through_date": date(2026, 10, 8)}
        with patch("nifty_api.scheduler.sync_history", return_value=result):
            self.assertTrue(scheduler.run_once(CONFIG))
        self.assertEqual(scheduler.state["last_result"]["through_date"], "2026-10-08")

    def test_server_starts_scheduler_only_when_configured(self):
        with patch.dict(os.environ, {"SYNC_AT": "22:30", "SYNC_CONFIG": CONFIG_JSON}), \
                patch("nifty_api.scheduler.loop") as loop, TestClient(app) as client:
            self.assertEqual(client.get("/api/healthz").status_code, 200)
        loop.assert_called_once()
        self.assertEqual(loop.call_args.args[:1], (AT,))
        scheduler.state.update(enabled=False, running=False)
        with patch.dict(os.environ, {}, clear=True), patch("nifty_api.scheduler.loop") as loop, TestClient(app):
            pass
        loop.assert_not_called()

    def test_partial_settings_fail_server_startup(self):
        with patch.dict(os.environ, {"SYNC_AT": "22:30"}, clear=True), self.assertRaises(ValueError):
            with TestClient(app):
                pass
