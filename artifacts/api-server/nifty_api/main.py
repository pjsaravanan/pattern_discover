"""HTTP entry point. Importing this module never connects to a database."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
import psycopg
from pydantic import BaseModel

from .errors import PatternError
from .routes import router

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

app.include_router(router)


@app.exception_handler(RequestValidationError)
async def request_error_handler(request: Request, error: RequestValidationError):
    # Report useful field locations without echoing raw inputs or exception objects.
    return JSONResponse(status_code=422, content={
        "error": "invalid_request", "message": "Request validation failed",
        "details": {"errors": [
            {"field": list(item["loc"]), "type": item["type"], "message": item["msg"]}
            for item in error.errors()
        ]},
    })


@app.exception_handler(PatternError)
async def pattern_error_handler(request: Request, error: PatternError):
    return JSONResponse(status_code=error.status, content={
        "error": error.code, "message": error.message, "details": error.details,
    })


@app.exception_handler(psycopg.Error)
async def database_error_handler(request: Request, error: psycopg.Error):
    # Never log exception text: connection failures may contain credential data.
    logging.getLogger("nifty_api").error("Database operation failed; SQLSTATE=%s", error.sqlstate or "unavailable")
    return JSONResponse(status_code=503, content={
        "error": "database_unavailable", "message": "Database operation failed; check connectivity and permissions",
        "details": {},
    })


@app.get("/api", include_in_schema=False)
@app.get("/api/", include_in_schema=False)
def api_index():
    return RedirectResponse("/api/docs")


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
