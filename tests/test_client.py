import pytest

from overwing import AsyncOverwing, Overwing, OverwingError
from tests.conftest import fake_evaluation


def test_requires_key(monkeypatch):
    monkeypatch.delenv("OVERWING_API_KEY", raising=False)
    with pytest.raises(OverwingError):
        Overwing()


def test_evaluate_sends_request(scripted):
    s = scripted([(200, fake_evaluation("pass"), None)])
    with Overwing("ow_live_test", base_url="https://example.test/", transport=s.transport()) as ow:
        e = ow.evaluate("hello", metadata={"a": 1}, idempotency_key="k1")
    assert e.verdict == "pass" and e.failed_rules == []
    req = s.calls[0]
    assert str(req.url) == "https://example.test/api/v1/evaluate"
    assert req.headers["authorization"] == "Bearer ow_live_test"
    assert req.headers["idempotency-key"] == "k1"
    assert s.body() == {"input": "hello", "rule_set": "content-safety", "metadata": {"a": 1}}


def test_retries_short_429_then_succeeds(scripted):
    s = scripted([(429, {"error": "slow"}, {"retry-after": "0"}), (200, fake_evaluation("fail"), None)])
    ow = Overwing("k", transport=s.transport())
    e = ow.evaluate("x")
    assert e.verdict == "fail" and e.failed_rules == ["toxicity"]
    assert len(s.calls) == 2


def test_error_carries_status_and_retry_after(scripted):
    s = scripted([(429, {"error": "Daily limit"}, {"retry-after": "3600"})])
    ow = Overwing("k", transport=s.transport())
    with pytest.raises(OverwingError) as ei:
        ow.evaluate("x")
    assert ei.value.status == 429 and ei.value.retry_after_seconds == 3600
    assert len(s.calls) == 1


def test_batch_accepts_502(scripted):
    s = scripted([(502, {"summary": {"total": 1, "pass": 0, "fail": 0, "review": 0, "errors": 1}, "results": [{"id": None, "index": 0, "evaluation": None, "error": "engine"}]}, None)])
    b = Overwing("k", transport=s.transport(), max_retries=0).evaluate_batch([{"input": "x"}])
    assert b.errors == 1 and b.results[0].evaluation is None


async def test_async_client(scripted):
    s = scripted([(200, fake_evaluation("review"), None)])
    async with AsyncOverwing("k", transport=s.transport()) as ow:
        e = await ow.evaluate("x")
    assert e.verdict == "review" and e.review_rules == ["pii_detected"]
