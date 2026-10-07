"""Run the existing API Server artifact with its managed PORT."""

import os

import uvicorn


def get_port() -> int:
    value = os.environ.get("PORT")
    if value is None:
        raise ValueError("PORT environment variable is required")
    try:
        port = int(value)
    except ValueError:
        raise ValueError("PORT must be an integer") from None
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be between 1 and 65535")
    return port


if __name__ == "__main__":
    uvicorn.run(
        "nifty_api.main:app",
        host="0.0.0.0",
        port=get_port(),
        access_log=False,
    )
