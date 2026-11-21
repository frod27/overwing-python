import hashlib
import hmac
import time

import pytest

from overwing import AsyncOverwing, AsyncRadar, Overwing, OverwingError, Radar, RadarDecoy, RadarIncident, RadarIngest, verify_radar_webhook

KEY = "ow_live_test"
INCIDENT_ID = "swi_0123456789abcdefghij"
DECOY_ID = "swd_0123456789abcdefghij"
DRILL_ID = "swdr_0123456789abcdefghij"
HASH = "sha256:9f2c0e41bd77aa10"

EVENTS = [
    {"id": "evt-000001", "surface_id": "scratch-bucket", "artifact_id": "notes/plan.md", "actor_id": "agent-17", "principal_id": "team-a", "op": "read", "ts": "2026-11-20T12:00:00Z", "content_hash": HASH, "size_bytes": 812},
    {"id": "evt-000002", "surface_id": "scratch-bucket", "artifact_id": "out/agent-17/step.txt", "actor_id": "agent-17", "principal_id": None, "op": "write", "ts": 1795262400000, "parent_artifact_hash": HASH, "metadata": {"region": "us-east-1"}},
]
CLOUDTRAIL = {"Records": [{"eventSource": "s3.amazonaws.com", "eventName": "GetObject", "eventTime": "2026-11-20T12:00:00Z"}]}
THRESHOLDS = {"windowMinutes": 30, "sweep": {"enabled": True, "minArtifacts": 20, "windowSeconds": 60, "exemptActors": []}, "response": {"enabled": False, "minSeverity": "critical"}}


def incident(**extra):
    return {
        "id": INCIDENT_ID,
        "status": "open",
        "severity": "critical",
        "title": "A decoy key was tried on ci-secrets",
        "detectors": ["decoy_credential"],
        "surfaces": ["ci-secrets"],
        "actors": ["ip:203.0.113.7"],
        "first_event_at": "2026-11-20T12:00:00.000Z",
        "last_event_at": "2026-11-20T12:00:05.000Z",
        "opened_at": "2026-11-20T12:00:06.000Z",
        "updated_at": "2026-11-20T12:00:06.000Z",
        "closed_at": None,
        "closed_by": None,
        "drill_id": None,
        "recommended_actions": [{"action": "rotate_secrets", "surface": "ci-secrets", "reason": "A key kept there was read and tried."}],
        "enforced": False,
        "responses": [],
        "contained_in_seconds": None,
        "url": f"https://overwing.ai/dashboard/swarm/incidents/{INCIDENT_ID}",
        "evidence_count": 1,
        **extra,
    }


def decoy(**extra):
    return {"id": DECOY_ID, "kind": "artifact", "surface_id": "scratch-bucket", "artifact_id": "notes/handover-a1b2c3.md", "content_hash": HASH, "label": None, "created_at": "2026-11-20T12:00:00.000Z", "retired_at": None, **extra}


# One row per method: its name, its arguments, then the request it must send and an answer to give it.
# (name, args, kwargs, method, path with query, body or None, status, answer)
CALLS = [
    ("status", (), {}, "GET", "/api/v1/swarm", None, 200, {"product": "Overwing Radar", "events_this_month": 2, "monthly_allowance": 10000}),
    ("send_events", (EVENTS,), {}, "POST", "/api/v1/swarm/events", {"events": EVENTS}, 202, {"accepted": 2, "duplicates": 0, "drill_id": None}),
    ("send_events", (EVENTS,), {"drill_id": DRILL_ID}, "POST", "/api/v1/swarm/events", {"events": EVENTS, "drill_id": DRILL_ID}, 202, {"accepted": 1, "duplicates": 1, "drill_id": DRILL_ID}),
    ("list_surfaces", (), {}, "GET", "/api/v1/swarm/surfaces", None, 200, {"surfaces": [{"id": "scratch-bucket", "kind": "bucket", "label": None, "created_at": "2026-11-20T12:00:00.000Z", "last_24h": {"events": 2, "actors": 1, "last_event_at": None}}]}),
    ("name_surface", ("scratch-bucket",), {"kind": "bucket", "label": "Scratch"}, "POST", "/api/v1/swarm/surfaces", {"id": "scratch-bucket", "kind": "bucket", "label": "Scratch"}, 201, {"id": "scratch-bucket", "kind": "bucket", "label": "Scratch", "created_at": "2026-11-20T12:00:00.000Z"}),
    ("name_surface", ("scratch-bucket",), {}, "POST", "/api/v1/swarm/surfaces", {"id": "scratch-bucket"}, 201, {"id": "scratch-bucket", "kind": None, "label": None}),
    ("list_decoys", (), {}, "GET", "/api/v1/swarm/decoys", None, 200, {"decoys": [decoy()]}),
    ("mint_decoy", ("scratch-bucket",), {}, "POST", "/api/v1/swarm/decoys", {"surface_id": "scratch-bucket"}, 201, decoy(content="# Handover\n", content_shown_once=True, plant="Write `content` to `artifact_id`.")),
    ("mint_decoy", ("ci-secrets", "key", "llm gateway"), {}, "POST", "/api/v1/swarm/decoys", {"surface_id": "ci-secrets", "kind": "key", "label": "llm gateway"}, 201, decoy(kind="key", surface_id="ci-secrets", artifact_id="credential:llm-gateway-a1b2c3", label="llm gateway")),
    ("register_decoy", ("scratch-bucket", "backups/old.env", HASH), {"label": "ours"}, "POST", "/api/v1/swarm/decoys", {"surface_id": "scratch-bucket", "artifact_id": "backups/old.env", "content_hash": HASH, "label": "ours"}, 201, decoy(artifact_id="backups/old.env", label="ours")),
    ("retire_decoy", (DECOY_ID,), {}, "DELETE", f"/api/v1/swarm/decoys/{DECOY_ID}", None, 200, {"id": DECOY_ID, "retired": True}),
    ("list_incidents", (), {}, "GET", "/api/v1/swarm/incidents", None, 200, {"incidents": [incident()]}),
    ("list_incidents", (), {"status": "open", "limit": 10}, "GET", "/api/v1/swarm/incidents?status=open&limit=10", None, 200, {"incidents": []}),
    ("get_incident", (INCIDENT_ID,), {}, "GET", f"/api/v1/swarm/incidents/{INCIDENT_ID}", None, 200, incident()),
    ("close_incident", (INCIDENT_ID,), {}, "POST", f"/api/v1/swarm/incidents/{INCIDENT_ID}/close", None, 200, incident(status="closed")),
    ("list_responses", (INCIDENT_ID,), {}, "GET", f"/api/v1/swarm/incidents/{INCIDENT_ID}/responses", None, 200, {"incident_id": INCIDENT_ID, "responses": [], "contained_in_seconds": None}),
    ("report_response", (INCIDENT_ID, "revoke_identity", "done"), {"target": "arn:aws:iam::111122223333:user/svc-reports", "detail": "exit 0", "by": "responder-east"}, "POST", f"/api/v1/swarm/incidents/{INCIDENT_ID}/responses", {"action": "revoke_identity", "status": "done", "target": "arn:aws:iam::111122223333:user/svc-reports", "detail": "exit 0", "by": "responder-east"}, 201, {"incident_id": INCIDENT_ID, "recorded": True, "responses": 1, "contained_in_seconds": 42}),
    ("report_response", (INCIDENT_ID, "rotate_secrets", "dry_run"), {}, "POST", f"/api/v1/swarm/incidents/{INCIDENT_ID}/responses", {"action": "rotate_secrets", "status": "dry_run"}, 201, {"incident_id": INCIDENT_ID, "recorded": True, "responses": 2, "contained_in_seconds": 42}),
    ("get_thresholds", (), {}, "GET", "/api/v1/swarm/config", None, 200, {"thresholds": THRESHOLDS, "changed": {}, "defaults": THRESHOLDS}),
    ("set_thresholds", ({"sweep": {"minArtifacts": 40, "exemptActors": ["svc-backup"]}},), {}, "PUT", "/api/v1/swarm/config", {"sweep": {"minArtifacts": 40, "exemptActors": ["svc-backup"]}}, 200, {"thresholds": THRESHOLDS, "changed": {"sweep": {"minArtifacts": 40, "exemptActors": ["svc-backup"]}}, "defaults": THRESHOLDS}),
    ("set_thresholds", ({},), {}, "PUT", "/api/v1/swarm/config", {}, 200, {"thresholds": THRESHOLDS, "changed": {}, "defaults": THRESHOLDS}),
    ("send_cloudtrail", (CLOUDTRAIL,), {}, "POST", "/api/v1/swarm/connectors/aws", CLOUDTRAIL, 202, {"records": 1, "accepted": 1, "duplicates": 0, "skipped": 0}),
]
IDS = [f"{i}-{c[0]}" for i, c in enumerate(CALLS)]
METHODS = sorted({c[0] for c in CALLS})


def sent(s, method, path, body):
    """The one request made: the method, the path with its query, the key, and the body or none."""
    assert len(s.calls) == 1
    req = s.calls[0]
    assert req.method == method
    assert str(req.url) == f"https://example.test{path}"
    assert req.headers["authorization"] == f"Bearer {KEY}"
    if body is None:
        assert req.content == b"" and "content-type" not in req.headers
    else:
        assert s.body() == body and req.headers["content-type"] == "application/json"


# ---- each method sends the right request ----


@pytest.mark.parametrize("name,args,kwargs,method,path,body,status,answer", CALLS, ids=IDS)
def test_sync_radar_sends(scripted, name, args, kwargs, method, path, body, status, answer):
    s = scripted([(status, answer, None)])
    with Radar(KEY, base_url="https://example.test", transport=s.transport()) as radar:
        getattr(radar, name)(*args, **kwargs)
    sent(s, method, path, body)


@pytest.mark.parametrize("name,args,kwargs,method,path,body,status,answer", CALLS, ids=IDS)
async def test_async_radar_sends(scripted, name, args, kwargs, method, path, body, status, answer):
    s = scripted([(status, answer, None)])
    async with AsyncRadar(KEY, base_url="https://example.test", transport=s.transport()) as radar:
        await getattr(radar, name)(*args, **kwargs)
    sent(s, method, path, body)


@pytest.mark.parametrize("name,args,kwargs,method,path,body,status,answer", CALLS, ids=IDS)
def test_main_client_sends_the_same_request(scripted, name, args, kwargs, method, path, body, status, answer):
    s = scripted([(status, answer, None)])
    with Overwing(KEY, base_url="https://example.test", transport=s.transport()) as ow:
        getattr(ow, f"radar_{name}")(*args, **kwargs)
    sent(s, method, path, body)


@pytest.mark.parametrize("name,args,kwargs,method,path,body,status,answer", CALLS, ids=IDS)
async def test_async_main_client_sends_the_same_request(scripted, name, args, kwargs, method, path, body, status, answer):
    s = scripted([(status, answer, None)])
    async with AsyncOverwing(KEY, base_url="https://example.test", transport=s.transport()) as ow:
        await getattr(ow, f"radar_{name}")(*args, **kwargs)
    sent(s, method, path, body)


def test_every_public_method_is_in_the_table_and_on_all_four_clients():
    public = sorted(n for n, v in vars(Radar).items() if callable(v) and not n.startswith("_"))
    assert public == METHODS
    assert sorted(n for n, v in vars(AsyncRadar).items() if callable(v) and not n.startswith("_")) == METHODS
    for client in (Overwing, AsyncOverwing):
        assert sorted(n[len("radar_"):] for n in vars(client) if n.startswith("radar_")) == METHODS


# ---- what comes back ----


def test_send_events_returns_the_counts(scripted):
    s = scripted([(202, {"accepted": 1, "duplicates": 1, "drill_id": None}, None)])
    result = Radar(KEY, transport=s.transport()).send_events(EVENTS)
    assert isinstance(result, RadarIngest) and (result.accepted, result.duplicates, result.drill_id) == (1, 1, None)
    # A null principal_id is sent as given: it is the event's own field, not an option left out.
    assert s.body()["events"][1]["principal_id"] is None


def test_a_decoy_key_carries_the_address_the_key_and_the_lines_to_keep(scripted):
    snippet = "# internal model gateway\nLLM_GATEWAY_BASE_URL=https://overwing.ai/api/gw/v1\nLLM_GATEWAY_API_KEY=sk-gw-secret\n"
    s = scripted([(201, decoy(kind="key", surface_id="ci-secrets", artifact_id="credential:llm-gateway-a1b2c3", label="llm gateway", base_url="https://overwing.ai/api/gw/v1", key="sk-gw-secret", content=snippet, content_shown_once=True, plant="Keep `content` where real keys are kept."), None)])
    d = Radar(KEY, transport=s.transport()).mint_decoy("ci-secrets", kind="key", label="llm gateway")
    assert isinstance(d, RadarDecoy) and d.kind == "key" and d.id == DECOY_ID and d.surface_id == "ci-secrets"
    assert d.base_url == "https://overwing.ai/api/gw/v1" and d.key == "sk-gw-secret" and d.content == snippet
    assert d.plant is not None and d.warning is None and not d.retired
    assert "sk-gw-secret" not in repr(d)


def test_decoys_minted_registered_and_listed(scripted):
    s = scripted([
        (201, decoy(content="# Handover\n\ntoken abc\n", content_shown_once=True, plant="Write it."), None),
        (201, decoy(artifact_id="backups/old.env", plant="Recorded."), None),
        (200, {"decoys": [decoy(), decoy(id="swd_retired0000000000000", retired_at="2026-11-21T09:00:00.000Z")]}, None),
    ])
    radar = Radar(KEY, transport=s.transport())
    minted = radar.mint_decoy("scratch-bucket", "notes")
    assert minted.content == "# Handover\n\ntoken abc\n" and minted.artifact_id == "notes/handover-a1b2c3.md" and minted.content_hash == HASH and minted.key is None
    assert "token abc" not in repr(minted)
    assert s.body() == {"surface_id": "scratch-bucket", "kind": "notes"}
    assert radar.register_decoy("scratch-bucket", "backups/old.env", HASH).content is None
    assert [d.retired for d in radar.list_decoys()] == [False, True]


def test_incidents_listed_read_and_closed(scripted):
    evidence = [{"detector": "decoy_credential", "score": 1, "summary": "A decoy key was tried.", "surfaces": ["ci-secrets"], "actors": ["ip:203.0.113.7"], "artifacts": ["credential:llm-gateway-a1b2c3"], "event_ids": ["e1"], "from": 1, "to": 2}]
    timeline = [{"at": "2026-11-20T12:00:00.000Z", "event_id": "e1", "actor_id": "ip:203.0.113.7", "op": "read", "surface_id": "ci-secrets", "artifact_id": "credential:llm-gateway-a1b2c3"}]
    full = {k: v for k, v in incident(evidence=evidence, timeline=timeline, webhook=None).items() if k != "evidence_count"}
    s = scripted([(200, {"incidents": [incident()]}, None), (200, full, None), (200, incident(status="closed", closed_at="2026-11-20T13:00:00.000Z", closed_by="key:radar"), None)])
    radar = Radar(KEY, transport=s.transport())
    (listed,) = radar.list_incidents(status="open")
    assert isinstance(listed, RadarIncident) and listed.open and listed.severity == "critical" and listed.detectors == ["decoy_credential"]
    assert listed.evidence_count == 1 and listed.evidence == [] and listed.timeline == []
    assert listed.recommended_actions[0]["action"] == "rotate_secrets" and listed.enforced is False
    one = radar.get_incident(INCIDENT_ID)
    assert one.evidence == evidence and one.timeline == timeline and one.evidence_count is None and one.url.endswith(INCIDENT_ID)
    closed = radar.close_incident(INCIDENT_ID)
    assert not closed.open and closed.closed_at == "2026-11-20T13:00:00.000Z" and closed.closed_by == "key:radar"


def test_lists_and_plain_answers_come_back_unwrapped(scripted):
    s = scripted([(200, {"surfaces": [{"id": "scratch-bucket", "kind": "bucket"}]}, None), (200, {"incident_id": INCIDENT_ID, "responses": [{"action": "revoke_identity", "status": "done"}], "contained_in_seconds": 42}, None), (200, {"thresholds": THRESHOLDS, "changed": {}, "defaults": THRESHOLDS}, None)])
    radar = Radar(KEY, transport=s.transport())
    assert radar.list_surfaces() == [{"id": "scratch-bucket", "kind": "bucket"}]
    assert radar.list_responses(INCIDENT_ID)["contained_in_seconds"] == 42
    assert radar.get_thresholds()["thresholds"]["sweep"]["minArtifacts"] == 20


def test_an_id_is_one_path_segment(scripted):
    s = scripted([(404, {"error": "Not an incident id."}, None)])
    with pytest.raises(OverwingError):
        Radar(KEY, base_url="https://example.test", transport=s.transport()).get_incident("a/b?c")
    assert s.calls[0].url.raw_path == b"/api/v1/swarm/incidents/a%2Fb%3Fc"


# ---- errors ----


def test_a_refused_batch_raises_with_the_event_named(scripted):
    s = scripted([(400, {"error": "events[1]: op: Invalid option."}, None)])
    with pytest.raises(OverwingError) as ei:
        Radar(KEY, transport=s.transport()).send_events(EVENTS)
    assert ei.value.status == 400 and "events[1]" in str(ei.value) and ei.value.body == {"error": "events[1]: op: Invalid option."}


def test_api_errors_carry_the_status(scripted):
    s = scripted([
        (404, {"error": "No incident with this id on this account."}, None),
        (409, {"error": "There is already a decoy at this artifact_id on this surface."}, None),
        (402, {"error": "Over the month's event allowance."}, None),
        (403, {"error": "This key's scope (\"swarm\") does not allow GET /api/v1/swarm/incidents. Use a key with scope \"full\"."}, None),
        (422, {"error": "An incident keeps 50 reports and this one has them."}, None),
    ])
    radar = Radar(KEY, transport=s.transport(), max_retries=0)
    calls = [lambda: radar.get_incident(INCIDENT_ID), lambda: radar.register_decoy("s", "a", HASH), lambda: radar.send_events(EVENTS), lambda: radar.list_incidents(), lambda: radar.report_response(INCIDENT_ID, "revoke_identity", "done")]
    for call, status in zip(calls, (404, 409, 402, 403, 422)):
        with pytest.raises(OverwingError) as ei:
            call()
        assert ei.value.status == status
    assert len(s.calls) == 5


def test_a_rate_limit_with_a_long_wait_is_not_retried(scripted):
    s = scripted([(429, {"error": "Too many ingest requests."}, {"retry-after": "30"})])
    with pytest.raises(OverwingError) as ei:
        Radar(KEY, transport=s.transport()).send_events(EVENTS)
    assert ei.value.status == 429 and ei.value.retry_after_seconds == 30 and len(s.calls) == 1


def test_a_server_error_is_retried_since_an_event_id_is_counted_once(scripted, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    s = scripted([(503, {"error": "Overwing Radar is not switched on for this deployment."}, None), (202, {"accepted": 2, "duplicates": 0}, None)])
    assert Radar(KEY, transport=s.transport()).send_events(EVENTS).accepted == 2
    assert len(s.calls) == 2 and s.body(0) == s.body(1)


async def test_async_errors_surface_the_same_way(scripted):
    s = scripted([(404, {"error": "No open incident with this id on this account."}, None), (400, {"error": "Bad thresholds: sweep.minArtifacts Too small."}, None)])
    async with AsyncRadar(KEY, transport=s.transport()) as radar:
        with pytest.raises(OverwingError) as ei:
            await radar.close_incident(INCIDENT_ID)
        assert ei.value.status == 404 and "No open incident" in str(ei.value)
        with pytest.raises(OverwingError) as ei:
            await radar.set_thresholds({"sweep": {"minArtifacts": 1}})
        assert ei.value.status == 400


# ---- the key ----


def test_the_key_is_read_from_the_environment(scripted, monkeypatch):
    monkeypatch.setenv("OVERWING_API_KEY", "ow_live_from_env")
    s = scripted([(200, {"product": "Overwing Radar"}, None)])
    assert Radar(transport=s.transport()).status()["product"] == "Overwing Radar"
    assert s.calls[0].headers["authorization"] == "Bearer ow_live_from_env"


@pytest.mark.parametrize("name,args,kwargs", [(c[0], c[1], c[2]) for c in CALLS], ids=IDS)
def test_no_key_every_method_raises_before_a_request(scripted, monkeypatch, name, args, kwargs):
    monkeypatch.delenv("OVERWING_API_KEY", raising=False)
    s = scripted([(200, {}, None)])
    with pytest.raises(OverwingError, match="API key missing"):
        getattr(Radar(transport=s.transport()), name)(*args, **kwargs)
    with pytest.raises(OverwingError, match="API key missing"):
        getattr(Overwing(transport=s.transport()), f"radar_{name}")(*args, **kwargs)
    assert s.calls == []


@pytest.mark.parametrize("name,args,kwargs", [(c[0], c[1], c[2]) for c in CALLS], ids=IDS)
async def test_async_no_key_every_method_raises_before_a_request(scripted, monkeypatch, name, args, kwargs):
    monkeypatch.delenv("OVERWING_API_KEY", raising=False)
    s = scripted([(200, {}, None)])
    with pytest.raises(OverwingError, match="API key missing"):
        await getattr(AsyncRadar(transport=s.transport()), name)(*args, **kwargs)
    with pytest.raises(OverwingError, match="API key missing"):
        await getattr(AsyncOverwing(transport=s.transport()), f"radar_{name}")(*args, **kwargs)
    assert s.calls == []


# ---- what is refused before a request ----


def test_a_batch_that_cannot_be_sent_is_refused_before_a_request(scripted):
    s = scripted([(202, {"accepted": 0, "duplicates": 0}, None)])
    radar = Radar(KEY, transport=s.transport())
    one = EVENTS[0]
    for bad in ([], [one] * 501, one, "evt-000001", [one, "evt-000002"], iter([one])):
        with pytest.raises(OverwingError) as ei:
            radar.send_events(bad)  # type: ignore[arg-type]
        assert ei.value.code == "invalid_input" and ei.value.field == "events" and ei.value.retryable is False
    with pytest.raises(OverwingError, match="At most 500"):
        radar.send_events([one] * 501)
    with pytest.raises(OverwingError) as ei:
        radar.set_thresholds([("sweep", {})])  # type: ignore[arg-type]
    assert ei.value.field == "changes"
    with pytest.raises(OverwingError) as ei:
        radar.send_cloudtrail('{"Records": []}')  # type: ignore[arg-type]
    assert ei.value.field == "body"
    assert s.calls == []
    assert radar.send_events([one] * 500).accepted == 0 and len(s.body()["events"]) == 500
    assert radar.send_events((one,)).accepted == 0 and s.body(1) == {"events": [one]}


def test_cloudtrail_takes_a_log_file_an_event_or_a_list(scripted):
    event = {"detail-type": "AWS API Call via CloudTrail", "detail": CLOUDTRAIL["Records"][0]}
    s = scripted([(202, {"records": 1, "accepted": 1, "duplicates": 0, "skipped": 0}, None)])
    radar = Radar(KEY, transport=s.transport())
    assert radar.send_cloudtrail(event)["accepted"] == 1 and s.body(0) == event
    radar.send_cloudtrail([event, CLOUDTRAIL])
    assert s.body(1) == [event, CLOUDTRAIL]


# ---- the webhook signature ----

SECRET = "whsec_test_secret"
# Signed by the server's own signPayload (src/lib/webhook/deliver.ts), not by this file.
SERVER_BODY = '{"type":"swarm.incident.opened","created_at":"2026-11-21T12:00:00.000Z","data":{"id":"swi_0123456789abcdefghij","severity":"critical","title":"Décoy key tried"}}'
SERVER_T = 1795262400
SERVER_HEADER = "t=1795262400,v1=f53721c20cd4fd549dedd68362fe2d84e6e199c56b6aa13989040b18300385ec"


def sign(secret: str, t: int, body: str) -> str:
    return f"t={t},v1={hmac.new(secret.encode(), f'{t}.{body}'.encode(), hashlib.sha256).hexdigest()}"


def test_webhook_accepts_what_the_server_signed():
    assert verify_radar_webhook(SECRET, SERVER_HEADER, SERVER_BODY, now=SERVER_T) is True
    assert verify_radar_webhook(SECRET, SERVER_HEADER, SERVER_BODY.encode("utf-8"), now=SERVER_T + 299) is True
    assert sign(SECRET, SERVER_T, SERVER_BODY) == SERVER_HEADER


def test_webhook_uses_the_clock_when_no_time_is_given():
    body = '{"type":"swarm.incident.updated"}'
    assert verify_radar_webhook(SECRET, sign(SECRET, int(time.time()), body), body) is True
    assert verify_radar_webhook(SECRET, sign(SECRET, int(time.time()) - 3600, body), body) is False


def test_webhook_refuses_a_wrong_secret():
    assert verify_radar_webhook("whsec_other", SERVER_HEADER, SERVER_BODY, now=SERVER_T) is False
    assert verify_radar_webhook("", SERVER_HEADER, SERVER_BODY, now=SERVER_T) is False


def test_webhook_refuses_a_changed_body():
    assert verify_radar_webhook(SECRET, SERVER_HEADER, SERVER_BODY.replace("critical", "low"), now=SERVER_T) is False
    assert verify_radar_webhook(SECRET, SERVER_HEADER, SERVER_BODY + " ", now=SERVER_T) is False
    # A signature moved to another time is a changed message too.
    assert verify_radar_webhook(SECRET, SERVER_HEADER.replace("t=1795262400", "t=1795262401"), SERVER_BODY, now=SERVER_T) is False


def test_webhook_refuses_a_stale_timestamp():
    assert verify_radar_webhook(SECRET, SERVER_HEADER, SERVER_BODY, now=SERVER_T + 300) is True
    assert verify_radar_webhook(SECRET, SERVER_HEADER, SERVER_BODY, now=SERVER_T + 301) is False
    assert verify_radar_webhook(SECRET, SERVER_HEADER, SERVER_BODY, now=SERVER_T - 301) is False
    assert verify_radar_webhook(SECRET, SERVER_HEADER, SERVER_BODY, tolerance_seconds=60, now=SERVER_T + 61) is False
    assert verify_radar_webhook(SECRET, SERVER_HEADER, SERVER_BODY, tolerance_seconds=3600, now=SERVER_T + 3000) is True


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "garbage",
        "t=1795262400",
        "v1=f53721c20cd4fd549dedd68362fe2d84e6e199c56b6aa13989040b18300385ec",
        "t=,v1=f53721c20cd4fd549dedd68362fe2d84e6e199c56b6aa13989040b18300385ec",
        "t=soon,v1=f53721c20cd4fd549dedd68362fe2d84e6e199c56b6aa13989040b18300385ec",
        "t=-1795262400,v1=f53721c20cd4fd549dedd68362fe2d84e6e199c56b6aa13989040b18300385ec",
        "t=1795262400.5,v1=f53721c20cd4fd549dedd68362fe2d84e6e199c56b6aa13989040b18300385ec",
        "t=" + "9" * 5000 + ",v1=f53721c20cd4fd549dedd68362fe2d84e6e199c56b6aa13989040b18300385ec",
        "t=1795262400,v1=f53721c2",
        "t=1795262400,v1=" + "z" * 64,
        "t=1795262400,v1=F53721C20CD4FD549DEDD68362FE2D84E6E199C56B6AA13989040B18300385EC",
        "t=1795262400,v2=f53721c20cd4fd549dedd68362fe2d84e6e199c56b6aa13989040b18300385ec",
        "t=1795262400;v1=f53721c20cd4fd549dedd68362fe2d84e6e199c56b6aa13989040b18300385ec",
        b"t=1795262400,v1=f53721c20cd4fd549dedd68362fe2d84e6e199c56b6aa13989040b18300385ec",
    ],
)
def test_webhook_refuses_a_malformed_header(header):
    assert verify_radar_webhook(SECRET, header, SERVER_BODY, now=SERVER_T) is False
