"""Environment configuration. Values come from the process environment, optionally seeded by a .env file."""

import os

from dotenv import find_dotenv, load_dotenv


def load_environment():
    """Load the nearest .env (searching up from the working directory); real environment variables win."""
    path = os.environ.get("NIFTY_ENV_FILE") or find_dotenv(usecwd=True)
    if path:
        load_dotenv(path, override=False)
    return path or None


def get_port(value=None) -> int:
    value = os.environ.get("PORT") if value is None else str(value)
    if value is None:
        raise ValueError("Pass --port or set the PORT environment variable")
    try:
        port = int(value)
    except ValueError:
        raise ValueError("PORT must be an integer") from None
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be between 1 and 65535")
    return port
