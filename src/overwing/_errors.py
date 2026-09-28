from __future__ import annotations

from typing import Any


class OverwingError(Exception):
    """Any non-2xx answer from the Overwing API, or a transport failure.

    Overwing Tower errors are typed for agents: `code` names the problem, `field`
    the request field at fault, `retryable` whether repeating the same request can
    succeed, and `suggested_fix` what to change.
    """

    def __init__(
        self,
        message: str,
        status: int = 0,
        retry_after_seconds: float | None = None,
        *,
        code: str | None = None,
        field: str | None = None,
        retryable: bool | None = None,
        suggested_fix: str | None = None,
        body: Any = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after_seconds = retry_after_seconds
        self.code = code
        self.field = field
        self.retryable = retryable
        self.suggested_fix = suggested_fix
        self.body = body
