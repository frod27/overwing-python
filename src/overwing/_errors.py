from __future__ import annotations


class OverwingError(Exception):
    """Any non-2xx answer from the Overwing API, or a transport failure."""

    def __init__(self, message: str, status: int = 0, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after_seconds = retry_after_seconds
