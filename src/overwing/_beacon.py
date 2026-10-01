"""Overwing Beacon: is a site reachable by agents? Free: the summary with no key, the full report with one."""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any
from urllib.parse import quote

import httpx

from ._http import AsyncHTTP, SyncHTTP, segment
from ._types import BeaconCheck

_CHECKS = "/api/v1/beacon/checks"


def _x402_url(base_url: str, url: str) -> str:
    return f"{base_url}/api/x402/beacon?url={quote(url, safe='')}"


class Beacon(SyncHTTP):
    """Synchronous Beacon client.

    A check says whether an agent can find a product, read it, and use it, with the
    changes worth making first. It is free. With a key (`api_key=` or OVERWING_API_KEY)
    the report is the full one and is saved to that dashboard; with no key it is the
    summary: the score, the three answers and the first fix (`check.access == "summary"`).
    A key is free: POST https://overwing.ai/api/v1/signup.

        beacon = Beacon()
        check = beacon.start("example.com")
        check = beacon.wait_for_report(check.id)   # the first read runs the check
        if check.complete: check.top_fixes
    """

    def __init__(self, api_key: str | None = None, *, base_url: str | None = None, timeout: float = 15.0, max_retries: int = 2, transport: httpx.BaseTransport | None = None) -> None:
        super().__init__(api_key or os.environ.get("OVERWING_API_KEY") or None, base_url=base_url, timeout=timeout, max_retries=max_retries, transport=transport)

    def __enter__(self) -> "Beacon":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def start(self, url: str) -> BeaconCheck:
        """Start a check. Free. It runs when its report is first read."""
        return BeaconCheck.from_dict(self._request("POST", _CHECKS, json={"url": url}))

    def report(self, check_id: str) -> BeaconCheck:
        """Where a check stands: running, or complete with the report (`access` says whether it is the full one or the summary)."""
        return BeaconCheck.from_dict(self._request("GET", f"{_CHECKS}/{segment(check_id)}"))

    def wait_for_report(self, check_id: str, *, timeout: float = 600.0, interval: float = 3.0) -> BeaconCheck:
        """Poll a check until its report is ready or the timeout passes. Returns the last state seen."""
        interval = max(1.0, interval)
        deadline = time.monotonic() + timeout
        while True:
            check = self.report(check_id)
            if check.complete or time.monotonic() + interval > deadline:
                return check
            time.sleep(interval)

    def sample(self) -> BeaconCheck:
        """A real report in full: the check of overwing.ai itself. No key needed."""
        return BeaconCheck.from_dict({"status": "complete", **self._request("GET", "/api/v1/beacon/sample")})

    def overview(self) -> dict[str, Any]:
        """What is checked, what it costs, and the endpoints."""
        return self._request("GET", "/api/v1/beacon")

    def x402_url(self, url: str) -> str:
        """The address an x402 client pays $1 at to get the full report in one call, with no account."""
        return _x402_url(self.base_url, url)


class AsyncBeacon(AsyncHTTP):
    """Asynchronous Beacon client. See `Beacon`."""

    def __init__(self, api_key: str | None = None, *, base_url: str | None = None, timeout: float = 15.0, max_retries: int = 2, transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(api_key or os.environ.get("OVERWING_API_KEY") or None, base_url=base_url, timeout=timeout, max_retries=max_retries, transport=transport)

    async def __aenter__(self) -> "AsyncBeacon":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def start(self, url: str) -> BeaconCheck:
        return BeaconCheck.from_dict(await self._request("POST", _CHECKS, json={"url": url}))

    async def report(self, check_id: str) -> BeaconCheck:
        return BeaconCheck.from_dict(await self._request("GET", f"{_CHECKS}/{segment(check_id)}"))

    async def wait_for_report(self, check_id: str, *, timeout: float = 600.0, interval: float = 3.0) -> BeaconCheck:
        interval = max(1.0, interval)
        deadline = time.monotonic() + timeout
        while True:
            check = await self.report(check_id)
            if check.complete or time.monotonic() + interval > deadline:
                return check
            await asyncio.sleep(interval)

    async def sample(self) -> BeaconCheck:
        return BeaconCheck.from_dict({"status": "complete", **await self._request("GET", "/api/v1/beacon/sample")})

    async def overview(self) -> dict[str, Any]:
        return await self._request("GET", "/api/v1/beacon")

    def x402_url(self, url: str) -> str:
        return _x402_url(self.base_url, url)
