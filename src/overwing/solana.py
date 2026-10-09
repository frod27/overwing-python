"""Overwing Preflight in the signing path: no allow, no signature.

    from overwing import Preflight
    from overwing.solana import guarded_sign, PreflightRefused

    preflight = Preflight()
    policy = {"wallet": str(keypair.pubkey()), "max_sol_out": 0.05}
    try:
        signed = guarded_sign(preflight, sign, tx, policy)   # sign(tx) runs only after an allow
    except PreflightRefused as e:
        e.verdict.reasons                                    # why; do not sign

Nothing here needs a Solana library. A transaction is base64 text, bytes, or any
object `bytes()` can serialize, which a solders transaction is. A private key is
never read, logged or sent: only the serialized transaction and the policy leave.

The default is to fail closed. A refusal raises `PreflightRefused`. No verdict at
all (the API unreachable, a timeout, a 5xx, an answer that cannot be read) raises
`OverwingError`. Pass `on_unavailable="allow"` to sign anyway when there is no
verdict; a refusal, a rejected request (400, 401) and a spent allowance (429)
still raise.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Awaitable, Callable, Literal, Mapping, TypeVar, Union

from ._errors import OverwingError, PreflightRefused
from ._preflight import Transaction, encode_transaction
from ._types import PreflightVerdict

__all__ = ["OnUnavailable", "PreflightRefused", "Transaction", "encode_transaction", "guarded_sign", "guarded_sign_async", "require_allow", "require_allow_async"]

OnUnavailable = Literal["refuse", "allow"]
T = TypeVar("T")
TX = TypeVar("TX")
VerdictHook = Callable[[Union[PreflightVerdict, None]], Any]

_log = logging.getLogger("overwing.solana")


def _checker(client: Any) -> Callable[..., Any]:
    """`Preflight.check`, or `Overwing.preflight_check`, sync or async."""
    fn = getattr(client, "preflight_check", None) or getattr(client, "check", None)
    if not callable(fn):
        raise TypeError("client must be a Preflight, AsyncPreflight, Overwing or AsyncOverwing")
    return fn


def _mode(on_unavailable: str) -> bool:
    if on_unavailable not in ("refuse", "allow"):
        raise ValueError('on_unavailable must be "refuse" or "allow"')
    return on_unavailable == "allow"


def _unavailable(e: OverwingError) -> bool:
    """No verdict because the service could not give one: unreachable, timed out, 5xx, or an answer that cannot be read."""
    return not isinstance(e, PreflightRefused) and (e.code in ("unreachable", "malformed_response") or e.status >= 500)


def _settle(verdict: Any) -> PreflightVerdict:
    if not isinstance(verdict, PreflightVerdict):
        raise OverwingError("Overwing Preflight gave no verdict. Do not sign.", code="malformed_response")
    if verdict.decision != "allow":
        raise PreflightRefused(verdict)
    return verdict


def _proceed_without_verdict(e: OverwingError) -> None:
    _log.warning("Overwing Preflight gave no verdict (%s); on_unavailable='allow', so the transaction goes ahead unchecked.", e)


def require_allow(client: Any, transaction: Transaction, policy: Mapping[str, Any], *, on_unavailable: OnUnavailable = "refuse") -> PreflightVerdict | None:
    """Check a transaction and return the verdict only when it is an allow.

    Raises `PreflightRefused` (with `.verdict`) on any other decision, and `OverwingError`
    when there is no verdict. With `on_unavailable="allow"`, no verdict returns None instead,
    and a warning is logged: that is the only case in which None comes back.
    """
    allow_unavailable = _mode(on_unavailable)
    check = _checker(client)
    try:
        return _settle(check(transaction, policy))
    except OverwingError as e:
        if allow_unavailable and _unavailable(e):
            _proceed_without_verdict(e)
            return None
        raise


async def require_allow_async(client: Any, transaction: Transaction, policy: Mapping[str, Any], *, on_unavailable: OnUnavailable = "refuse") -> PreflightVerdict | None:
    """`require_allow` for `AsyncPreflight` and `AsyncOverwing`."""
    allow_unavailable = _mode(on_unavailable)
    check = _checker(client)
    try:
        return _settle(await check(transaction, policy))
    except OverwingError as e:
        if allow_unavailable and _unavailable(e):
            _proceed_without_verdict(e)
            return None
        raise


def guarded_sign(client: Any, sign_fn: Callable[[TX], T], transaction: TX, policy: Mapping[str, Any], *, on_unavailable: OnUnavailable = "refuse", on_verdict: VerdictHook | None = None) -> T:
    """Check, then sign: `sign_fn(transaction)` is called only after an allow, with the very object that was checked.

    Returns what `sign_fn` returns. Raises exactly as `require_allow` does, and then `sign_fn`
    is never called. `on_verdict` receives the verdict before signing (None when there was none and
    `on_unavailable="allow"`): keep `verdict.id` to report the landed signature. Send at once:
    a verdict covers `valid_for_seconds`.
    """
    verdict = require_allow(client, transaction, policy, on_unavailable=on_unavailable)  # type: ignore[arg-type]
    if on_verdict is not None:
        on_verdict(verdict)
    return sign_fn(transaction)


async def guarded_sign_async(client: Any, sign_fn: Callable[[TX], Union[T, Awaitable[T]]], transaction: TX, policy: Mapping[str, Any], *, on_unavailable: OnUnavailable = "refuse", on_verdict: VerdictHook | None = None) -> T:
    """`guarded_sign` for the async clients. `sign_fn` may be a plain function or a coroutine function."""
    verdict = await require_allow_async(client, transaction, policy, on_unavailable=on_unavailable)  # type: ignore[arg-type]
    if on_verdict is not None:
        on_verdict(verdict)
    out = sign_fn(transaction)
    if inspect.isawaitable(out):
        return await out
    return out
