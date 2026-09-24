from __future__ import annotations

import os
import time
from typing import Any

import httpx

from ._errors import OverwingError
from ._types import BatchResult, Evaluation

DEFAULT_BASE_URL = "https://overwing.ai"
_USER_AGENT = "overwing-python/0.2.0"


def _resolve(api_key: str | None, base_url: str | None) -> tuple[str, str]:
    key = api_key or os.environ.get("OVERWING_API_KEY")
    if not key:
        raise OverwingError("Overwing API key missing. Pass api_key= or set OVERWING_API_KEY. Get one at https://overwing.ai/login")
    return key, (base_url or os.environ.get("OVERWING_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def _error_from(res: httpx.Response) -> OverwingError:
    try:
        message = str(res.json().get("error", f"HTTP {res.status_code}"))
    except ValueError:
        message = f"HTTP {res.status_code}"
    ra = res.headers.get("retry-after")
    return OverwingError(message, res.status_code, float(ra) if ra else None)


def _retryable(res: httpx.Response) -> bool:
    if res.status_code == 429:
        ra = res.headers.get("retry-after")
        return ra is None or float(ra) <= 5
    return res.status_code >= 500



def _compact(body: dict[str, Any]) -> dict[str, Any]:
    """Drop keys whose value is None so optional fields are omitted from the request."""
    return {k: v for k, v in body.items() if v is not None}

class _Base:
    def __init__(self, api_key: str | None = None, *, base_url: str | None = None, timeout: float = 15.0, max_retries: int = 2) -> None:
        self._api_key, self.base_url = _resolve(api_key, base_url)
        self._timeout = timeout
        self._max_retries = max_retries

    def _headers(self, idempotency_key: str | None = None, json_body: bool = False) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self._api_key}", "Accept": "application/json", "User-Agent": _USER_AGENT}
        if json_body:
            h["Content-Type"] = "application/json"
        if idempotency_key:
            h["Idempotency-Key"] = idempotency_key
        return h


class Overwing(_Base):
    """Synchronous client for the Overwing API."""

    def __init__(self, api_key: str | None = None, *, base_url: str | None = None, timeout: float = 15.0, max_retries: int = 2, transport: httpx.BaseTransport | None = None) -> None:
        super().__init__(api_key, base_url=base_url, timeout=timeout, max_retries=max_retries)
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout, transport=transport)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "Overwing":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _request(self, method: str, path: str, *, json: Any = None, idempotency_key: str | None = None, accept: tuple[int, ...] = ()) -> Any:
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
            if res.status_code in accept:
                return res.json()
            if _retryable(res) and attempt < self._max_retries:
                attempt += 1
                ra = res.headers.get("retry-after")
                time.sleep(float(ra) if ra else 0.3 * attempt)
                continue
            if res.is_error:
                raise _error_from(res)
            return res.json() if res.content else None

    def evaluate(self, text: str, *, rule_set: str = "content-safety", metadata: dict[str, Any] | None = None, context: dict[str, Any] | None = None, idempotency_key: str | None = None) -> Evaluation:
        """Score one text. `context` carries facts the rules may reference (recipient, channel, ownership). Raises OverwingError on any non-2xx."""
        return Evaluation.from_dict(self._request("POST", "/api/v1/evaluate", json=_compact({"input": text, "rule_set": rule_set, "metadata": metadata, "context": context}), idempotency_key=idempotency_key))

    def evaluate_batch(self, items: list[dict[str, Any]], *, rule_set: str = "content-safety", context: dict[str, Any] | None = None, idempotency_key: str | None = None) -> BatchResult:
        """Score up to 50 texts. Each item: {"input": str, "id"?: str, "metadata"?: dict, "context"?: dict}."""
        return BatchResult.from_dict(self._request("POST", "/api/v1/evaluate/batch", json=_compact({"rule_set": rule_set, "items": items, "context": context}), idempotency_key=idempotency_key, accept=(502,)))

    def get_evaluation(self, evaluation_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/evaluations/{evaluation_id}")

    def list_rule_sets(self, include_inactive: bool = False) -> list[dict[str, Any]]:
        return self._request("GET", "/api/v1/rule-sets" + ("?include_inactive=true" if include_inactive else ""))["rule_sets"]

    def get_rule_set(self, slug: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/rule-sets/{slug}")

    def create_rule_set(self, *, name: str, slug: str, rules: list[dict[str, Any]], description: str | None = None) -> dict[str, Any]:
        return self._request("POST", "/api/v1/rule-sets", json={"name": name, "slug": slug, "rules": rules, "description": description})

    def usage(self, days: int | None = None) -> dict[str, Any]:
        return self._request("GET", "/api/v1/usage" + (f"?days={days}" if days else ""))

    def me(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/me")


class AsyncOverwing(_Base):
    """Asynchronous client for the Overwing API."""

    def __init__(self, api_key: str | None = None, *, base_url: str | None = None, timeout: float = 15.0, max_retries: int = 2, transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(api_key, base_url=base_url, timeout=timeout, max_retries=max_retries)
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=timeout, transport=transport)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "AsyncOverwing":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _request(self, method: str, path: str, *, json: Any = None, idempotency_key: str | None = None, accept: tuple[int, ...] = ()) -> Any:
        import asyncio

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
            if res.status_code in accept:
                return res.json()
            if _retryable(res) and attempt < self._max_retries:
                attempt += 1
                ra = res.headers.get("retry-after")
                await asyncio.sleep(float(ra) if ra else 0.3 * attempt)
                continue
            if res.is_error:
                raise _error_from(res)
            return res.json() if res.content else None

    async def evaluate(self, text: str, *, rule_set: str = "content-safety", metadata: dict[str, Any] | None = None, context: dict[str, Any] | None = None, idempotency_key: str | None = None) -> Evaluation:
        return Evaluation.from_dict(await self._request("POST", "/api/v1/evaluate", json=_compact({"input": text, "rule_set": rule_set, "metadata": metadata, "context": context}), idempotency_key=idempotency_key))

    async def evaluate_batch(self, items: list[dict[str, Any]], *, rule_set: str = "content-safety", context: dict[str, Any] | None = None, idempotency_key: str | None = None) -> BatchResult:
        return BatchResult.from_dict(await self._request("POST", "/api/v1/evaluate/batch", json=_compact({"rule_set": rule_set, "items": items, "context": context}), idempotency_key=idempotency_key, accept=(502,)))

    async def get_evaluation(self, evaluation_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/v1/evaluations/{evaluation_id}")

    async def list_rule_sets(self, include_inactive: bool = False) -> list[dict[str, Any]]:
        return (await self._request("GET", "/api/v1/rule-sets" + ("?include_inactive=true" if include_inactive else "")))["rule_sets"]

    async def get_rule_set(self, slug: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/v1/rule-sets/{slug}")

    async def create_rule_set(self, *, name: str, slug: str, rules: list[dict[str, Any]], description: str | None = None) -> dict[str, Any]:
        return await self._request("POST", "/api/v1/rule-sets", json={"name": name, "slug": slug, "rules": rules, "description": description})

    async def usage(self, days: int | None = None) -> dict[str, Any]:
        return await self._request("GET", "/api/v1/usage" + (f"?days={days}" if days else ""))

    async def me(self) -> dict[str, Any]:
        return await self._request("GET", "/api/v1/me")
