"""Overwing Atlas: who the AI agents are, and whether a User-Agent's claim can be trusted. Works with no API key."""

from __future__ import annotations

import os
from typing import Any

import httpx

from ._http import AsyncHTTP, SyncHTTP, query
from ._types import AtlasLookup


class _Limit:
    """Captures the lookup allowance headers of one response."""

    def __init__(self) -> None:
        self.limit: int | None = None
        self.remaining: int | None = None

    def __call__(self, headers: httpx.Headers) -> None:
        lim, rem = headers.get("x-atlas-lookup-limit"), headers.get("x-atlas-lookup-remaining")
        self.limit = int(lim) if lim is not None else None
        self.remaining = int(rem) if rem is not None else None


def _agents_path(q: str | None, purpose: str | None, operator: str | None, verification: str | None, limit: int | None, offset: int | None) -> str:
    return "/api/v1/atlas/agents" + query({"q": q, "purpose": purpose, "operator": operator, "verification": verification, "limit": limit, "offset": offset})


class Atlas(SyncHTTP):
    """Synchronous Atlas client.

    With no key, lookups use the keyless allowance (10 a day per client). A key
    (`api_key=` or OVERWING_API_KEY) raises it to 100 a day, or to your Atlas plan's limit.

        who = Atlas().lookup(request.headers["user-agent"])
        if not who.signed: ...  # the claim is only a string; treat it as unverified
    """

    def __init__(self, api_key: str | None = None, *, base_url: str | None = None, timeout: float = 15.0, max_retries: int = 2, transport: httpx.BaseTransport | None = None) -> None:
        super().__init__(api_key or os.environ.get("OVERWING_API_KEY") or None, base_url=base_url, timeout=timeout, max_retries=max_retries, transport=transport)

    @property
    def keyless(self) -> bool:
        return self._token is None

    def __enter__(self) -> "Atlas":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def lookup(self, user_agent: str) -> AtlasLookup:
        """Say what a User-Agent string claims to be. Raises OverwingError with status 429 when the allowance is spent."""
        seen = _Limit()
        body = self._request("GET", "/api/v1/atlas/lookup" + query({"user_agent": user_agent}), on_headers=seen)
        return AtlasLookup.from_dict(body, limit=seen.limit, remaining=seen.remaining)

    def agents(self, *, q: str | None = None, purpose: str | None = None, operator: str | None = None, verification: str | None = None, limit: int | None = None, offset: int | None = None) -> dict[str, Any]:
        """Browse or search the registry. Public fields for everyone; Atlas Pro and Team keys get the curated fields."""
        return self._request("GET", _agents_path(q, purpose, operator, verification, limit, offset))

    def summary(self) -> dict[str, Any]:
        """Registry counts, browser-agent traffic shares, field-scan headlines, and the report summary."""
        return self._request("GET", "/api/v1/atlas/summary")


class AsyncAtlas(AsyncHTTP):
    """Asynchronous Atlas client. See `Atlas`."""

    def __init__(self, api_key: str | None = None, *, base_url: str | None = None, timeout: float = 15.0, max_retries: int = 2, transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(api_key or os.environ.get("OVERWING_API_KEY") or None, base_url=base_url, timeout=timeout, max_retries=max_retries, transport=transport)

    @property
    def keyless(self) -> bool:
        return self._token is None

    async def __aenter__(self) -> "AsyncAtlas":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def lookup(self, user_agent: str) -> AtlasLookup:
        seen = _Limit()
        body = await self._request("GET", "/api/v1/atlas/lookup" + query({"user_agent": user_agent}), on_headers=seen)
        return AtlasLookup.from_dict(body, limit=seen.limit, remaining=seen.remaining)

    async def agents(self, *, q: str | None = None, purpose: str | None = None, operator: str | None = None, verification: str | None = None, limit: int | None = None, offset: int | None = None) -> dict[str, Any]:
        return await self._request("GET", _agents_path(q, purpose, operator, verification, limit, offset))

    async def summary(self) -> dict[str, Any]:
        return await self._request("GET", "/api/v1/atlas/summary")
