from __future__ import annotations

import os
from typing import Any

import httpx

from ._errors import OverwingError
from ._http import DEFAULT_BASE_URL, AsyncHTTP, SyncHTTP, compact as _compact, query, segment
from ._tower import AsyncTower, Tower
from ._types import AtlasLookup, BatchResult, Evaluation, TowerAgent

__all__ = ["DEFAULT_BASE_URL", "AsyncOverwing", "Overwing"]


def _api_key(api_key: str | None) -> str:
    key = api_key or os.environ.get("OVERWING_API_KEY")
    if not key:
        raise OverwingError("Overwing API key missing. Pass api_key= or set OVERWING_API_KEY. Get one at https://overwing.ai/login")
    return key


def _lookup_from(body: dict[str, Any], headers: dict[str, int | None]) -> AtlasLookup:
    return AtlasLookup.from_dict(body, limit=headers.get("limit"), remaining=headers.get("remaining"))


def _capture(into: dict[str, int | None]) -> Any:
    def hook(headers: httpx.Headers) -> None:
        lim, rem = headers.get("x-atlas-lookup-limit"), headers.get("x-atlas-lookup-remaining")
        into["limit"] = int(lim) if lim is not None else None
        into["remaining"] = int(rem) if rem is not None else None

    return hook


class Overwing(SyncHTTP):
    """Synchronous client for the Overwing API, as an organization."""

    def __init__(self, api_key: str | None = None, *, base_url: str | None = None, timeout: float = 15.0, max_retries: int = 2, transport: httpx.BaseTransport | None = None) -> None:
        super().__init__(_api_key(api_key), base_url=base_url, timeout=timeout, max_retries=max_retries, transport=transport)

    def __enter__(self) -> "Overwing":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

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

    # ---- Overwing Atlas (this key's allowance; for keyless use, construct Atlas()) ----

    def atlas_lookup(self, user_agent: str) -> AtlasLookup:
        """Say what a User-Agent string claims to be and whether the claim can be trusted."""
        seen: dict[str, int | None] = {}
        return _lookup_from(self._request("GET", "/api/v1/atlas/lookup" + query({"user_agent": user_agent}), on_headers=_capture(seen)), seen)

    # ---- Overwing Tower setup (agents operate through Tower(agent_key)) ----

    def tower_load_template(self) -> dict[str, Any]:
        """Load the starter workflow (email purchase order to order entry, mock IBM i). Idempotent."""
        return self._request("POST", "/api/v1/tower/template", json={})

    def tower_create_agent(self, name: str, scopes: list[str]) -> TowerAgent:
        """Create a scoped agent identity. `.key` is returned once: store it, then pass it to Tower(agent_key)."""
        return TowerAgent.from_dict(self._request("POST", "/api/v1/tower/agents", json={"name": name, "scopes": scopes}))

    def tower_list_agents(self) -> list[TowerAgent]:
        return [TowerAgent.from_dict(a) for a in self._request("GET", "/api/v1/tower/agents")["agents"]]

    def tower_revoke_agent(self, agent_id: str) -> None:
        """The agent's key stops working at once."""
        self._request("DELETE", f"/api/v1/tower/agents/{segment(agent_id)}")

    def tower_agent(self, name: str, scopes: list[str]) -> tuple[TowerAgent, Tower]:
        """Create an agent and return it with a ready Tower client."""
        agent = self.tower_create_agent(name, scopes)
        assert agent.key is not None
        return agent, Tower(agent.key, base_url=self.base_url, timeout=self._timeout, max_retries=self._max_retries, transport=self._transport)


class AsyncOverwing(AsyncHTTP):
    """Asynchronous client for the Overwing API, as an organization."""

    def __init__(self, api_key: str | None = None, *, base_url: str | None = None, timeout: float = 15.0, max_retries: int = 2, transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(_api_key(api_key), base_url=base_url, timeout=timeout, max_retries=max_retries, transport=transport)

    async def __aenter__(self) -> "AsyncOverwing":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

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

    async def atlas_lookup(self, user_agent: str) -> AtlasLookup:
        seen: dict[str, int | None] = {}
        return _lookup_from(await self._request("GET", "/api/v1/atlas/lookup" + query({"user_agent": user_agent}), on_headers=_capture(seen)), seen)

    async def tower_load_template(self) -> dict[str, Any]:
        return await self._request("POST", "/api/v1/tower/template", json={})

    async def tower_create_agent(self, name: str, scopes: list[str]) -> TowerAgent:
        return TowerAgent.from_dict(await self._request("POST", "/api/v1/tower/agents", json={"name": name, "scopes": scopes}))

    async def tower_list_agents(self) -> list[TowerAgent]:
        return [TowerAgent.from_dict(a) for a in (await self._request("GET", "/api/v1/tower/agents"))["agents"]]

    async def tower_revoke_agent(self, agent_id: str) -> None:
        await self._request("DELETE", f"/api/v1/tower/agents/{segment(agent_id)}")

    async def tower_agent(self, name: str, scopes: list[str]) -> tuple[TowerAgent, AsyncTower]:
        agent = await self.tower_create_agent(name, scopes)
        assert agent.key is not None
        return agent, AsyncTower(agent.key, base_url=self.base_url, timeout=self._timeout, max_retries=self._max_retries, transport=self._transport)
