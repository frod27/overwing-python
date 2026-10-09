from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from ._errors import OverwingError

Verdict = Literal["pass", "fail", "review"]
RuleAction = Literal["block", "redact", "review"]
RecommendedAction = Literal["block", "redact", "review", "allow"]


@dataclass(frozen=True)
class RuleResult:
    rule: str
    type: Literal["choice", "score", "noul"]
    answer: str | float | bool
    probability: float
    confidence: float
    verdict: Verdict
    action: RuleAction = "block"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RuleResult":
        return cls(rule=d["rule"], type=d["type"], answer=d["answer"], probability=float(d["probability"]), confidence=float(d["confidence"]), verdict=d["verdict"], action=d.get("action", "block"))


@dataclass(frozen=True)
class Evaluation:
    id: str
    verdict: Verdict
    aggregate_score: float
    confidence: float
    latency_ms: int
    recommended_action: RecommendedAction
    results: list[RuleResult] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    #: Present only on an evaluation made with no key: what is left of the free allowance, and that the text was not stored.
    access: dict[str, Any] | None = None

    @property
    def failed_rules(self) -> list[str]:
        return [r.rule for r in self.results if r.verdict == "fail"]

    @property
    def review_rules(self) -> list[str]:
        return [r.rule for r in self.results if r.verdict == "review"]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Evaluation":
        return cls(
            id=d["id"],
            verdict=d["verdict"],
            aggregate_score=float(d["aggregate_score"]),
            confidence=float(d["confidence"]),
            latency_ms=int(d["latency_ms"]),
            recommended_action=d.get("recommended_action", "block" if d["verdict"] == "fail" else "review" if d["verdict"] == "review" else "allow"),
            results=[RuleResult.from_dict(r) for r in d.get("results", [])],
            raw=d,
            access=d.get("access"),
        )


@dataclass(frozen=True)
class BatchItemResult:
    id: str | None
    index: int
    evaluation: Evaluation | None
    error: str | None


@dataclass(frozen=True)
class BatchResult:
    total: int
    passed: int
    failed: int
    review: int
    errors: int
    results: list[BatchItemResult]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "BatchResult":
        s = d["summary"]
        return cls(
            total=s["total"], passed=s["pass"], failed=s["fail"], review=s["review"], errors=s["errors"],
            results=[BatchItemResult(id=r.get("id"), index=r["index"], evaluation=Evaluation.from_dict(r["evaluation"]) if r.get("evaluation") else None, error=r.get("error")) for r in d.get("results", [])],
        )


# ---- Overwing Atlas ----


@dataclass(frozen=True)
class AtlasLookup:
    """What a User-Agent string claims to be, and whether the claim can be trusted."""

    user_agent: str
    identified: bool
    agent: str | None
    operator: str | None
    purpose_class: str | None
    #: "Web Bot Auth signature", "User-agent string only (spoofable)", "Unattributable / spoofed", ...
    verification: str | None
    trust_note: str
    #: Lookups left today, from the response headers. None when the server did not say.
    remaining_today: int | None = None
    daily_limit: int | None = None
    #: True when the call used the keyless allowance.
    keyless: bool = False
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def signed(self) -> bool:
        """True when the operator signs its requests, so the claim can be verified rather than taken on trust."""
        return self.verification == "Web Bot Auth signature"

    @property
    def matches(self) -> list[dict[str, Any]]:
        return list(self.raw.get("matches", []))

    @classmethod
    def from_dict(cls, d: dict[str, Any], *, limit: int | None = None, remaining: int | None = None) -> "AtlasLookup":
        claims = d.get("claims") or {}
        access = d.get("access") or {}
        return cls(
            user_agent=d.get("user_agent", ""),
            identified=bool(d.get("identified")),
            agent=claims.get("agent"),
            operator=claims.get("operator"),
            purpose_class=claims.get("purpose_class"),
            verification=claims.get("verification"),
            trust_note=d.get("trust_note", ""),
            remaining_today=remaining if remaining is not None else access.get("remaining_today"),
            daily_limit=limit if limit is not None else access.get("daily_limit"),
            keyless=access.get("mode") == "keyless",
            raw=d,
        )


# ---- Overwing Tower ----

TowerOutcome = Literal["auto", "review", "reject"]
TowerActionStatus = Literal["pending", "approved", "executed", "failed", "compensated", "rejected"]


@dataclass(frozen=True)
class TowerDecision:
    """How Tower ruled, and why."""

    decision_id: str
    outcome: TowerOutcome
    score: float | None
    reason: str
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def results(self) -> list[dict[str, Any]]:
        """Each policy question's answer, probability, confidence and whether it passed."""
        return list(self.raw.get("results", []))

    @property
    def failed_checks(self) -> list[dict[str, Any]]:
        return [c for c in self.raw.get("checks", []) if not c.get("passed")]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TowerDecision":
        score = d.get("score")
        return cls(decision_id=d.get("decision_id", ""), outcome=d["outcome"], score=float(score) if score is not None else None, reason=d.get("reason", ""), raw=d)


@dataclass(frozen=True)
class TowerAction:
    """One requested action. Branch on `status`."""

    action_id: str
    operation: str
    status: TowerActionStatus
    dry_run: bool
    #: The full decision on submit. None when the API returned only its id.
    decision: TowerDecision | None
    #: What the legacy system returned.
    result: dict[str, Any] | None
    #: Set when the action is waiting on a person.
    review_id: str | None = None
    #: True when this is the stored outcome of an earlier request with the same idempotency key.
    replayed: bool = False
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def executed(self) -> bool:
        return self.status == "executed"

    @property
    def pending(self) -> bool:
        """A person must approve. Poll with `get` or `wait_for_review`; do not resubmit."""
        return self.status == "pending"

    @property
    def rejected(self) -> bool:
        """Do not retry unchanged. `decision.reason` says why."""
        return self.status == "rejected"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TowerAction":
        dec = d.get("decision")
        return cls(
            action_id=d["action_id"],
            operation=d.get("operation", ""),
            status=d["status"],
            dry_run=bool(d.get("dry_run")),
            decision=TowerDecision.from_dict(dec) if isinstance(dec, dict) and "outcome" in dec else None,
            result=d.get("result"),
            review_id=d.get("review_id"),
            replayed=bool(d.get("replayed")),
            raw=d,
        )


@dataclass(frozen=True)
class TowerAgent:
    """An agent identity. `key` is present only on the response that created it."""

    agent_id: str
    name: str
    scopes: list[str]
    status: Literal["active", "revoked"]
    key: str | None = field(default=None, repr=False)
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TowerAgent":
        return cls(agent_id=d["agent_id"], name=d.get("name", ""), scopes=list(d.get("scopes", [])), status=d.get("status", "active"), key=d.get("key"), raw=d)


@dataclass(frozen=True)
class Account:
    """An account made with no email, or a key recovered for one. `api_key` is shown once: store it."""

    org_id: str
    api_key: str = field(repr=False)
    #: Evaluations a day. 50 until a domain is proved.
    daily_limit: int | None = None
    #: Keys that stopped working, after a recovery.
    revoked_keys: int | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Account":
        return cls(org_id=d["org_id"], api_key=d["api_key"], daily_limit=d.get("daily_limit"), revoked_keys=d.get("revoked_keys"), raw=d)


@dataclass(frozen=True)
class DomainProof:
    """A domain proof for an account. While it is pending, `dns_*` and `file_*` hold the one value to publish at the domain."""

    domain: str | None
    #: "pending_verification" or "verified".
    status: str
    dns_name: str | None = None
    dns_value: str | None = None
    file_url: str | None = None
    file_body: str | None = None
    #: Set when the proof was looked for and not found: what was looked for.
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def verified(self) -> bool:
        return self.status == "verified"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DomainProof":
        v = d.get("verification") or {}
        dns, http = v.get("dns") or {}, v.get("http") or {}
        return cls(domain=d.get("domain"), status=d.get("status") or "pending_verification", dns_name=dns.get("name"), dns_value=dns.get("value"), file_url=http.get("url"), file_body=http.get("body"), error=d.get("error"), raw=d)


@dataclass(frozen=True)
class AtlasRegistration:
    """An agent you registered in Atlas. While it is unverified, `dns_*` and `file_*` hold the one value to publish at the domain."""

    id: str
    #: "pending_verification", "pending_review", "published", "rejected" or "withdrawn".
    status: str
    name: str = ""
    operator: str = ""
    domain: str = ""
    tokens: list[str] = field(default_factory=list)
    #: Why it is waiting for a person, or why it was not accepted.
    note: str | None = None
    #: The TXT record to publish: its name and value.
    dns_name: str | None = None
    dns_value: str | None = None
    #: Or the file to serve: its address and content.
    file_url: str | None = None
    file_body: str | None = None
    #: Once published: the registry entry.
    agent: str | None = None
    #: Set by `verify_registration` when the proof was not found: what was looked for.
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def published(self) -> bool:
        return self.status == "published"

    @property
    def pending_verification(self) -> bool:
        """The proof has not been found at the domain yet."""
        return self.status == "pending_verification"

    @classmethod
    def from_dict(cls, d: dict[str, Any], *, error: str | None = None) -> "AtlasRegistration":
        v = d.get("verification") or {}
        dns, http = v.get("dns") or {}, v.get("http") or {}
        return cls(
            id=d["id"],
            status=d.get("status", ""),
            name=d.get("name", ""),
            operator=d.get("operator", ""),
            domain=d.get("domain", ""),
            tokens=list(d.get("tokens") or []),
            note=d.get("note"),
            dns_name=dns.get("name"),
            dns_value=dns.get("value"),
            file_url=http.get("url"),
            file_body=http.get("body"),
            agent=d.get("agent"),
            error=error,
            raw=d,
        )


BeaconStatus = Literal["queued", "running", "complete"]


@dataclass(frozen=True)
class BeaconCheck:
    """One Beacon check: is a site reachable by agents? Branch on `status`; the report fields are set once it is complete."""

    id: str
    url: str
    status: BeaconStatus
    #: "full" or "summary". The summary (no key) has no `checks` and only the first of `top_fixes`.
    access: str | None = None
    #: In a summary: how many checks the full report holds, {"checks", "pass", "gaps", "missing", "fixes"}.
    counts: dict[str, int] | None = None
    #: 0 to 100.
    score: int | None = None
    #: "yes", "partly" or "no": is the product reachable by agents.
    verdict: str | None = None
    summary: str | None = None
    #: find, read, use: each {"key", "title", "question", "answer", "points", "max"}.
    categories: list[dict[str, Any]] = field(default_factory=list)
    #: Each {"id", "category", "title", "status", "points", "max", "detail", "fix"?, "evidence"?}. Empty in a summary.
    checks: list[dict[str, Any]] = field(default_factory=list)
    #: The three changes worth the most: each {"check", "fix", "gain"}. A summary has the first.
    top_fixes: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def complete(self) -> bool:
        return self.status == "complete"

    @property
    def full(self) -> bool:
        """The full report, not the summary. False until the check is complete."""
        return self.complete and self.access != "summary"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "BeaconCheck":
        return cls(
            id=d["id"],
            url=d.get("url", ""),
            status=d.get("status", "complete"),
            access=d.get("access"),
            counts=d.get("counts"),
            score=d.get("score"),
            verdict=d.get("verdict"),
            summary=d.get("summary"),
            categories=list(d.get("categories") or []),
            checks=list(d.get("checks") or []),
            top_fixes=list(d.get("top_fixes") or []),
            raw=d,
        )


def _malformed(what: str, d: Any) -> OverwingError:
    return OverwingError(f"Overwing Preflight answered with {what}. Treat it as no verdict: do not sign.", code="malformed_response", body=d)


@dataclass(frozen=True)
class PreflightVerdict:
    """One Preflight verdict on one Solana transaction. Sign only when `allowed`; anything else means do not sign."""

    id: str
    #: "allow" or "refuse". Any other value is not an allow.
    decision: str
    #: Why it was refused: each {"code", "detail"}. Empty on an allow.
    reasons: list[dict[str, Any]] = field(default_factory=list)
    #: What the simulation says would leave and arrive: {"sol_out_lamports", "token_out", "token_in", "control"}.
    effects: dict[str, Any] | None = None
    #: Every program the transaction runs, top-level or inner.
    programs: list[str] = field(default_factory=list)
    #: Hex digest of the transaction that was checked.
    digest: str | None = None
    slot: int | None = None
    #: Whether an allow is inside the guarantee (every program it runs is a covered one).
    covered: bool = False
    decided_at: str | None = None
    #: How long the verdict describes the chain for. Sign and send at once, or check again.
    valid_for_seconds: int | None = None
    #: The signed receipt: {"payload", "payload_hash", "signature", "signing_key_id", "public_key"}.
    receipt: dict[str, Any] | None = None
    record_url: str | None = None
    if_it_goes_wrong: str | None = None
    #: On a verdict read from the public record: the transactions reported against it.
    reports: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"

    @property
    def refused(self) -> bool:
        """True for "refuse" and for any decision that is not "allow"."""
        return not self.allowed

    @property
    def reason_codes(self) -> list[str]:
        return [str(r["code"]) for r in self.reasons if isinstance(r, dict) and r.get("code")]

    @classmethod
    def from_dict(cls, d: Any) -> "PreflightVerdict":
        """Raises OverwingError when the answer has no id or no decision, so a malformed answer is never read as an allow."""
        if not isinstance(d, dict):
            raise _malformed("something that is not an object", d)
        if not isinstance(d.get("id"), str) or not d["id"]:
            raise _malformed("no verdict id", d)
        if not isinstance(d.get("decision"), str) or not d["decision"]:
            raise _malformed("no decision", d)
        reasons = d.get("reasons")
        if not isinstance(reasons, list):
            # The public record carries only the codes.
            codes = d.get("reason_codes")
            reasons = [{"code": c, "detail": None} for c in codes] if isinstance(codes, list) else []
        vfs, slot = d.get("valid_for_seconds"), d.get("slot")
        return cls(
            id=d["id"],
            decision=d["decision"],
            reasons=[r for r in reasons if isinstance(r, dict)],
            effects=d.get("effects") if isinstance(d.get("effects"), dict) else None,
            programs=[p for p in (d.get("programs") or []) if isinstance(p, str)] if isinstance(d.get("programs"), list) else [],
            digest=d.get("digest"),
            slot=slot if isinstance(slot, int) else None,
            covered=d.get("covered") is True,
            decided_at=d.get("decided_at"),
            valid_for_seconds=vfs if isinstance(vfs, int) else None,
            receipt=d.get("receipt") if isinstance(d.get("receipt"), dict) else None,
            record_url=d.get("record_url"),
            if_it_goes_wrong=d.get("if_it_goes_wrong"),
            reports=[r for r in d["reports"] if isinstance(r, dict)] if isinstance(d.get("reports"), list) else [],
            raw=d,
        )


@dataclass(frozen=True)
class PreflightReport:
    """What the chain showed for a landed transaction reported against a verdict."""

    check_id: str
    #: The landed transaction's signature.
    transaction: str
    #: "miss" (it took more than the policy permits after an allow) or "not_a_miss".
    outcome: str
    why: str | None = None
    covered: bool = False
    payout_usd: float | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def miss(self) -> bool:
        return self.outcome == "miss"

    @classmethod
    def from_dict(cls, d: Any) -> "PreflightReport":
        if not isinstance(d, dict) or not isinstance(d.get("outcome"), str):
            raise _malformed("no report outcome", d)
        return cls(check_id=str(d.get("check_id") or ""), transaction=str(d.get("transaction") or ""), outcome=d["outcome"], why=d.get("why"), covered=d.get("covered") is True, payout_usd=d.get("payout_usd"), raw=d)


@dataclass(frozen=True)
class PreflightRecord:
    """The public record: every verdict and every miss. Never the wallet, the amounts or the transaction."""

    #: {"checks", "allowed", "refused", "covered_allows", "reports", "misses", "covered_misses", "paid_usd"}.
    totals: dict[str, Any] = field(default_factory=dict)
    misses: list[dict[str, Any]] = field(default_factory=list)
    #: The latest verdicts, newest first, as the record publishes them (codes, digest and signature).
    recent: list[PreflightVerdict] = field(default_factory=list)
    #: Whether the guarantee is active, the reserve, and the limits.
    guarantee: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, d: Any) -> "PreflightRecord":
        if not isinstance(d, dict):
            raise _malformed("a record that is not an object", d)
        return cls(totals=dict(d.get("totals") or {}), misses=list(d.get("misses") or []), recent=[PreflightVerdict.from_dict(v) for v in d.get("recent") or []], guarantee=dict(d.get("guarantee") or {}), raw=d)
