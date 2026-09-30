"""One HTTP layer shared by the Overwing, Atlas and Tower clients."""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Callable
from urllib.parse import quote, urlencode

import httpx

from ._errors import OverwingError

DEFAULT_BASE_URL = "https://overwing.ai"
SDK_VERSION = "0.4.0"
_USER_AGENT = f"overwing-python/{SDK_VERSION}"

HeadersHook = Callable[[httpx.Headers], None]


def resolve_base_url(base_url: str | None) -> str:
    return (base_url or os.environ.get("OVERWING_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def error_from(res: httpx.Response) -> OverwingError:
    """Build an error from a response. Handles both {"error": "text"} and Tower's {"error": {code, message, ...}}."""
    ra = res.headers.get("retry-after")
    retry_after = float(ra) if ra else None
    try:
        body = res.json()
    except ValueError:
        return OverwingError(f"HTTP {res.status_code}", res.status_code, retry_after)
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        retryable = err.get("retryable")
        return OverwingError(
            str(err.get("message") or f"HTTP {res.status_code}"),
            res.status_code,
            retry_after,
            code=err.get("code") if isinstance(err.get("code"), str) else None,
            field=err.get("field") if isinstance(err.get("field"), str) else None,
            retryable=retryable if isinstance(retryable, bool) else None,
            suggested_fix=err.get("suggested_fix") if isinstance(err.get("suggested_fix"), str) else None,
            body=body,
        )
    return OverwingError(str(err) if err is not None else f"HTTP {res.status_code}", res.status_code, retry_after, body=body)


def _retryable(res: httpx.Response) -> bool:
    if res.status_code == 429:
        ra = res.headers.get("retry-after")
        return ra is None or float(ra) <= 5
    return res.status_code >= 500


def compact(body: dict[str, Any]) -> dict[str, Any]:
    """Drop keys whose value is None so optional fields are omitted from the request."""
    return {k: v for k, v in body.items() if v is not None}


def query(params: dict[str, Any]) -> str:
    q = {k: ("true" if v is True else "false" if v is False else v) for k, v in params.items() if v is not None and v != ""}
    return f"?{urlencode(q)}" if q else ""


def segment(value: str | int) -> str:
    """Encode one path segment."""
    return quote(str(value), safe="")


class _Base:
    """Holds the credential and request settings. `token` may be None for keyless calls."""

    def __init__(self, token: str | None, *, base_url: str | None, timeout: float, max_retries: int) -> None:
        self._token = token
        self.base_url = resolve_base_url(base_url)
        self._timeout = timeout
        self._max_retries = max_retries

    def _headers(self, idempotency_key: str | None = None, json_body: bool = False) -> dict[str, str]:
        h = {"Accept": "application/json", "User-Agent": _USER_AGENT}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        if json_body:
            h["Content-Type"] = "application/json"
        if idempotency_key:
            h["Idempotency-Key"] = idempotency_key
        return h


class SyncHTTP(_Base):
    def __init__(self, token: str | None, *, base_url: str | None = None, timeout: float = 15.0, max_retries: int = 2, transport: httpx.BaseTransport | None = None) -> None:
        super().__init__(token, base_url=base_url, timeout=timeout, max_retries=max_retries)
        self._transport = transport
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout, transport=transport)

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, *, json: Any = None, idempotency_key: str | None = None, accept: tuple[int, ...] = (), on_headers: HeadersHook | None = None) -> Any:
        attempt = 0
        while True:
            try:
                res = self._http.request(method, path, json=json, headers=self._headers(idempotency_key, json is not None))
            except httpx.HTTPError as e:
                if attempt < self._max_retries:
                    attempt += 1
                    time.sleep(0.25 * attempt)
                    continue
                raise OverwingError(f"Overwing API unreachable: {e}") from e
            if on_headers is not None:
                on_headers(res.headers)
            if res.status_code in accept:
                return res.json()
            if _retryable(res) and attempt < self._max_retries:
                attempt += 1
                ra = res.headers.get("retry-after")
                time.sleep(float(ra) if ra else 0.3 * attempt)
                continue
            if res.is_error:
                raise error_from(res)
            return res.json() if res.content else None


class AsyncHTTP(_Base):
    def __init__(self, token: str | None, *, base_url: str | None = None, timeout: float = 15.0, max_retries: int = 2, transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(token, base_url=base_url, timeout=timeout, max_retries=max_retries)
        self._transport = transport
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=timeout, transport=transport)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, *, json: Any = None, idempotency_key: str | None = None, accept: tuple[int, ...] = (), on_headers: HeadersHook | None = None) -> Any:
        attempt = 0
        while True:
            try:
                res = await self._http.request(method, path, json=json, headers=self._headers(idempotency_key, json is not None))
            except httpx.HTTPError as e:
                if attempt < self._max_retries:
                    attempt += 1
                    await asyncio.sleep(0.25 * attempt)
                    continue
                raise OverwingError(f"Overwing API unreachable: {e}") from e
            if on_headers is not None:
                on_headers(res.headers)
            if res.status_code in accept:
                return res.json()
            if _retryable(res) and attempt < self._max_retries:
                attempt += 1
                ra = res.headers.get("retry-after")
                await asyncio.sleep(float(ra) if ra else 0.3 * attempt)
                continue
            if res.is_error:
                raise error_from(res)
            return res.json() if res.content else None
