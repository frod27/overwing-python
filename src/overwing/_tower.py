"""Overwing Tower, as an agent: typed operations on a legacy system, each ruled auto, review, or reject, with a signed receipt for every step."""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import httpx

from ._errors import OverwingError
from ._http import AsyncHTTP, SyncHTTP, compact, query, segment
from ._types import TowerAction, TowerDecision


def _agent_key(agent_key: str | None) -> str:
    key = agent_key or os.environ.get("OVERWING_AGENT_KEY")
    if not key:
        raise OverwingError("Overwing Tower agent key missing. Pass agent_key= or set OVERWING_AGENT_KEY. An organization creates one with Overwing().tower_create_agent(name, scopes).")
    return key


def _submit_body(operation: str, input: dict[str, Any], idempotency_key: str, dry_run: bool) -> dict[str, Any]:
    if not idempotency_key:
        raise OverwingError("idempotency_key is required: use something stable for this business request, such as the source message id.")
    return compact({"operation": operation, "input": input, "idempotency_key": idempotency_key, "dry_run": True if dry_run else None})


class Tower(SyncHTTP):
    """Synchronous Tower client for one agent.

        tower = Tower()                                   # reads OVERWING_AGENT_KEY
        action = tower.submit("create_order", order, idempotency_key=message_id)
        if action.pending:
            action = tower.wait_for_review(action.action_id)
    """

    def __init__(self, agent_key: str | None = None, *, base_url: str | None = None, timeout: float = 15.0, max_retries: int = 2, transport: httpx.BaseTransport | None = None) -> None:
        super().__init__(_agent_key(agent_key), base_url=base_url, timeout=timeout, max_retries=max_retries, transport=transport)

    def __enter__(self) -> "Tower":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def capabilities(self) -> dict[str, Any]:
        """The operations this agent may call, each with the JSON Schema its input must match."""
        return self._request("GET", "/api/v1/tower/capabilities")

    def decide(self, operation: str, input: dict[str, Any]) -> TowerDecision:
        """How Tower would rule, with no side effects."""
        return TowerDecision.from_dict(self._request("POST", "/api/v1/tower/decide", json={"operation": operation, "input": input}))

    def submit(self, operation: str, input: dict[str, Any], *, idempotency_key: str, dry_run: bool = False) -> TowerAction:
        """Request an action. Returns for every ruling, so branch on `status` (or `.executed`, `.pending`, `.rejected`).

        Raises OverwingError for a malformed request, an operation outside the agent's scopes, or a spent quota.
        Repeating an idempotency_key returns the original outcome, marked `replayed`, instead of acting twice.
        """
        return TowerAction.from_dict(self._request("POST", "/api/v1/tower/actions", json=_submit_body(operation, input, idempotency_key, dry_run), accept=(422,)))

    def get(self, action_id: str) -> TowerAction:
        return TowerAction.from_dict(self._request("GET", f"/api/v1/tower/actions/{segment(action_id)}"))

    def wait_for_review(self, action_id: str, *, timeout: float = 600.0, interval: float = 5.0) -> TowerAction:
        """Poll an action that is waiting on a person until it leaves "pending" or the timeout passes. Returns the last state seen."""
        interval = max(1.0, interval)
        deadline = time.monotonic() + timeout
        while True:
            action = self.get(action_id)
            if not action.pending or time.monotonic() + interval > deadline:
                return action
            time.sleep(interval)

    def compensate(self, action_id: str) -> TowerAction:
        """Run the compensating operation for an executed action. Runs once; repeating it returns the first outcome."""
        return TowerAction.from_dict(self._request("POST", f"/api/v1/tower/actions/{segment(action_id)}/compensate", json={}))

    def get_receipt(self, id_or_sequence: str | int) -> dict[str, Any]:
        """One signed receipt: payload, hashes, signature."""
        return self._request("GET", f"/api/v1/tower/receipts/{segment(id_or_sequence)}")

    def verify_receipts(self, *, start: int | None = None, end: int | None = None) -> dict[str, Any]:
        """Recompute every hash and check every signature from sequence `start` to `end` (default: the whole chain, up to 5,000 links)."""
        return self._request("GET", "/api/v1/tower/receipts/verify" + query({"from": start, "to": end}))

    def public_key(self) -> dict[str, Any]:
        """The Ed25519 public key that signs receipts, for verifying them yourself."""
        return self._request("GET", "/api/v1/tower/receipts/public-key")


class AsyncTower(AsyncHTTP):
    """Asynchronous Tower client for one agent. See `Tower`."""

    def __init__(self, agent_key: str | None = None, *, base_url: str | None = None, timeout: float = 15.0, max_retries: int = 2, transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(_agent_key(agent_key), base_url=base_url, timeout=timeout, max_retries=max_retries, transport=transport)

    async def __aenter__(self) -> "AsyncTower":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def capabilities(self) -> dict[str, Any]:
        return await self._request("GET", "/api/v1/tower/capabilities")

    async def decide(self, operation: str, input: dict[str, Any]) -> TowerDecision:
        return TowerDecision.from_dict(await self._request("POST", "/api/v1/tower/decide", json={"operation": operation, "input": input}))

    async def submit(self, operation: str, input: dict[str, Any], *, idempotency_key: str, dry_run: bool = False) -> TowerAction:
        return TowerAction.from_dict(await self._request("POST", "/api/v1/tower/actions", json=_submit_body(operation, input, idempotency_key, dry_run), accept=(422,)))

    async def get(self, action_id: str) -> TowerAction:
        return TowerAction.from_dict(await self._request("GET", f"/api/v1/tower/actions/{segment(action_id)}"))

    async def wait_for_review(self, action_id: str, *, timeout: float = 600.0, interval: float = 5.0) -> TowerAction:
        interval = max(1.0, interval)
        deadline = time.monotonic() + timeout
        while True:
            action = await self.get(action_id)
            if not action.pending or time.monotonic() + interval > deadline:
                return action
            await asyncio.sleep(interval)

    async def compensate(self, action_id: str) -> TowerAction:
        return TowerAction.from_dict(await self._request("POST", f"/api/v1/tower/actions/{segment(action_id)}/compensate", json={}))

    async def get_receipt(self, id_or_sequence: str | int) -> dict[str, Any]:
        return await self._request("GET", f"/api/v1/tower/receipts/{segment(id_or_sequence)}")

    async def verify_receipts(self, *, start: int | None = None, end: int | None = None) -> dict[str, Any]:
        return await self._request("GET", "/api/v1/tower/receipts/verify" + query({"from": start, "to": end}))

    async def public_key(self) -> dict[str, Any]:
        return await self._request("GET", "/api/v1/tower/receipts/public-key")
