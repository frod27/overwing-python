import pytest

from overwing import AsyncBeacon, Beacon, OverwingError

ID = "bcn_0123456789abcdef"
REPORT = {"id": ID, "url": "https://example.com", "host": "example.com", "score": 25, "verdict": "no", "summary": "s", "categories": [{"key": "find", "answer": "no"}], "checks": [], "top_fixes": [{"check": "llms_txt", "fix": "Publish /llms.txt", "gain": 10}], "judged": True}


def test_start_sends_no_authorization_and_returns_where_to_pay(scripted, monkeypatch):
    monkeypatch.setenv("OVERWING_API_KEY", "ow_live_should_not_be_sent")
    s = scripted([(201, {"id": ID, "url": "https://example.com", "status": "awaiting_payment", "price_usd": 5, "checkout_url": "https://checkout.example/c"}, None)])
    with Beacon(base_url="https://example.test", transport=s.transport()) as beacon:
        check = beacon.start("example.com")
    assert check.awaiting_payment and not check.complete
    assert check.checkout_url == "https://checkout.example/c"
    req = s.calls[0]
    assert req.method == "POST" and req.url.path == "/api/v1/beacon/checks"
    assert "authorization" not in req.headers
    assert s.body() == {"url": "example.com"}


def test_unpaid_is_a_state_not_an_error(scripted):
    s = scripted([(402, {"id": ID, "url": "https://example.com", "status": "awaiting_payment", "checkout_url": "c", "error": "not paid"}, None)])
    check = Beacon(transport=s.transport()).report(ID)
    assert check.awaiting_payment and check.checkout_url == "c" and check.score is None


def test_wait_polls_until_complete(scripted, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    s = scripted([(402, {"id": ID, "status": "awaiting_payment", "checkout_url": "c"}, None), (202, {"id": ID, "status": "running"}, None), (200, {"status": "complete", **REPORT}, None)])
    check = Beacon(transport=s.transport()).wait_for_report(ID, interval=1.0)
    assert check.complete and check.score == 25 and check.verdict == "no"
    assert check.top_fixes[0]["check"] == "llms_txt"
    assert len(s.calls) == 3 and s.calls[2].url.path == f"/api/v1/beacon/checks/{ID}"


def test_sample_unknown_id_and_x402_address(scripted):
    s = scripted([(200, {"sample": True, **REPORT}, None), (404, {"error": "Check 'x' not found"}, None)])
    beacon = Beacon(base_url="https://example.test/", transport=s.transport())
    assert beacon.sample().complete
    with pytest.raises(OverwingError) as e:
        beacon.report("x")
    assert e.value.status == 404
    assert beacon.x402_url("https://example.com/a b") == "https://example.test/api/x402/beacon?url=https%3A%2F%2Fexample.com%2Fa%20b"


async def test_async_client(scripted):
    s = scripted([(200, {"status": "complete", **REPORT}, None)])
    async with AsyncBeacon(transport=s.transport()) as beacon:
        check = await beacon.report(ID)
    assert check.complete and check.score == 25
