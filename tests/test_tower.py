import pytest

from overwing import AsyncOverwing, AsyncTower, Overwing, OverwingError, Tower

ACTION_ID = "7beeb669-ddc0-4b0c-b358-fc41e0181b9d"


def decision(outcome="auto", score=0.94):
    return {"decision_id": "d1", "outcome": outcome, "score": score, "reason": f"ruled {outcome}", "provider": "jev", "thresholds": {"auto": 0.85, "review": 0.45}, "results": [{"question_id": "intent", "answer": "new_order", "passed": True, "probability": 1, "confidence": 1, "gated": False}], "checks": [{"check": "credit_limit", "passed": outcome != "reject", "detail": "over" if outcome == "reject" else None}], "latency_ms": 200}


def action(status, **extra):
    outcome = "reject" if status == "rejected" else "review" if status == "pending" else "auto"
    return {"action_id": ACTION_ID, "operation": "create_order", "status": status, "dry_run": False, "idempotency_key": "k1", "decision": decision(outcome), "result": None, "error": None, **extra}


def test_requires_agent_key(monkeypatch):
    monkeypatch.delenv("OVERWING_AGENT_KEY", raising=False)
    with pytest.raises(OverwingError):
        Tower()


def test_submit_sends_agent_key_and_idempotency_key(scripted):
    s = scripted([(200, action("executed", result={"order_number": "SO1"}), None)])
    with Tower("ow_agent_test", base_url="https://example.test", transport=s.transport()) as tower:
        a = tower.submit("create_order", {"customer_id": "C1"}, idempotency_key="k1")
    assert a.executed and a.result == {"order_number": "SO1"} and not a.replayed
    assert a.decision is not None and a.decision.outcome == "auto" and a.decision.score == 0.94
    assert str(s.calls[0].url) == "https://example.test/api/v1/tower/actions"
    assert s.calls[0].headers["authorization"] == "Bearer ow_agent_test"
    assert s.body() == {"operation": "create_order", "input": {"customer_id": "C1"}, "idempotency_key": "k1"}


def test_dry_run_flag(scripted):
    s = scripted([(200, action("approved", dry_run=True), None)])
    a = Tower("k", transport=s.transport()).submit("create_order", {}, idempotency_key="k2", dry_run=True)
    assert a.dry_run and s.body()["dry_run"] is True


def test_idempotency_key_is_required(scripted):
    s = scripted([(200, action("executed"), None)])
    with pytest.raises(OverwingError):
        Tower("k", transport=s.transport()).submit("create_order", {}, idempotency_key="")
    assert s.calls == []


def test_pending_and_rejected_return_instead_of_raising(scripted):
    s = scripted([(202, action("pending", review_id="rv1"), None), (422, action("rejected", error={"code": "rejected", "message": "over credit limit", "retryable": False}), None)])
    tower = Tower("k", transport=s.transport(), max_retries=0)
    pending = tower.submit("create_order", {}, idempotency_key="a")
    assert pending.pending and pending.review_id == "rv1"
    rejected = tower.submit("create_order", {}, idempotency_key="b")
    assert rejected.rejected and rejected.decision is not None and rejected.decision.failed_checks[0]["check"] == "credit_limit"


def test_typed_error(scripted):
    s = scripted([(403, {"error": {"code": "forbidden_scope", "field": "operation", "message": "This agent is not scoped to 'update_order'", "retryable": False, "suggested_fix": "Ask the organization to add the operation to the agent's scopes"}}, None)])
    with pytest.raises(OverwingError) as ei:
        Tower("k", transport=s.transport()).submit("update_order", {}, idempotency_key="c")
    e = ei.value
    assert e.status == 403 and e.code == "forbidden_scope" and e.field == "operation" and e.retryable is False
    assert "add the operation" in (e.suggested_fix or "") and "not scoped" in str(e)


def test_wait_for_review_polls_until_resolved(scripted, monkeypatch):
    monkeypatch.setattr("overwing._tower.time.sleep", lambda _s: None)
    s = scripted([(200, action("pending"), None), (200, action("executed"), None)])
    a = Tower("k", transport=s.transport()).wait_for_review(ACTION_ID, interval=1, timeout=30)
    assert a.executed and len(s.calls) == 2


def test_replay_and_decision_id_only(scripted):
    body = action("compensated", replayed=True)
    body["decision"] = {"decision_id": "d1"}
    s = scripted([(200, body, None)])
    a = Tower("k", transport=s.transport()).compensate(ACTION_ID)
    assert a.status == "compensated" and a.replayed and a.decision is None
    assert s.calls[0].url.path == f"/api/v1/tower/actions/{ACTION_ID}/compensate"


def test_verify_range(scripted):
    s = scripted([(200, {"ok": True, "checked": 4, "first_break": None, "from": 3, "to": 6, "signing_key_ids": ["k"], "latest_sequence": 6}, None)])
    r = Tower("k", base_url="https://example.test", transport=s.transport()).verify_receipts(start=3)
    assert r["ok"] and str(s.calls[0].url) == "https://example.test/api/v1/tower/receipts/verify?from=3"


def test_setup_creates_agent_and_ready_client(scripted):
    s = scripted([
        (201, {"agent_id": "a1", "name": "order-bot", "scopes": ["create_order"], "status": "active", "key": "ow_agent_minted", "key_shown_once": True}, None),
        (200, {"agent": {"id": "a1", "name": "order-bot", "scopes": ["create_order"]}, "operations": []}, None),
        (200, {"agent_id": "a1", "status": "revoked"}, None),
    ])
    ow = Overwing("ow_live_test", base_url="https://example.test", transport=s.transport())
    agent, tower = ow.tower_agent("order-bot", ["create_order"])
    assert agent.key == "ow_agent_minted" and "ow_agent_minted" not in repr(agent)
    assert s.calls[0].headers["authorization"] == "Bearer ow_live_test"
    assert s.body() == {"name": "order-bot", "scopes": ["create_order"]}
    tower.capabilities()
    assert s.calls[1].headers["authorization"] == "Bearer ow_agent_minted"
    assert str(s.calls[1].url) == "https://example.test/api/v1/tower/capabilities"
    ow.tower_revoke_agent("a1")
    assert s.calls[2].method == "DELETE" and s.calls[2].url.path == "/api/v1/tower/agents/a1"


async def test_async_tower_and_setup(scripted):
    s = scripted([(201, {"agent_id": "a1", "name": "n", "scopes": ["*"], "status": "active", "key": "ow_agent_minted"}, None), (202, action("pending", review_id="rv1"), None)])
    async with AsyncOverwing("ow_live_test", transport=s.transport()) as ow:
        agent, tower = await ow.tower_agent("n", ["*"])
    assert isinstance(tower, AsyncTower) and agent.scopes == ["*"]
    a = await tower.submit("create_order", {}, idempotency_key="k")
    assert a.pending and s.calls[1].headers["authorization"] == "Bearer ow_agent_minted"
    await tower.aclose()
