import pytest
from agents import Agent, InputGuardrail, OutputGuardrail, RunContextWrapper

from overwing import AsyncOverwing, OverwingError
from overwing.openai_agents import overwing_input_guardrail, overwing_output_guardrail, text_from_input, text_from_output
from tests.conftest import fake_evaluation

ctx = RunContextWrapper(context=None)
agent = Agent(name="t")


def test_text_extraction():
    assert text_from_input("  hi ") == "hi"
    assert text_from_input([{"role": "system", "content": "x"}, {"role": "user", "content": [{"type": "input_text", "text": "a"}, {"type": "input_text", "text": "b"}]}]) == "a\nb"
    assert text_from_input([{"role": "user", "content": "plain"}]) == "plain"
    assert text_from_input(42) == ""
    assert text_from_output({"reply": "x"}) == '{"reply": "x"}'


async def test_input_guardrail_trips_on_fail(scripted):
    s = scripted([(200, fake_evaluation("fail"), None)])
    g = overwing_input_guardrail(client=AsyncOverwing("k", transport=s.transport()), metadata={"user": "u1"})
    assert isinstance(g, InputGuardrail) and g.get_name() == "overwing-input"
    r = await g.run(agent, "you idiot", ctx)
    assert r.output.tripwire_triggered is True
    assert r.output.output_info["evaluation"].verdict == "fail"
    assert s.body()["metadata"] == {"user": "u1", "phase": "input", "source": "openai-agents"}


async def test_review_trips_only_when_asked(scripted):
    a = overwing_input_guardrail(client=AsyncOverwing("k", transport=scripted([(200, fake_evaluation("review"), None)]).transport()))
    assert (await a.run(agent, "hmm", ctx)).output.tripwire_triggered is False
    b = overwing_input_guardrail(client=AsyncOverwing("k", transport=scripted([(200, fake_evaluation("review"), None)]).transport()), trip_on="fail-or-review")
    assert (await b.run(agent, "hmm", ctx)).output.tripwire_triggered is True


async def test_output_guardrail_scores_text(scripted):
    s = scripted([(200, fake_evaluation("pass"), None)])
    g = overwing_output_guardrail(client=AsyncOverwing("k", transport=s.transport()))
    assert isinstance(g, OutputGuardrail)
    r = await g.run(ctx, agent, "All good.")
    assert r.output.tripwire_triggered is False
    assert s.body()["input"] == "All good."


async def test_empty_and_fail_open(scripted):
    s = scripted([(500, {"error": "boom"}, None)])
    strict = overwing_output_guardrail(client=AsyncOverwing("k", transport=s.transport(), max_retries=0))
    r = await strict.run(ctx, agent, "")
    assert r.output.output_info["skipped"] == "empty" and len(s.calls) == 0
    with pytest.raises(OverwingError):
        await strict.run(ctx, agent, "text")
    open_ = overwing_output_guardrail(client=AsyncOverwing("k", transport=scripted([(500, {"error": "boom"}, None)]).transport(), max_retries=0), fail_open=True)
    r = await open_.run(ctx, agent, "text")
    assert r.output.tripwire_triggered is False and r.output.output_info["skipped"] == "unreachable"
