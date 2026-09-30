"""Overwing Beacon: is a site reachable by agents? One paid lookup, no API key."""

from __future__ import annotations

import asyncio
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
    changes worth making first. It costs $5 by card or $1 over x402; there is no key.

        beacon = Beacon()
        check = beacon.start("example.com")     # a person pays at check.checkout_url
        check = beacon.wait_for_report(check.id)
        if check.complete: check.top_fixes
    """

    def __init__(self, *, base_url: str | None = None, timeout: float = 15.0, max_retries: int = 2, transport: httpx.BaseTransport | None = None) -> None:
        super().__init__(None, base_url=base_url, timeout=timeout, max_retries=max_retries, transport=transport)

    def __enter__(self) -> "Beacon":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def start(self, url: str) -> BeaconCheck:
        """Start a check paid by card. Nothing runs until `checkout_url` is paid."""
        return BeaconCheck.from_dict(self._request("POST", _CHECKS, json={"url": url}))

    def report(self, check_id: str) -> BeaconCheck:
        """Where a check stands: awaiting_payment (with the checkout link), running, or complete with the report."""
        return BeaconCheck.from_dict(self._request("GET", f"{_CHECKS}/{segment(check_id)}", accept=(402,)))

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
        """A real report, free: the check of overwing.ai itself. The same shape a paid check returns."""
        return BeaconCheck.from_dict({"status": "complete", **self._request("GET", "/api/v1/beacon/sample")})

    def overview(self) -> dict[str, Any]:
        """What is checked, what it costs, and the endpoints."""
        return self._request("GET", "/api/v1/beacon")

    def x402_url(self, url: str) -> str:
        """The address an x402 client pays $1 at to get the report in one call."""
        return _x402_url(self.base_url, url)


class AsyncBeacon(AsyncHTTP):
    """Asynchronous Beacon client. See `Beacon`."""

    def __init__(self, *, base_url: str | None = None, timeout: float = 15.0, max_retries: int = 2, transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(None, base_url=base_url, timeout=timeout, max_retries=max_retries, transport=transport)

    async def __aenter__(self) -> "AsyncBeacon":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def start(self, url: str) -> BeaconCheck:
        return BeaconCheck.from_dict(await self._request("POST", _CHECKS, json={"url": url}))

    async def report(self, check_id: str) -> BeaconCheck:
        return BeaconCheck.from_dict(await self._request("GET", f"{_CHECKS}/{segment(check_id)}", accept=(402,)))

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
