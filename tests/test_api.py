import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from nifty_api.main import app
from fixtures import CONFIG, bars


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"NIFTY_API_KEY": "unit-test-only-key"})
        self.env.start()
        self.client = TestClient(app)
        self.headers = {"X-API-Key": "unit-test-only-key"}
    def tearDown(self):
        self.client.close()
        self.env.stop()

    def payload(self):
        return {"config": CONFIG.model_dump(mode="json"), "bars": [b.model_dump(mode="json") for b in bars()],
                "complete_session": True}

    def test_api_key_is_required_for_data_operations(self):
        response = self.client.post("/patterns/encode", json=self.payload())
        self.assertEqual(response.status_code, 404)
        response = self.client.post("/api/patterns/encode", json=self.payload())
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"], "unauthorized")

    def test_api_is_open_when_no_key_is_configured(self):
        with patch.dict(os.environ, {"NIFTY_API_KEY": ""}):
            response = self.client.post("/api/patterns/encode", json=self.payload())
        self.assertEqual(response.status_code, 200, response.text)

    def test_encode_json_contract_and_backslash_roundtrip(self):
        response = self.client.post("/api/patterns/encode", headers=self.headers, json=self.payload())
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["buckets"][0]["symbol"], "\\")
        self.assertEqual(result["close_code"], "7/")
        self.assertEqual(result["config_id"], CONFIG.identity)

    def test_missing_required_config_and_support_target_are_rejected(self):
        p = self.payload()
        p["config"].pop("close_tolerance_points")
        response = self.client.post("/api/patterns/encode", headers=self.headers, json=p)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"], "invalid_request")
        self.assertNotIn("input", response.json()["details"]["errors"][0])
        p = {"config": CONFIG.model_dump(mode="json"), "trade_date": "2026-01-05", "through_position": 2}
        self.assertEqual(self.client.post("/api/patterns/match", headers=self.headers, json=p).status_code, 422)

    def test_source_incompleteness_is_machine_readable(self):
        p = self.payload()
        p["bars"].pop(0)
        response = self.client.post("/api/patterns/encode", headers=self.headers, json=p)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"], "incomplete_session")
        self.assertEqual(response.json()["details"]["missing_count"], 1)

    def test_contract_documents_operations_and_security(self):
        schema = self.client.get("/api/openapi.json").json()
        paths = schema["paths"]
        for path in ("/api/history/build", "/api/patterns/encode", "/api/patterns/match", "/api/evaluation/walk-forward"):
            self.assertIn(path, paths)
            self.assertTrue(paths[path]["post"]["security"])
        self.assertNotIn("security", paths["/api/healthz"]["get"])

    def test_root_preview_opens_docs(self):
        response = self.client.get("/api")
        self.assertEqual(response.status_code, 200)
        self.assertIn("/api/openapi.json", response.text)
