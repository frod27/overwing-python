import base64

import httpx
import pytest

from overwing import AsyncOverwing, AsyncPreflight, Overwing, OverwingError, Preflight, PreflightRefused
from overwing.solana import encode_transaction, guarded_sign, guarded_sign_async, require_allow, require_allow_async

ID = "pfc_0123456789abcdef"
WALLET = "9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin"
SYSTEM = "11111111111111111111111111111111"
POLICY = {"wallet": WALLET, "max_sol_out": 0.05}
TX_BYTES = bytes(range(256)) * 2
TX_B64 = base64.b64encode(TX_BYTES).decode()


def verdict(decision="allow", **extra):
    reasons = [] if decision == "allow" else [{"code": "sol_out_exceeds_limit", "detail": "1.2 SOL would leave; the policy allows 0.05"}]
    return {
        "id": ID,
        "decision": decision,
        "reasons": reasons,
        "effects": {"sol_out_lamports": "1005000", "token_out": {}, "token_in": {}, "control": []},
        "programs": [SYSTEM],
        "digest": "8e49",
        "slot": 454679568,
        "covered": decision == "allow",
        "decided_at": "2026-10-08T22:33:53.770Z",
        "valid_for_seconds": 120,
        "receipt": {"payload": {}, "payload_hash": "aff3", "signature": "sig", "signing_key_id": "b71e", "public_key": "https://overwing.ai/api/v1/tower/receipts/public-key"},
        "record_url": f"https://overwing.ai/api/v1/preflight/checks/{ID}",
        "if_it_goes_wrong": "Report the landed signature.",
        **extra,
    }


class SoldersLike:
    """Stands in for a solders transaction: bytes(tx) is its wire form."""

    def __bytes__(self) -> bytes:
        return TX_BYTES


class Signer:
    def __init__(self) -> None:
        self.signed: list[object] = []

    def __call__(self, tx):
        self.signed.append(tx)
        return "signed"


def unreachable(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectTimeout("timed out", request=request)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)


# ---- the client ----


def test_check_sends_the_key_the_transaction_and_the_policy(scripted):
    s = scripted([(200, verdict(), None)])
    with Preflight("ow_live_test", base_url="https://example.test", transport=s.transport()) as preflight:
        v = preflight.check(TX_B64, POLICY)
    assert v.allowed and not v.refused and v.id == ID and v.reasons == [] and v.reason_codes == []
    assert v.covered and v.valid_for_seconds == 120 and v.programs == [SYSTEM] and v.effects is not None and v.effects["sol_out_lamports"] == "1005000"
    assert v.receipt is not None and v.receipt["signing_key_id"] == "b71e"
    req = s.calls[0]
    assert req.method == "POST" and str(req.url) == "https://example.test/api/v1/preflight/checks"
    assert req.headers["authorization"] == "Bearer ow_live_test"
    assert s.body() == {"transaction": TX_B64, "policy": POLICY}


def test_a_refusal_is_returned_by_check_with_its_reasons(scripted):
    s = scripted([(200, verdict("refuse"), None)])
    v = Preflight("k", transport=s.transport()).check(TX_BYTES, POLICY)
    assert v.refused and not v.allowed and v.reason_codes == ["sol_out_exceeds_limit"]


def test_bytes_base64_and_a_bytes_object_send_the_same_body(scripted):
    s = scripted([(200, verdict(), None)])
    preflight = Preflight("k", transport=s.transport())
    wrapped = "\n".join(TX_B64[i : i + 76] for i in range(0, len(TX_B64), 76))
    for tx in (TX_B64, TX_BYTES, bytearray(TX_BYTES), memoryview(TX_BYTES), SoldersLike(), wrapped):
        preflight.check(tx, POLICY)
    assert len(s.calls) == 6
    assert all(c.content == s.calls[0].content for c in s.calls)
    assert s.body()["transaction"] == TX_B64


@pytest.mark.parametrize("bad", ["not base64!", "", b"", 12345, None, {"tx": TX_B64}])
def test_a_transaction_that_cannot_be_serialized_is_refused_before_any_request(scripted, bad):
    s = scripted([(200, verdict(), None)])
    with pytest.raises(OverwingError) as e:
        Preflight("k", transport=s.transport()).check(bad, POLICY)
    assert e.value.code == "invalid_input" and e.value.field == "transaction"
    assert s.calls == []


def test_check_needs_a_key_and_the_public_reads_do_not(scripted, monkeypatch):
    monkeypatch.delenv("OVERWING_API_KEY", raising=False)
    record = {"totals": {"checks": 2, "allowed": 1, "refused": 1, "misses": 0}, "misses": [], "recent": [{"id": ID, "decision": "refuse", "reason_codes": ["sol_out_exceeds_limit"], "programs": [SYSTEM], "digest": "8e49", "covered": False, "slot": 1}], "guarantee": {"active": False}}
    public = {"id": ID, "decision": "allow", "reason_codes": [], "programs": [SYSTEM], "digest": "8e49", "covered": True, "slot": 1, "reports": [{"transaction": "5sig", "outcome": "not_a_miss"}]}
    report = {"check_id": ID, "transaction": "5sig", "outcome": "not_a_miss", "why": None, "covered": True, "payout_usd": None}
    s = scripted([(200, public, None), (200, report, None), (200, record, None), (200, {"product": "Overwing Preflight"}, None)])
    preflight = Preflight(base_url="https://example.test/", transport=s.transport())
    with pytest.raises(OverwingError):
        preflight.check(TX_B64, POLICY)
    assert s.calls == []

    v = preflight.verdict(ID)
    assert v.allowed and v.reports[0]["outcome"] == "not_a_miss"
    r = preflight.report(ID, "5sig")
    assert not r.miss and r.check_id == ID and r.transaction == "5sig" and r.payout_usd is None
    rec = preflight.record()
    assert rec.totals["checks"] == 2 and rec.recent[0].refused and rec.recent[0].reason_codes == ["sol_out_exceeds_limit"] and rec.guarantee == {"active": False}
    assert preflight.overview()["product"] == "Overwing Preflight"
    assert [(c.method, c.url.path) for c in s.calls] == [("GET", f"/api/v1/preflight/checks/{ID}"), ("POST", f"/api/v1/preflight/checks/{ID}/reports"), ("GET", "/api/v1/preflight/record"), ("GET", "/api/v1/preflight")]
    assert all("authorization" not in c.headers for c in s.calls)
    assert s.body(1) == {"signature": "5sig"}
    assert preflight.x402_url() == "https://example.test/api/x402/preflight"


def test_api_errors_keep_their_status(scripted):
    s = scripted([(400, {"error": "policy.max_sol_out is required"}, None), (401, {"error": "Invalid API key"}, None), (502, {"error": "Solana node unreachable"}, None)])
    preflight = Preflight("k", transport=s.transport(), max_retries=0)
    for status in (400, 401, 502):
        with pytest.raises(OverwingError) as e:
            preflight.check(TX_B64, POLICY)
        assert e.value.status == status


@pytest.mark.parametrize("body", [None, [], "allow", {"decision": "allow"}, {"id": ID}, {"id": ID, "decision": None}, {"id": ID, "decision": True}, {"id": "", "decision": "allow"}])
def test_a_malformed_answer_raises_and_is_never_an_allow(scripted, body):
    s = scripted([(200, body, None)])
    with pytest.raises(OverwingError) as e:
        Preflight("k", transport=s.transport()).check(TX_B64, POLICY)
    assert e.value.code == "malformed_response"


def test_an_answer_that_is_not_json_raises():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text="<html>gateway</html>"))
    with pytest.raises(OverwingError) as e:
        Preflight("k", transport=transport).check(TX_B64, POLICY)
    assert e.value.code == "malformed_response"


def test_the_organization_client_has_the_same_calls(scripted):
    s = scripted([(200, verdict(), None), (200, verdict(), None), (200, {"check_id": ID, "transaction": "5sig", "outcome": "miss", "why": "took more", "covered": True, "payout_usd": None}, None), (200, {"totals": {}, "recent": []}, None), (200, {"available": True}, None)])
    ow = Overwing("ow_live_test", transport=s.transport())
    assert ow.preflight_check(SoldersLike(), POLICY).allowed
    assert ow.preflight_verdict(ID).id == ID
    assert ow.preflight_report(ID, "5sig").miss
    assert ow.preflight_record().recent == []
    assert ow.preflight_overview() == {"available": True}
    assert s.body() == {"transaction": TX_B64, "policy": POLICY}
    assert [c.url.path for c in s.calls] == ["/api/v1/preflight/checks", f"/api/v1/preflight/checks/{ID}", f"/api/v1/preflight/checks/{ID}/reports", "/api/v1/preflight/record", "/api/v1/preflight"]


# ---- overwing.solana: no allow, no signature ----


def test_require_allow_returns_the_verdict_on_allow(scripted):
    s = scripted([(200, verdict(), None)])
    v = require_allow(Preflight("k", transport=s.transport()), TX_BYTES, POLICY)
    assert v is not None and v.allowed and v.id == ID


def test_require_allow_raises_on_refuse_with_the_verdict(scripted):
    s = scripted([(200, verdict("refuse"), None)])
    with pytest.raises(PreflightRefused) as e:
        require_allow(Preflight("k", transport=s.transport()), TX_BYTES, POLICY)
    assert e.value.verdict.id == ID and e.value.verdict.reason_codes == ["sol_out_exceeds_limit"]
    assert "1.2 SOL would leave" in str(e.value) and e.value.code == "preflight_refused"
    assert isinstance(e.value, OverwingError)


def test_guarded_sign_signs_the_same_object_after_an_allow(scripted):
    s = scripted([(200, verdict(), None)])
    sign, tx, seen = Signer(), SoldersLike(), []
    assert guarded_sign(Preflight("k", transport=s.transport()), sign, tx, POLICY, on_verdict=seen.append) == "signed"
    assert sign.signed == [tx] and sign.signed[0] is tx
    assert seen[0].id == ID
    assert s.body()["transaction"] == TX_B64


def test_a_refusal_never_reaches_the_signing_function(scripted):
    s = scripted([(200, verdict("refuse"), None)])
    sign = Signer()
    with pytest.raises(PreflightRefused):
        guarded_sign(Preflight("k", transport=s.transport()), sign, TX_BYTES, POLICY)
    assert sign.signed == []


def test_a_refusal_is_final_even_with_on_unavailable_allow(scripted):
    s = scripted([(200, verdict("refuse"), None)])
    sign = Signer()
    with pytest.raises(PreflightRefused):
        guarded_sign(Preflight("k", transport=s.transport()), sign, TX_BYTES, POLICY, on_unavailable="allow")
    assert sign.signed == []


@pytest.mark.parametrize("decision", ["review", "ALLOW", "allowed", "unknown"])
def test_a_decision_that_is_not_allow_is_a_refusal(scripted, decision):
    s = scripted([(200, verdict(decision, reasons=[]), None)])
    sign = Signer()
    with pytest.raises(PreflightRefused) as e:
        guarded_sign(Preflight("k", transport=s.transport()), sign, TX_BYTES, POLICY, on_unavailable="allow")
    assert e.value.verdict.decision == decision and sign.signed == []


def test_a_network_failure_fails_closed():
    sign = Signer()
    with pytest.raises(OverwingError) as e:
        guarded_sign(Preflight("k", transport=httpx.MockTransport(unreachable)), sign, TX_BYTES, POLICY)
    assert not isinstance(e.value, PreflightRefused) and e.value.code == "unreachable" and e.value.status == 0
    assert sign.signed == []


@pytest.mark.parametrize("status", [400, 401, 429, 500, 502, 503])
def test_an_http_error_fails_closed(scripted, status):
    s = scripted([(status, {"error": "no verdict"}, None)])
    sign = Signer()
    with pytest.raises(OverwingError) as e:
        guarded_sign(Preflight("k", transport=s.transport(), max_retries=0), sign, TX_BYTES, POLICY)
    assert e.value.status == status and sign.signed == []


@pytest.mark.parametrize("body", [None, [], {"decision": "allow"}, {"id": ID}])
def test_a_malformed_answer_fails_closed(scripted, body):
    s = scripted([(200, body, None)])
    sign = Signer()
    with pytest.raises(OverwingError):
        guarded_sign(Preflight("k", transport=s.transport()), sign, TX_BYTES, POLICY)
    assert sign.signed == []


def test_on_unavailable_allow_proceeds_when_there_is_no_verdict(scripted, caplog):
    preflight = Preflight("k", transport=httpx.MockTransport(unreachable))
    assert require_allow(preflight, TX_BYTES, POLICY, on_unavailable="allow") is None
    assert "unchecked" in caplog.text
    sign, seen = Signer(), []
    assert guarded_sign(preflight, sign, TX_BYTES, POLICY, on_unavailable="allow", on_verdict=seen.append) == "signed"
    assert sign.signed == [TX_BYTES] and seen == [None]

    for status, body in ((502, {"error": "Solana node unreachable"}), (503, {"error": "not enabled"}), (200, {"id": ID})):
        s = scripted([(status, body, None)])
        sign = Signer()
        guarded_sign(Preflight("k", transport=s.transport(), max_retries=0), sign, TX_BYTES, POLICY, on_unavailable="allow")
        assert sign.signed == [TX_BYTES]


@pytest.mark.parametrize("status", [400, 401, 403, 429])
def test_on_unavailable_allow_does_not_cover_a_rejected_request(scripted, status):
    s = scripted([(status, {"error": "rejected"}, None)])
    sign = Signer()
    with pytest.raises(OverwingError) as e:
        guarded_sign(Preflight("k", transport=s.transport(), max_retries=0), sign, TX_BYTES, POLICY, on_unavailable="allow")
    assert e.value.status == status and sign.signed == []


def test_on_unavailable_allow_does_not_cover_a_missing_key_or_a_bad_transaction(scripted, monkeypatch):
    monkeypatch.delenv("OVERWING_API_KEY", raising=False)
    s = scripted([(200, verdict(), None)])
    sign = Signer()
    for client, tx in ((Preflight(transport=s.transport()), TX_BYTES), (Overwing(transport=s.transport()), TX_BYTES), (Preflight("k", transport=s.transport()), "not base64!")):
        with pytest.raises(OverwingError):
            guarded_sign(client, sign, tx, POLICY, on_unavailable="allow")
    assert sign.signed == [] and s.calls == []


def test_on_unavailable_must_be_refuse_or_allow(scripted):
    s = scripted([(200, verdict(), None)])
    sign = Signer()
    with pytest.raises(ValueError):
        guarded_sign(Preflight("k", transport=s.transport()), sign, TX_BYTES, POLICY, on_unavailable="open")  # type: ignore[arg-type]
    assert sign.signed == [] and s.calls == []


def test_the_helpers_take_the_organization_client(scripted):
    s = scripted([(200, verdict(), None), (200, verdict("refuse"), None)])
    ow, sign = Overwing("k", transport=s.transport()), Signer()
    assert guarded_sign(ow, sign, TX_B64, POLICY) == "signed"
    with pytest.raises(PreflightRefused):
        guarded_sign(ow, sign, TX_B64, POLICY)
    assert sign.signed == [TX_B64]


def test_encode_transaction():
    assert encode_transaction(TX_BYTES) == encode_transaction(TX_B64) == encode_transaction(SoldersLike()) == TX_B64


# ---- async ----


async def test_async_client(scripted):
    s = scripted([(200, verdict(), None), (200, verdict("refuse"), None), (200, {"check_id": ID, "transaction": "5sig", "outcome": "not_a_miss"}, None), (200, {"totals": {"checks": 1}}, None), (200, {"available": True}, None)])
    async with AsyncPreflight("ow_live_test", transport=s.transport()) as preflight:
        assert (await preflight.check(TX_BYTES, POLICY)).allowed
        assert (await preflight.verdict(ID)).refused
        assert not (await preflight.report(ID, "5sig")).miss
        assert (await preflight.record()).totals == {"checks": 1}
        assert await preflight.overview() == {"available": True}
    assert s.body() == {"transaction": TX_B64, "policy": POLICY}
    assert s.calls[0].headers["authorization"] == "Bearer ow_live_test"


async def test_async_allow_signs_with_a_plain_or_a_coroutine_function(scripted):
    s = scripted([(200, verdict(), None)])
    preflight = AsyncPreflight("k", transport=s.transport())
    v = await require_allow_async(preflight, TX_B64, POLICY)
    assert v is not None and v.allowed
    sign = Signer()
    assert await guarded_sign_async(preflight, sign, TX_BYTES, POLICY) == "signed"

    async def async_sign(tx):
        sign.signed.append(tx)
        return "signed async"

    assert await guarded_sign_async(preflight, async_sign, TX_BYTES, POLICY) == "signed async"
    assert sign.signed == [TX_BYTES, TX_BYTES]


async def test_async_refusal_and_failures_never_sign(scripted, monkeypatch):
    async def no_wait(_s):
        return None

    monkeypatch.setattr("asyncio.sleep", no_wait)
    sign = Signer()
    s = scripted([(200, verdict("refuse"), None)])
    with pytest.raises(PreflightRefused) as e:
        await guarded_sign_async(AsyncPreflight("k", transport=s.transport()), sign, TX_BYTES, POLICY, on_unavailable="allow")
    assert e.value.verdict.id == ID

    down = AsyncPreflight("k", transport=httpx.MockTransport(unreachable))
    with pytest.raises(OverwingError) as e2:
        await guarded_sign_async(down, sign, TX_BYTES, POLICY)
    assert e2.value.code == "unreachable"

    s = scripted([(200, {"id": ID}, None)])
    with pytest.raises(OverwingError):
        await guarded_sign_async(AsyncPreflight("k", transport=s.transport()), sign, TX_BYTES, POLICY)
    assert sign.signed == []

    assert await require_allow_async(down, TX_BYTES, POLICY, on_unavailable="allow") is None
    assert await guarded_sign_async(down, sign, TX_BYTES, POLICY, on_unavailable="allow") == "signed"
    assert sign.signed == [TX_BYTES]


async def test_async_organization_client(scripted):
    s = scripted([(200, verdict(), None), (200, verdict("refuse"), None)])
    aow, sign = AsyncOverwing("k", transport=s.transport()), Signer()
    assert (await aow.preflight_check(TX_BYTES, POLICY)).allowed
    with pytest.raises(PreflightRefused):
        await guarded_sign_async(aow, sign, TX_BYTES, POLICY)
    assert sign.signed == []
