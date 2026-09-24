"""Overwing guardrails for the OpenAI Agents SDK (Python).

    from agents import Agent, Runner
    from overwing.openai_agents import overwing_input_guardrail, overwing_output_guardrail

    agent = Agent(
        name="Support",
        instructions="Help the customer.",
        input_guardrails=[overwing_input_guardrail()],
        output_guardrails=[overwing_output_guardrail()],
    )

A tripped guardrail makes the SDK raise InputGuardrailTripwireTriggered or
OutputGuardrailTripwireTriggered; the Overwing evaluation is on
``exc.guardrail_result.output.output_info["evaluation"]``.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Literal

from agents import Agent, GuardrailFunctionOutput, InputGuardrail, OutputGuardrail, RunContextWrapper

from ._client import AsyncOverwing
from ._errors import OverwingError
from ._types import Evaluation

TripOn = Literal["fail", "fail-or-review"]


def text_from_input(value: Any) -> str:
    """Pull user-facing text out of the SDK's input shape (a string or a list of input items)."""
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, list):
        return ""
    texts: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        if role is not None and role != "user":
            continue
        content = item.get("content")
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str) and part.get("type") in (None, "input_text", "text"):
                    texts.append(part["text"])
        elif isinstance(item.get("text"), str):
            texts.append(item["text"])
    return "\n".join(texts).strip()


def text_from_output(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    for attr in ("model_dump", "dict"):
        fn = getattr(value, attr, None)
        if callable(fn):
            try:
                return json.dumps(fn(), default=str)
            except Exception:  # noqa: BLE001
                break
    try:
        return json.dumps(value, default=str)
    except Exception:  # noqa: BLE001
        return str(value)


class _Runner:
    def __init__(self, *, client: AsyncOverwing | None, rule_set: str, trip_on: TripOn, metadata: dict[str, Any] | Callable[[], dict[str, Any]] | None, on_verdict: Callable[[Evaluation, str], None] | None, fail_open: bool, context: dict[str, Any] | Callable[[], dict[str, Any]] | None = None) -> None:
        self.client = client or AsyncOverwing()
        self.rule_set = rule_set
        self.trip_on = trip_on
        self.metadata = metadata
        self.context = context
        self.on_verdict = on_verdict
        self.fail_open = fail_open

    async def run(self, text: str, phase: str) -> GuardrailFunctionOutput:
        if not text:
            return GuardrailFunctionOutput(output_info={"evaluation": None, "skipped": "empty"}, tripwire_triggered=False)
        meta = self.metadata() if callable(self.metadata) else dict(self.metadata or {})
        meta.update({"phase": phase, "source": "openai-agents"})
        try:
            ctx = self.context() if callable(self.context) else self.context
            evaluation = await self.client.evaluate(text, rule_set=self.rule_set, metadata=meta, context=ctx)
        except OverwingError:
            if self.fail_open:
                return GuardrailFunctionOutput(output_info={"evaluation": None, "skipped": "unreachable"}, tripwire_triggered=False)
            raise
        if self.on_verdict:
            self.on_verdict(evaluation, phase)
        tripped = evaluation.verdict == "fail" or (self.trip_on == "fail-or-review" and evaluation.verdict == "review")
        return GuardrailFunctionOutput(output_info={"evaluation": evaluation}, tripwire_triggered=tripped)


def overwing_input_guardrail(
    *,
    client: AsyncOverwing | None = None,
    rule_set: str = "content-safety",
    trip_on: TripOn = "fail",
    name: str = "overwing-input",
    metadata: dict[str, Any] | Callable[[], dict[str, Any]] | None = None,
    on_verdict: Callable[[Evaluation, str], None] | None = None,
    fail_open: bool = False,
    run_in_parallel: bool = True,
    context: dict[str, Any] | Callable[[], dict[str, Any]] | None = None,
) -> InputGuardrail[Any]:
    """Scores the user's input before (or alongside) the agent run. `context` carries facts the rules may reference."""
    runner = _Runner(client=client, rule_set=rule_set, trip_on=trip_on, metadata=metadata, on_verdict=on_verdict, fail_open=fail_open, context=context)

    async def guardrail(ctx: RunContextWrapper[Any], agent: Agent[Any], input: Any) -> GuardrailFunctionOutput:  # noqa: A002
        return await runner.run(text_from_input(input), "input")

    return InputGuardrail(guardrail_function=guardrail, name=name, run_in_parallel=run_in_parallel)


def overwing_output_guardrail(
    *,
    client: AsyncOverwing | None = None,
    rule_set: str = "content-safety",
    trip_on: TripOn = "fail",
    name: str = "overwing-output",
    metadata: dict[str, Any] | Callable[[], dict[str, Any]] | None = None,
    on_verdict: Callable[[Evaluation, str], None] | None = None,
    fail_open: bool = False,
    context: dict[str, Any] | Callable[[], dict[str, Any]] | None = None,
) -> OutputGuardrail[Any]:
    """Scores the agent's final output before it is returned. `context` carries facts the rules may reference."""
    runner = _Runner(client=client, rule_set=rule_set, trip_on=trip_on, metadata=metadata, on_verdict=on_verdict, fail_open=fail_open, context=context)

    async def guardrail(ctx: RunContextWrapper[Any], agent: Agent[Any], output: Any) -> GuardrailFunctionOutput:
        return await runner.run(text_from_output(output), "output")

    return OutputGuardrail(guardrail_function=guardrail, name=name)
