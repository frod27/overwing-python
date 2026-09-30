import pytest

from overwing import AsyncOverwing, Overwing, OverwingError
from tests.conftest import fake_evaluation


def test_evaluates_with_no_key(monkeypatch, scripted):
    monkeypatch.delenv("OVERWING_API_KEY", raising=False)
    access = {"mode": "keyless", "daily_limit": 10, "remaining_today": 9, "input_stored": False}
    s = scripted([(200, {**fake_evaluation("pass"), "access": access}, None)])
    ow = Overwing(transport=s.transport())
    assert ow.keyless is True
    e = ow.evaluate("hello", rule_set="outbound-message", metadata={"a": 1}, context={"channel": "email"}, idempotency_key="k1")
    assert e.access == access
    req = s.calls[0]
    assert "authorization" not in req.headers and "idempotency-key" not in req.headers
    assert s.body() == {"input": "hello", "rule_set": "outbound-message", "context": {"channel": "email"}}


def test_no_key_everything_else_raises_before_a_request(monkeypatch, scripted):
    monkeypatch.delenv("OVERWING_API_KEY", raising=False)
    s = scripted([(200, {}, None)])
    ow = Overwing(transport=s.transport())
    for call in (ow.usage, ow.list_rule_sets, lambda: ow.get_evaluation("eval_1"), lambda: ow.evaluate_batch([{"input": "x"}])):
        with pytest.raises(OverwingError, match="signup"):
            call()
    assert s.calls == []
    assert Overwing("ow_live_test", transport=s.transport()).keyless is False


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


def test_store_false_is_sent_and_omitted_by_default(scripted):
    s = scripted([(200, fake_evaluation("pass"), None), (200, fake_evaluation("pass"), None)])
    with Overwing("ow_live_test", transport=s.transport()) as ow:
        ow.evaluate("hello", store=False)
        assert s.body()["store"] is False
        ow.evaluate("hello")
        assert "store" not in s.body(1)


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
