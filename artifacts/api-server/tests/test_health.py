"""Regression tests for the pre-existing health contract."""

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nifty_api.main import app
from run import get_port


class HealthTests(unittest.TestCase):
    def test_exact_health_contract_without_database_configuration(self):
        with patch.dict(os.environ, {}, clear=True), TestClient(app) as client:
            response = client.get("/api/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertTrue(response.headers["content-type"].startswith("application/json"))

    def test_openapi_keeps_health_operation(self):
        with TestClient(app) as client:
            schema = client.get("/api/openapi.json").json()
        self.assertEqual(schema["info"]["title"], "Api")
        self.assertEqual(schema["paths"]["/api/healthz"]["get"]["operationId"], "healthCheck")

    def test_docs_use_prefixed_schema_url(self):
        with TestClient(app) as client:
            response = client.get("/api/docs")
        self.assertEqual(response.status_code, 200)
        self.assertIn("/api/openapi.json", response.text)

    def test_managed_port_is_required_and_validated(self):
        for value in (None, "bad", "0", "-1", "65536"):
            env = {} if value is None else {"PORT": value}
            with self.subTest(value=value), patch.dict(os.environ, env, clear=True):
                with self.assertRaises(ValueError):
                    get_port()
        with patch.dict(os.environ, {"PORT": "8080"}, clear=True):
            self.assertEqual(get_port(), 8080)


if __name__ == "__main__":
    unittest.main()
