import json
from typing import Any

import httpx
import pytest


def fake_evaluation(verdict: str, eid: str = "eval_0000000000000001", recommended: str | None = None) -> dict[str, Any]:
    return {
        "id": eid,
        "verdict": verdict,
        "recommended_action": recommended or ("block" if verdict == "fail" else "review" if verdict == "review" else "allow"),
        "aggregate_score": 1.0 if verdict == "pass" else 0.5,
        "confidence": 0.9,
        "latency_ms": 42,
        "results": [
            {"rule": "toxicity", "type": "choice", "answer": "toxic" if verdict == "fail" else "safe", "probability": 0.9, "confidence": 0.9, "verdict": "fail" if verdict == "fail" else "pass", "action": "block"},
            {"rule": "pii_detected", "type": "noul", "answer": False, "probability": 0.9, "confidence": 0.6 if verdict == "review" else 0.9, "verdict": "review" if verdict == "review" else "pass", "action": "redact"},
        ],
    }


class Scripted:
    """Records requests and answers with a scripted sequence."""

    def __init__(self, responses: list[tuple[int, Any, dict[str, str] | None]]) -> None:
        self.responses = responses
        self.calls: list[httpx.Request] = []
        self.i = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        status, body, headers = self.responses[min(self.i, len(self.responses) - 1)]
        self.i += 1
        return httpx.Response(status, json=body, headers=headers or {})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def body(self, n: int = 0) -> dict[str, Any]:
        return json.loads(self.calls[n].content)


@pytest.fixture
def scripted():
    return Scripted
