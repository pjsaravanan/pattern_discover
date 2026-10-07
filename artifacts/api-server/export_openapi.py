"""Export the Python API contract for the existing monorepo code generators."""

import json
from pathlib import Path

from nifty_api.main import app


def contract():
    schema = app.openapi()
    schema = {**schema, "paths": {path.removeprefix("/api"): value for path, value in schema["paths"].items()},
              "servers": [{"url": "/api", "description": "Base API path"}]}
    return schema


if __name__ == "__main__":
    destination = Path(__file__).resolve().parents[2] / "lib/api-spec/openapi.yaml"
    # JSON is a valid YAML 1.2 document. No additional YAML runtime dependency.
    destination.write_text(json.dumps(contract(), indent=2) + "\n")
