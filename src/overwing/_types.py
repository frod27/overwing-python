from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Verdict = Literal["pass", "fail", "review"]


@dataclass(frozen=True)
class RuleResult:
    rule: str
    type: Literal["choice", "score", "noul"]
    answer: str | float | bool
    probability: float
    confidence: float
    verdict: Verdict

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RuleResult":
        return cls(rule=d["rule"], type=d["type"], answer=d["answer"], probability=float(d["probability"]), confidence=float(d["confidence"]), verdict=d["verdict"])


@dataclass(frozen=True)
class Evaluation:
    id: str
    verdict: Verdict
    aggregate_score: float
    confidence: float
    latency_ms: int
    results: list[RuleResult] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

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
            results=[RuleResult.from_dict(r) for r in d.get("results", [])],
            raw=d,
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
