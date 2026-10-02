import pytest

from overwing import AsyncAtlas, Atlas, Overwing, OverwingError


def lookup_body(**extra):
    return {
        "user_agent": "GPTBot/1.2",
        "identified": True,
        "claims": {"agent": "GPTBot", "operator": "OpenAI", "purpose_class": "Training / bulk crawl", "verification": "User-agent string only (spoofable)", "matched_token": "GPTBot"},
        "trust_note": "A user-agent string is not proof of identity.",
        "matches": [],
        **extra,
    }


def test_keyless_lookup_sends_no_authorization(scripted, monkeypatch):
    monkeypatch.delenv("OVERWING_API_KEY", raising=False)
    s = scripted([(200, lookup_body(access={"mode": "keyless", "daily_limit": 10, "remaining_today": 9, "next": {}}), {"x-atlas-lookup-limit": "10", "x-atlas-lookup-remaining": "9"})])
    with Atlas(base_url="https://example.test", transport=s.transport()) as atlas:
        assert atlas.keyless
        who = atlas.lookup("Mozilla/5.0 (compatible; GPTBot/1.2; +https://openai.com/gptbot)")
    assert who.identified and who.agent == "GPTBot" and who.operator == "OpenAI"
    assert who.keyless and who.remaining_today == 9 and who.daily_limit == 10
    assert not who.signed
    req = s.calls[0]
    assert "authorization" not in req.headers
    assert req.url.path == "/api/v1/atlas/lookup"
    assert req.url.params["user_agent"] == "Mozilla/5.0 (compatible; GPTBot/1.2; +https://openai.com/gptbot)"


def test_key_is_sent_when_given(scripted):
    s = scripted([(200, lookup_body(), None)])
    atlas = Atlas("ow_live_test", transport=s.transport())
    assert not atlas.keyless
    who = atlas.lookup("GPTBot")
    assert s.calls[0].headers["authorization"] == "Bearer ow_live_test"
    assert not who.keyless and who.remaining_today is None


def test_unidentified_string(scripted, monkeypatch):
    monkeypatch.delenv("OVERWING_API_KEY", raising=False)
    s = scripted([(200, {"user_agent": "curl/8", "identified": False, "claims": None, "trust_note": "No registered agent matches.", "matches": []}, None)])
    who = Atlas(transport=s.transport()).lookup("curl/8")
    assert not who.identified and who.agent is None and who.verification is None


def test_signed_operator(scripted):
    body = lookup_body()
    body["claims"]["verification"] = "Web Bot Auth signature"
    s = scripted([(200, body, None)])
    assert Atlas("k", transport=s.transport()).lookup("ChatGPT-Agent").signed


def test_spent_allowance_is_a_429_and_is_not_retried(scripted, monkeypatch):
    monkeypatch.delenv("OVERWING_API_KEY", raising=False)
    s = scripted([(429, {"error": "Keyless allowance spent (10 lookups a day without a key)", "next": {}}, {"retry-after": "13000"})])
    with pytest.raises(OverwingError) as ei:
        Atlas(transport=s.transport()).lookup("GPTBot")
    assert ei.value.status == 429 and ei.value.retry_after_seconds == 13000 and "Keyless allowance spent" in str(ei.value)
    assert len(s.calls) == 1


def test_registry_filters_and_main_client(scripted):
    s = scripted([(200, {"total": 0, "agents": []}, None), (200, lookup_body(), {"x-atlas-lookup-limit": "100", "x-atlas-lookup-remaining": "99"})])
    Atlas("k", base_url="https://example.test", transport=s.transport()).agents(purpose="browser", limit=5)
    assert str(s.calls[0].url) == "https://example.test/api/v1/atlas/agents?purpose=browser&limit=5"
    who = Overwing("ow_live_test", transport=s.transport()).atlas_lookup("GPTBot")
    assert who.remaining_today == 99 and s.calls[1].headers["authorization"] == "Bearer ow_live_test"


async def test_async_atlas(scripted, monkeypatch):
    monkeypatch.delenv("OVERWING_API_KEY", raising=False)
    s = scripted([(200, lookup_body(), {"x-atlas-lookup-limit": "10", "x-atlas-lookup-remaining": "3"})])
    async with AsyncAtlas(transport=s.transport()) as atlas:
        who = await atlas.lookup("GPTBot")
    assert who.agent == "GPTBot" and who.remaining_today == 3
    assert "authorization" not in s.calls[0].headers


REG = {"id": "areg_0123456789abcdef", "status": "pending_verification", "name": "AcmeBot", "operator": "Acme, Inc.", "domain": "acme.com", "tokens": ["AcmeBot"]}
STEPS = {"value": "overwing-atlas-verification=tok", "dns": {"type": "TXT", "name": "_overwing-atlas.acme.com", "value": "overwing-atlas-verification=tok"}, "http": {"url": "https://acme.com/.well-known/overwing-atlas.txt", "body": "overwing-atlas-verification=tok"}}


def test_register_sends_only_what_was_given_and_returns_what_to_publish(scripted):
    s = scripted([(201, {**REG, "verification": STEPS}, None)])
    with Atlas("ow_live_test", base_url="https://example.test", transport=s.transport()) as atlas:
        reg = atlas.register("AcmeBot", operator="Acme, Inc.", domain="acme.com", tokens=["AcmeBot"], follows_robots_txt=False)
    assert reg.pending_verification and not reg.published
    assert (reg.dns_name, reg.dns_value) == ("_overwing-atlas.acme.com", "overwing-atlas-verification=tok")
    assert reg.file_url == "https://acme.com/.well-known/overwing-atlas.txt" and reg.file_body == reg.dns_value
    req = s.calls[0]
    assert req.method == "POST" and req.url.path == "/api/v1/atlas/registrations"
    assert req.headers["authorization"] == "Bearer ow_live_test"
    assert s.body() == {"name": "AcmeBot", "operator": "Acme, Inc.", "domain": "acme.com", "tokens": ["AcmeBot"], "follows_robots_txt": False}


def test_verify_reads_a_missing_proof_as_a_state_then_published(scripted):
    s = scripted([
        (422, {"error": "No proof found.", "registration": REG}, None),
        (200, {**REG, "status": "published", "agent": "https://overwing.ai/api/v1/atlas/agents/acmebot"}, None),
        (200, {"registrations": [{**REG, "status": "published"}]}, None),
        (200, {"id": REG["id"], "status": "withdrawn"}, None),
    ])
    atlas = Atlas("ow_live_test", transport=s.transport())
    first = atlas.verify_registration(REG["id"])
    assert first.pending_verification and first.error == "No proof found."
    second = atlas.verify_registration(REG["id"])
    assert second.published and second.error is None and second.agent.endswith("/acmebot")
    assert s.calls[1].url.path == f"/api/v1/atlas/registrations/{REG['id']}/verify"
    assert [r.status for r in atlas.registrations()] == ["published"]
    assert atlas.withdraw_registration(REG["id"]).status == "withdrawn"
    assert s.calls[3].method == "DELETE"


async def test_async_register_and_verify(scripted):
    s = scripted([(201, {**REG, "verification": STEPS}, None), (200, {**REG, "status": "published"}, None)])
    async with AsyncAtlas("ow_live_test", transport=s.transport()) as atlas:
        reg = await atlas.register("AcmeBot", operator="Acme, Inc.", domain="acme.com", tokens=["AcmeBot"])
        done = await atlas.verify_registration(reg.id)
    assert reg.dns_name == "_overwing-atlas.acme.com" and done.published
