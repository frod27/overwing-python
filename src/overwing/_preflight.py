"""Overwing Preflight: should the agent sign this Solana transaction? One check against your policy, before the signature."""

from __future__ import annotations

import base64
import binascii
import os
from typing import Any, Mapping, Protocol, Union, runtime_checkable

import httpx

from ._errors import OverwingError
from ._http import AsyncHTTP, SyncHTTP, segment
from ._types import PreflightRecord, PreflightReport, PreflightVerdict

_OVERVIEW = "/api/v1/preflight"
_CHECKS = "/api/v1/preflight/checks"
_RECORD = "/api/v1/preflight/record"
_NO_KEY = "Overwing API key missing. A Preflight check needs one: pass api_key= or set OVERWING_API_KEY. A key is free: Overwing.signup(). Reading a verdict, the record and the overview needs none."


@runtime_checkable
class SupportsBytes(Protocol):
    def __bytes__(self) -> bytes: ...


#: A serialized Solana transaction: base64 text, raw bytes, or any object `bytes()` can serialize (a solders transaction).
Transaction = Union[str, bytes, bytearray, memoryview, SupportsBytes]


def _bad_transaction(why: str) -> OverwingError:
    return OverwingError(f"transaction {why}. Pass the serialized transaction as base64 text, as bytes, or as an object bytes() can serialize.", code="invalid_input", field="transaction", retryable=False)


def encode_transaction(transaction: Transaction) -> str:
    """The serialized transaction as base64, from base64 text, bytes, or an object with `__bytes__`. No Solana library needed."""
    if isinstance(transaction, str):
        try:
            raw = base64.b64decode("".join(transaction.split()), validate=True)
        except (binascii.Error, ValueError) as e:
            raise _bad_transaction("is a string that is not base64") from e
    elif isinstance(transaction, (bytes, bytearray, memoryview)):
        raw = bytes(transaction)
    elif hasattr(type(transaction), "__bytes__"):
        raw = bytes(transaction)
    else:
        raise _bad_transaction(f"is a {type(transaction).__name__}")
    if not raw:
        raise _bad_transaction("is empty")
    return base64.b64encode(raw).decode("ascii")


def check_body(transaction: Transaction, policy: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(policy, Mapping):
        raise OverwingError("policy must be a mapping: {\"wallet\": ..., \"max_sol_out\": ...}", code="invalid_input", field="policy", retryable=False)
    return {"transaction": encode_transaction(transaction), "policy": dict(policy)}


def report_path(check_id: str) -> str:
    return f"{_CHECKS}/{segment(check_id)}/reports"


def verdict_path(check_id: str) -> str:
    return f"{_CHECKS}/{segment(check_id)}"


class Preflight(SyncHTTP):
    """Synchronous Preflight client.

    A check reads one unsigned Solana transaction, simulates it against the chain as it
    is now, and answers allow or refuse against your policy. It never sees a private key.
    Sign only on an allow. Every other outcome, an error included, means do not sign:
    `overwing.solana.require_allow` and `guarded_sign` do that for you.

        preflight = Preflight()                      # OVERWING_API_KEY
        verdict = preflight.check(tx, {"wallet": wallet, "max_sol_out": 0.05})
        if verdict.allowed: ...                      # sign and send at once
    """

    def __init__(self, api_key: str | None = None, *, base_url: str | None = None, timeout: float = 15.0, max_retries: int = 2, transport: httpx.BaseTransport | None = None) -> None:
        super().__init__(api_key or os.environ.get("OVERWING_API_KEY") or None, base_url=base_url, timeout=timeout, max_retries=max_retries, transport=transport)

    def __enter__(self) -> "Preflight":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def check(self, transaction: Transaction, policy: Mapping[str, Any]) -> PreflightVerdict:
        """Check one transaction against a policy. Returns for allow and for refuse: branch on `verdict.allowed`.

        `policy` needs `wallet` and `max_sol_out` (or `max_sol_out_lamports`); `max_token_out`, `min_token_in`,
        `allowed_programs` and `allow_delegation` are optional. Raises OverwingError when there is no verdict.
        """
        body = check_body(transaction, policy)
        if self._token is None:
            raise OverwingError(_NO_KEY)
        return PreflightVerdict.from_dict(self._request("POST", _CHECKS, json=body))

    def verdict(self, check_id: str) -> PreflightVerdict:
        """The public record of one verdict, with the transactions reported against it. No key needed."""
        return PreflightVerdict.from_dict(self._request("GET", verdict_path(check_id)))

    def report(self, check_id: str, signature: str) -> PreflightReport:
        """Report the landed transaction (its base58 signature) against a verdict. The answer is read from the chain. No key needed."""
        return PreflightReport.from_dict(self._request("POST", report_path(check_id), json={"signature": signature}))

    def record(self) -> PreflightRecord:
        """The public record: totals, misses, the latest verdicts and the reserve. No key needed."""
        return PreflightRecord.from_dict(self._request("GET", _RECORD))

    def overview(self) -> dict[str, Any]:
        """What is checked, the policy fields, the reason codes, the prices and the endpoints. No key needed."""
        return self._request("GET", _OVERVIEW)

    def x402_url(self) -> str:
        """The address an x402 client pays $0.01 at for one check, with the same body and answer and no account."""
        return f"{self.base_url}/api/x402/preflight"


class AsyncPreflight(AsyncHTTP):
    """Asynchronous Preflight client. See `Preflight`."""

    def __init__(self, api_key: str | None = None, *, base_url: str | None = None, timeout: float = 15.0, max_retries: int = 2, transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(api_key or os.environ.get("OVERWING_API_KEY") or None, base_url=base_url, timeout=timeout, max_retries=max_retries, transport=transport)

    async def __aenter__(self) -> "AsyncPreflight":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def check(self, transaction: Transaction, policy: Mapping[str, Any]) -> PreflightVerdict:
        body = check_body(transaction, policy)
        if self._token is None:
            raise OverwingError(_NO_KEY)
        return PreflightVerdict.from_dict(await self._request("POST", _CHECKS, json=body))

    async def verdict(self, check_id: str) -> PreflightVerdict:
        return PreflightVerdict.from_dict(await self._request("GET", verdict_path(check_id)))

    async def report(self, check_id: str, signature: str) -> PreflightReport:
        return PreflightReport.from_dict(await self._request("POST", report_path(check_id), json={"signature": signature}))

    async def record(self) -> PreflightRecord:
        return PreflightRecord.from_dict(await self._request("GET", _RECORD))

    async def overview(self) -> dict[str, Any]:
        return await self._request("GET", _OVERVIEW)

    def x402_url(self) -> str:
        return f"{self.base_url}/api/x402/preflight"
