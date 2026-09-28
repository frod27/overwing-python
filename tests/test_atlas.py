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
