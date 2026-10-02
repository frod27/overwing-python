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


STEPS = {"value": "overwing-atlas-verification=tok", "dns": {"type": "TXT", "name": "_overwing-atlas.acme.com", "value": "overwing-atlas-verification=tok"}, "http": {"url": "https://acme.com/.well-known/overwing-atlas.txt", "body": "overwing-atlas-verification=tok"}}


def test_signup_with_no_email_sends_no_key_and_returns_a_client_on_the_new_one(monkeypatch, scripted):
    monkeypatch.setenv("OVERWING_API_KEY", "ow_live_should_not_be_sent")
    s = scripted([(201, {"org_id": "o1", "account": "key_only", "daily_limit": 50, "api_key": "ow_live_new"}, None), (200, {"org_id": "o1", "account": "key_only", "verified_domain": None}, None)])
    account, ow = Overwing.signup(base_url="https://example.test", transport=s.transport())
    assert account.org_id == "o1" and account.daily_limit == 50 and "ow_live_new" not in repr(account)
    req = s.calls[0]
    assert req.method == "POST" and req.url.path == "/api/v1/signup" and "authorization" not in req.headers
    assert s.body() == {}
    assert not ow.keyless and ow.me()["account"] == "key_only"
    assert s.calls[1].headers["authorization"] == "Bearer ow_live_new"


def test_prove_domain_and_a_proof_not_there_yet_is_a_state(scripted):
    s = scripted([(200, {"domain": "acme.com", "status": "pending_verification", "verification": STEPS}, None), (422, {"error": "No proof found.", "verification": STEPS}, None), (200, {"domain": "acme.com", "status": "verified"}, None)])
    ow = Overwing("ow_live_test", transport=s.transport())
    proof = ow.prove_domain("acme.com")
    assert not proof.verified and proof.dns_name == "_overwing-atlas.acme.com" and proof.file_url.endswith("/.well-known/overwing-atlas.txt")
    assert s.body() == {"domain": "acme.com"}
    waiting = ow.verify_domain()
    assert not waiting.verified and waiting.error == "No proof found." and waiting.dns_value == "overwing-atlas-verification=tok"
    assert ow.verify_domain().verified
    assert s.calls[2].url.path == "/api/v1/org/domain/verify"


def test_recovery_needs_no_key_and_returns_a_client_on_the_new_one(monkeypatch, scripted):
    monkeypatch.delenv("OVERWING_API_KEY", raising=False)
    s = scripted([(200, {"domain": "acme.com", "verification": STEPS, "expires_in_hours": 24}, None), (422, {"error": "No proof found for a recovery in progress on this domain."}, None), (200, {"org_id": "o1", "domain": "acme.com", "api_key": "ow_live_again", "revoked_keys": 1}, None)])
    started = Overwing.start_recovery("acme.com", transport=s.transport())
    assert started.dns_name == "_overwing-atlas.acme.com"
    with pytest.raises(OverwingError) as e:
        Overwing.finish_recovery("acme.com", transport=s.transport(), max_retries=0)
    assert e.value.status == 422
    account, ow = Overwing.finish_recovery("acme.com", transport=s.transport())
    assert account.revoked_keys == 1 and not ow.keyless
    assert all("authorization" not in c.headers for c in s.calls)


async def test_async_signup(scripted):
    s = scripted([(201, {"org_id": "o1", "daily_limit": 50, "api_key": "ow_live_new"}, None), (200, {"domain": "acme.com", "status": "pending_verification", "verification": STEPS}, None)])
    account, ow = await AsyncOverwing.signup("Acme agent", transport=s.transport())
    assert account.api_key == "ow_live_new" and s.body() == {"org_name": "Acme agent"}
    proof = await ow.prove_domain("acme.com")
    assert proof.dns_value == "overwing-atlas-verification=tok"
    await ow.aclose()
