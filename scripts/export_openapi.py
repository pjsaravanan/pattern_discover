"""Export the Python API contract as a checked-in OpenAPI JSON file."""

import json
from pathlib import Path

from nifty_api.main import app


def contract():
    schema = app.openapi()
    schema = {**schema, "paths": {path.removeprefix("/api"): value for path, value in schema["paths"].items()},
              "servers": [{"url": "/api", "description": "Base API path"}]}
    return schema


if __name__ == "__main__":
    destination = Path(__file__).resolve().parents[1] / "docs" / "openapi.json"
    destination.write_text(json.dumps(contract(), indent=2) + "\n", encoding="utf-8")
