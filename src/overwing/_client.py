from __future__ import annotations

import os
from typing import Any, Mapping

import httpx

from ._errors import OverwingError
from ._http import DEFAULT_BASE_URL, AsyncHTTP, SyncHTTP, compact as _compact, query, segment
from . import _preflight as _pf
from ._tower import AsyncTower, Tower
from ._types import Account, AtlasLookup, BatchResult, DomainProof, Evaluation, PreflightRecord, PreflightReport, PreflightVerdict, TowerAgent

__all__ = ["DEFAULT_BASE_URL", "AsyncOverwing", "Overwing"]


def _api_key(api_key: str | None) -> str | None:
    """The key, or None: with no key, evaluate() uses the free allowance and everything else raises."""
    return api_key or os.environ.get("OVERWING_API_KEY") or None


def _require_key(token: str | None, method: str, path: str) -> None:
    # Only a single evaluation works without a key. Say so here, before a request that would come back 401.
    if token is None and not (method == "POST" and path == "/api/v1/evaluate"):
        raise OverwingError("Overwing API key missing. Without a key only evaluate() works (10 a day). Pass api_key= or set OVERWING_API_KEY. Overwing.signup() makes an account with no email.")


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

    @property
    def keyless(self) -> bool:
        """True when no key is configured. evaluate() then uses the free allowance; every other method raises."""
        return self._token is None

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        _require_key(self._token, method, path)
        return super()._request(method, path, **kwargs)

    @classmethod
    def signup(cls, org_name: str | None = None, *, base_url: str | None = None, timeout: float = 15.0, max_retries: int = 2, transport: httpx.BaseTransport | None = None) -> tuple[Account, "Overwing"]:
        """Create an account with no email, and a client that uses it. Nothing is sent to anyone.

        The key is the account: store `account.api_key` at once, since with no email there is no reset link.
        It starts at 50 evaluations a day; `prove_domain` raises that and makes the key recoverable.

            account, ow = Overwing.signup()
        """
        http = SyncHTTP(None, base_url=base_url, timeout=timeout, max_retries=max_retries, transport=transport)
        try:
            account = Account.from_dict(SyncHTTP._request(http, "POST", "/api/v1/signup", json={"org_name": org_name} if org_name else {}))
        finally:
            if transport is None:
                http.close()
        return account, cls(account.api_key, base_url=base_url, timeout=timeout, max_retries=max_retries, transport=transport)

    @classmethod
    def start_recovery(cls, domain: str, *, base_url: str | None = None, timeout: float = 15.0, max_retries: int = 2, transport: httpx.BaseTransport | None = None) -> DomainProof:
        """Key lost? Begin recovering an account made with no email, by the domain it proved. No key needed. Publish the value at the domain, then call `finish_recovery`."""
        http = SyncHTTP(None, base_url=base_url, timeout=timeout, max_retries=max_retries, transport=transport)
        try:
            return DomainProof.from_dict(SyncHTTP._request(http, "POST", "/api/v1/signup/recover", json={"domain": domain}))
        finally:
            if transport is None:
                http.close()

    @classmethod
    def finish_recovery(cls, domain: str, *, base_url: str | None = None, timeout: float = 15.0, max_retries: int = 2, transport: httpx.BaseTransport | None = None) -> tuple[Account, "Overwing"]:
        """Finish a recovery: every old key is revoked and one new key is returned, once. Raises OverwingError (status 422) while the proof is not at the domain."""
        http = SyncHTTP(None, base_url=base_url, timeout=timeout, max_retries=max_retries, transport=transport)
        try:
            account = Account.from_dict(SyncHTTP._request(http, "POST", "/api/v1/signup/recover/verify", json={"domain": domain}))
        finally:
            if transport is None:
                http.close()
        return account, cls(account.api_key, base_url=base_url, timeout=timeout, max_retries=max_retries, transport=transport)

    def prove_domain(self, domain: str) -> DomainProof:
        """Begin proving that the account controls a domain. Publish the value it returns there, then call `verify_domain`."""
        return DomainProof.from_dict(self._request("POST", "/api/v1/org/domain", json={"domain": domain}))

    def verify_domain(self) -> DomainProof:
        """Look for the proof. Not there yet: `verified` is False and `error` says what was looked for; asking again is safe."""
        return DomainProof.from_dict(self._request("POST", "/api/v1/org/domain/verify", accept=(422,)))

    def claim(self, email: str, password: str) -> dict[str, Any]:
        """A person takes charge of an account made with no email: attaches a login. They get a confirmation message."""
        return self._request("POST", "/api/v1/org/claim", json={"email": email, "password": password})

    def __enter__(self) -> "Overwing":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def evaluate(self, text: str, *, rule_set: str = "content-safety", metadata: dict[str, Any] | None = None, context: dict[str, Any] | None = None, store: bool | None = None, idempotency_key: str | None = None) -> Evaluation:
        """Score one text. `context` carries facts the rules may reference (recipient, channel, ownership). `store=False` runs the check without keeping the text or the context. Raises OverwingError on any non-2xx.

        With no key this uses the free allowance: 10 a day, inputs up to 2,000 characters, the prebuilt rule sets, and the
        text is not stored, so `metadata` and `idempotency_key` are not sent. `Evaluation.access` says what is left.
        """
        return Evaluation.from_dict(self._request("POST", "/api/v1/evaluate", json=_compact({"input": text, "rule_set": rule_set, "metadata": None if self.keyless else metadata, "context": context, "store": store}), idempotency_key=None if self.keyless else idempotency_key))

    def evaluate_batch(self, items: list[dict[str, Any]], *, rule_set: str = "content-safety", context: dict[str, Any] | None = None, store: bool | None = None, idempotency_key: str | None = None) -> BatchResult:
        """Score up to 50 texts. Each item: {"input": str, "id"?: str, "metadata"?: dict, "context"?: dict}."""
        return BatchResult.from_dict(self._request("POST", "/api/v1/evaluate/batch", json=_compact({"rule_set": rule_set, "items": items, "context": context, "store": store}), idempotency_key=idempotency_key, accept=(502,)))

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

    # ---- Overwing Preflight (should the agent sign this Solana transaction? see overwing.solana) ----

    def preflight_check(self, transaction: _pf.Transaction, policy: Mapping[str, Any]) -> PreflightVerdict:
        """Check one unsigned Solana transaction against a policy. Sign only when `verdict.allowed`. See `Preflight.check`."""
        return PreflightVerdict.from_dict(self._request("POST", _pf._CHECKS, json=_pf.check_body(transaction, policy)))

    def preflight_verdict(self, check_id: str) -> PreflightVerdict:
        """The public record of one verdict, with the transactions reported against it."""
        return PreflightVerdict.from_dict(self._request("GET", _pf.verdict_path(check_id)))

    def preflight_report(self, check_id: str, signature: str) -> PreflightReport:
        """Report the landed transaction (its base58 signature) against a verdict."""
        return PreflightReport.from_dict(self._request("POST", _pf.report_path(check_id), json={"signature": signature}))

    def preflight_record(self) -> PreflightRecord:
        """The public record: totals, misses, the latest verdicts and the reserve."""
        return PreflightRecord.from_dict(self._request("GET", _pf._RECORD))

    def preflight_overview(self) -> dict[str, Any]:
        """The policy fields, the reason codes, the prices and the endpoints."""
        return self._request("GET", _pf._OVERVIEW)

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

    @property
    def keyless(self) -> bool:
        """True when no key is configured. evaluate() then uses the free allowance; every other method raises."""
        return self._token is None

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        _require_key(self._token, method, path)
        return await super()._request(method, path, **kwargs)

    @classmethod
    async def signup(cls, org_name: str | None = None, *, base_url: str | None = None, timeout: float = 15.0, max_retries: int = 2, transport: httpx.AsyncBaseTransport | None = None) -> tuple[Account, "AsyncOverwing"]:
        """Create an account with no email, and a client that uses it. See `Overwing.signup`."""
        http = AsyncHTTP(None, base_url=base_url, timeout=timeout, max_retries=max_retries, transport=transport)
        try:
            account = Account.from_dict(await AsyncHTTP._request(http, "POST", "/api/v1/signup", json={"org_name": org_name} if org_name else {}))
        finally:
            if transport is None:
                await http.aclose()
        return account, cls(account.api_key, base_url=base_url, timeout=timeout, max_retries=max_retries, transport=transport)

    @classmethod
    async def start_recovery(cls, domain: str, *, base_url: str | None = None, timeout: float = 15.0, max_retries: int = 2, transport: httpx.AsyncBaseTransport | None = None) -> DomainProof:
        http = AsyncHTTP(None, base_url=base_url, timeout=timeout, max_retries=max_retries, transport=transport)
        try:
            return DomainProof.from_dict(await AsyncHTTP._request(http, "POST", "/api/v1/signup/recover", json={"domain": domain}))
        finally:
            if transport is None:
                await http.aclose()

    @classmethod
    async def finish_recovery(cls, domain: str, *, base_url: str | None = None, timeout: float = 15.0, max_retries: int = 2, transport: httpx.AsyncBaseTransport | None = None) -> tuple[Account, "AsyncOverwing"]:
        http = AsyncHTTP(None, base_url=base_url, timeout=timeout, max_retries=max_retries, transport=transport)
        try:
            account = Account.from_dict(await AsyncHTTP._request(http, "POST", "/api/v1/signup/recover/verify", json={"domain": domain}))
        finally:
            if transport is None:
                await http.aclose()
        return account, cls(account.api_key, base_url=base_url, timeout=timeout, max_retries=max_retries, transport=transport)

    async def prove_domain(self, domain: str) -> DomainProof:
        return DomainProof.from_dict(await self._request("POST", "/api/v1/org/domain", json={"domain": domain}))

    async def verify_domain(self) -> DomainProof:
        return DomainProof.from_dict(await self._request("POST", "/api/v1/org/domain/verify", accept=(422,)))

    async def claim(self, email: str, password: str) -> dict[str, Any]:
        return await self._request("POST", "/api/v1/org/claim", json={"email": email, "password": password})

    async def __aenter__(self) -> "AsyncOverwing":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def evaluate(self, text: str, *, rule_set: str = "content-safety", metadata: dict[str, Any] | None = None, context: dict[str, Any] | None = None, store: bool | None = None, idempotency_key: str | None = None) -> Evaluation:
        return Evaluation.from_dict(await self._request("POST", "/api/v1/evaluate", json=_compact({"input": text, "rule_set": rule_set, "metadata": None if self.keyless else metadata, "context": context, "store": store}), idempotency_key=None if self.keyless else idempotency_key))

    async def evaluate_batch(self, items: list[dict[str, Any]], *, rule_set: str = "content-safety", context: dict[str, Any] | None = None, store: bool | None = None, idempotency_key: str | None = None) -> BatchResult:
        return BatchResult.from_dict(await self._request("POST", "/api/v1/evaluate/batch", json=_compact({"rule_set": rule_set, "items": items, "context": context, "store": store}), idempotency_key=idempotency_key, accept=(502,)))

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

    # ---- Overwing Preflight ----

    async def preflight_check(self, transaction: _pf.Transaction, policy: Mapping[str, Any]) -> PreflightVerdict:
        return PreflightVerdict.from_dict(await self._request("POST", _pf._CHECKS, json=_pf.check_body(transaction, policy)))

    async def preflight_verdict(self, check_id: str) -> PreflightVerdict:
        return PreflightVerdict.from_dict(await self._request("GET", _pf.verdict_path(check_id)))

    async def preflight_report(self, check_id: str, signature: str) -> PreflightReport:
        return PreflightReport.from_dict(await self._request("POST", _pf.report_path(check_id), json={"signature": signature}))

    async def preflight_record(self) -> PreflightRecord:
        return PreflightRecord.from_dict(await self._request("GET", _pf._RECORD))

    async def preflight_overview(self) -> dict[str, Any]:
        return await self._request("GET", _pf._OVERVIEW)

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
