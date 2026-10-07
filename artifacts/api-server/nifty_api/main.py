"""HTTP entry point. Importing this module never connects to a database."""

from fastapi import FastAPI
from pydantic import BaseModel


class HealthStatus(BaseModel):
    status: str


app = FastAPI(
    title="Api",
    description="NIFTY intraday trajectory pattern API",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
)


@app.get(
    "/api/healthz",
    response_model=HealthStatus,
    operation_id="healthCheck",
    tags=["health"],
    summary="Health check",
)
def health_check() -> HealthStatus:
    """Returns process health, independently of database availability."""
    return HealthStatus(status="ok")
