from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from overwing import Overwing
from overwing.langchain import OverwingCallbackHandler, OverwingGuardrailError, overwing_guard, text_of
from tests.conftest import fake_evaluation


def test_text_of():
    assert text_of("  hi ") == "hi"
    assert text_of(AIMessage(content="msg")) == "msg"
    assert text_of(AIMessage(content=[{"type": "text", "text": "a"}, {"type": "text", "text": "b"}])) == "a\nb"
    assert text_of([HumanMessage(content="x"), AIMessage(content="y")]) == "x\ny"


def test_guard_passes_and_annotates(scripted):
    s = scripted([(200, fake_evaluation("pass"), None)])
    guard = overwing_guard(client=Overwing("k", transport=s.transport()), metadata={"user": "u1"})
    out = guard.invoke(AIMessage(content="All good."))
    assert isinstance(out, AIMessage) and out.content == "All good."
    assert out.response_metadata["overwing"]["verdict"] == "pass"
    assert s.body()["metadata"] == {"user": "u1", "phase": "output", "source": "langchain"}


def test_guard_raises_on_fail_by_default(scripted):
    s = scripted([(200, fake_evaluation("fail"), None)])
    guard = overwing_guard(client=Overwing("k", transport=s.transport()))
    with pytest.raises(OverwingGuardrailError) as ei:
        guard.invoke("you idiot")
    assert ei.value.evaluation.failed_rules == ["toxicity"] and ei.value.phase == "output"


def test_guard_replace_and_review_escalation(scripted):
    s = scripted([(200, fake_evaluation("fail"), None)])
    guard = overwing_guard(client=Overwing("k", transport=s.transport()), on_fail="replace", replacement=lambda e: f"Blocked ({e.id})")
    out = guard.invoke(AIMessage(content="bad"))
    assert out.content == "Blocked (eval_0000000000000001)" and out.response_metadata["overwing"]["verdict"] == "fail"
    s2 = scripted([(200, fake_evaluation("review"), None)])
    strict = overwing_guard(client=Overwing("k", transport=s2.transport()), on_review="raise")
    with pytest.raises(OverwingGuardrailError):
        strict.invoke("hmm")


def test_guard_skips_empty_and_composes(scripted):
    s = scripted([(200, fake_evaluation("pass"), None)])
    from langchain_core.runnables import RunnableLambda
    chain = RunnableLambda(lambda x: AIMessage(content=f"Echo: {x}")) | overwing_guard(client=Overwing("k", transport=s.transport()))
    assert chain.invoke("hello").content == "Echo: hello"
    assert overwing_guard(client=Overwing("k", transport=s.transport())).invoke("") == ""


def test_callback_scores_outputs_and_raises(scripted):
    s = scripted([(200, fake_evaluation("pass"), None), (200, fake_evaluation("fail"), None)])
    h = OverwingCallbackHandler(client=Overwing("k", transport=s.transport()))
    assert h.raise_error is True
    ok = LLMResult(generations=[[ChatGeneration(message=AIMessage(content="fine"))]])
    h.on_llm_end(ok, run_id=uuid4())
    assert h.verdicts[0][1].verdict == "pass"
    bad = LLMResult(generations=[[ChatGeneration(message=AIMessage(content="you idiot"))]])
    with pytest.raises(OverwingGuardrailError):
        h.on_llm_end(bad, run_id=uuid4())


def test_callback_checks_input_when_enabled(scripted):
    s = scripted([(200, fake_evaluation("fail"), None)])
    h = OverwingCallbackHandler(client=Overwing("k", transport=s.transport()), check_input=True)
    with pytest.raises(OverwingGuardrailError) as ei:
        h.on_chat_model_start({}, [[HumanMessage(content="Reach me at dana@example.com")]], run_id=uuid4())
    assert ei.value.phase == "input"
    assert s.body()["input"] == "Reach me at dana@example.com"
    quiet = OverwingCallbackHandler(client=Overwing("k", transport=scripted([(200, fake_evaluation("fail"), None)]).transport()), on_fail="log")
    quiet.on_llm_end(LLMResult(generations=[[ChatGeneration(message=AIMessage(content="x"))]]), run_id=uuid4())
    assert quiet.verdicts[0][1].verdict == "fail"


def test_guard_honors_redact_and_forwards_context(scripted):
    s = scripted([(200, fake_evaluation("fail", recommended="redact"), None)])
    guard = overwing_guard(client=Overwing("k", transport=s.transport()), context={"recipient": "customer"})
    out = guard.invoke(AIMessage(content="call me at 555-0142"))
    assert out.content == "I can't share that response."
    assert out.response_metadata["overwing"]["recommended_action"] == "redact"
    assert s.body()["context"] == {"recipient": "customer"}
    strict = overwing_guard(client=Overwing("k", transport=scripted([(200, fake_evaluation("fail", recommended="redact"), None)]).transport()), honor_actions=False)
    with pytest.raises(OverwingGuardrailError):
        strict.invoke("call me")
