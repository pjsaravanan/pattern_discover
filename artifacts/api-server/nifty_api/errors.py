"""Sanitized, machine-readable failures."""


class PatternError(Exception):
    def __init__(self, code: str, message: str, details=None, status: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.status = status
