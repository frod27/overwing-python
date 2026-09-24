"""Overwing guardrails for LangChain.

Two ways to use it:

1. A runnable you pipe after your model. It scores the model's answer and,
   on `fail`, raises or replaces it. This is the one to reach for.

       from overwing.langchain import overwing_guard
       chain = prompt | llm | overwing_guard(on_fail="replace")

2. A callback handler that scores every LLM output (and optionally every
   prompt) as it happens. Use it to log verdicts across an application or to
   abort a run on `fail` without changing the chain.

       from overwing.langchain import OverwingCallbackHandler
       llm.invoke("...", config={"callbacks": [OverwingCallbackHandler()]})
"""

from __future__ import annotations

import json
from typing import Any, Callable, Literal
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler, BaseCallbackHandler
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import LLMResult
from langchain_core.runnables import RunnableLambda

from ._client import AsyncOverwing, Overwing
from ._errors import OverwingError
from ._types import Evaluation

Action = Literal["raise", "replace", "annotate"]
DEFAULT_REPLACEMENT = "I can't share that response."


class OverwingGuardrailError(Exception):
    """Raised when a verdict trips the configured action."""

    def __init__(self, evaluation: Evaluation, phase: str) -> None:
        detail = ", ".join(f"{r.rule}={r.verdict}" for r in evaluation.results if r.verdict != "pass") or "no rule detail"
        super().__init__(f"Overwing {evaluation.verdict.upper()} on {phase} ({detail}) · {evaluation.id}")
        self.evaluation = evaluation
        self.phase = phase


def text_of(value: Any) -> str:
    """Text from a string, a message, a list of messages, or anything else LangChain hands us."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, BaseMessage):
        content = value.content
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            return "\n".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content).strip()
        return str(content).strip()
    if isinstance(value, list):
        return "\n".join(t for t in (text_of(v) for v in value) if t).strip()
    if value is None:
        return ""
    try:
        return json.dumps(value, default=str)
    except Exception:  # noqa: BLE001
        return str(value)


def _decide(evaluation: Evaluation, on_fail: Action, on_review: Action) -> Action | Literal["pass"]:
    if evaluation.verdict == "fail":
        return on_fail
    if evaluation.verdict == "review":
        return on_review
    return "pass"


def _annotate(value: Any, evaluation: Evaluation) -> Any:
    meta = {
        "id": evaluation.id,
        "verdict": evaluation.verdict,
        "aggregate_score": evaluation.aggregate_score,
        "confidence": evaluation.confidence,
        "failed_rules": evaluation.failed_rules,
        "review_rules": evaluation.review_rules,
    }
    if isinstance(value, BaseMessage):
        value.response_metadata = {**(value.response_metadata or {}), "overwing": meta}
    return value


def _replace(value: Any, replacement: str) -> Any:
    if isinstance(value, BaseMessage):
        return AIMessage(content=replacement, response_metadata={**(value.response_metadata or {})})
    return replacement


def overwing_guard(
    *,
    client: Overwing | None = None,
    rule_set: str = "content-safety",
    on_fail: Action = "raise",
    on_review: Action = "annotate",
    replacement: str | Callable[[Evaluation], str] = DEFAULT_REPLACEMENT,
    metadata: dict[str, Any] | None = None,
    on_verdict: Callable[[Evaluation, str], None] | None = None,
    fail_open: bool = False,
) -> RunnableLambda:
    """A runnable that scores whatever flows through it (string, AIMessage, or list) and acts on the verdict.

    - fail    -> `on_fail`: "raise" (default) raises OverwingGuardrailError; "replace" swaps the text; "annotate" passes it through with the verdict on `response_metadata["overwing"]`.
    - review  -> `on_review`: "annotate" (default) | "raise" | "replace".
    - pass    -> passed through, annotated when the value is a message.
    """
    ow = client or Overwing()

    def run(value: Any) -> Any:
        text = text_of(value)
        if not text:
            return value
        try:
            evaluation = ow.evaluate(text, rule_set=rule_set, metadata={**(metadata or {}), "phase": "output", "source": "langchain"})
        except OverwingError:
            if fail_open:
                return value
            raise
        if on_verdict:
            on_verdict(evaluation, "output")
        action = _decide(evaluation, on_fail, on_review)
        if action == "raise":
            raise OverwingGuardrailError(evaluation, "output")
        if action == "replace":
            return _annotate(_replace(value, replacement(evaluation) if callable(replacement) else replacement), evaluation)
        return _annotate(value, evaluation)

    return RunnableLambda(run, name="overwing_guard")


class OverwingCallbackHandler(BaseCallbackHandler):
    """Scores every LLM output (and prompts when `check_input=True`).

    On `fail` the handler raises OverwingGuardrailError, which aborts the run
    because `raise_error` is True. Set `on_fail="log"` to only record verdicts.
    """

    raise_error = True

    def __init__(
        self,
        *,
        client: Overwing | None = None,
        rule_set: str = "content-safety",
        on_fail: Literal["raise", "log"] = "raise",
        on_review: Literal["raise", "log"] = "log",
        check_input: bool = False,
        metadata: dict[str, Any] | None = None,
        on_verdict: Callable[[Evaluation, str], None] | None = None,
        fail_open: bool = False,
    ) -> None:
        super().__init__()
        self.client = client or Overwing()
        self.rule_set = rule_set
        self.on_fail = on_fail
        self.on_review = on_review
        self.check_input = check_input
        self.metadata = metadata or {}
        self.on_verdict = on_verdict
        self.fail_open = fail_open
        self.verdicts: list[tuple[str, Evaluation]] = []

    def _score(self, text: str, phase: str) -> None:
        if not text:
            return
        try:
            evaluation = self.client.evaluate(text, rule_set=self.rule_set, metadata={**self.metadata, "phase": phase, "source": "langchain-callback"})
        except OverwingError:
            if self.fail_open:
                return
            raise
        self.verdicts.append((phase, evaluation))
        if self.on_verdict:
            self.on_verdict(evaluation, phase)
        if (evaluation.verdict == "fail" and self.on_fail == "raise") or (evaluation.verdict == "review" and self.on_review == "raise"):
            raise OverwingGuardrailError(evaluation, phase)

    def on_llm_start(self, serialized: dict[str, Any], prompts: list[str], *, run_id: UUID, **kwargs: Any) -> None:
        if self.check_input:
            self._score("\n".join(prompts).strip(), "input")

    def on_chat_model_start(self, serialized: dict[str, Any], messages: list[list[BaseMessage]], *, run_id: UUID, **kwargs: Any) -> None:
        if self.check_input:
            last = [m for batch in messages for m in batch if getattr(m, "type", "") == "human"]
            self._score(text_of(last[-1]) if last else "", "input")

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        for batch in response.generations:
            for gen in batch:
                self._score(gen.text.strip() if gen.text else text_of(getattr(gen, "message", None)), "output")


class AsyncOverwingCallbackHandler(AsyncCallbackHandler):
    """Async twin of OverwingCallbackHandler."""

    raise_error = True

    def __init__(
        self,
        *,
        client: AsyncOverwing | None = None,
        rule_set: str = "content-safety",
        on_fail: Literal["raise", "log"] = "raise",
        on_review: Literal["raise", "log"] = "log",
        check_input: bool = False,
        metadata: dict[str, Any] | None = None,
        on_verdict: Callable[[Evaluation, str], None] | None = None,
        fail_open: bool = False,
    ) -> None:
        super().__init__()
        self.client = client or AsyncOverwing()
        self.rule_set = rule_set
        self.on_fail = on_fail
        self.on_review = on_review
        self.check_input = check_input
        self.metadata = metadata or {}
        self.on_verdict = on_verdict
        self.fail_open = fail_open
        self.verdicts: list[tuple[str, Evaluation]] = []

    async def _score(self, text: str, phase: str) -> None:
        if not text:
            return
        try:
            evaluation = await self.client.evaluate(text, rule_set=self.rule_set, metadata={**self.metadata, "phase": phase, "source": "langchain-callback"})
        except OverwingError:
            if self.fail_open:
                return
            raise
        self.verdicts.append((phase, evaluation))
        if self.on_verdict:
            self.on_verdict(evaluation, phase)
        if (evaluation.verdict == "fail" and self.on_fail == "raise") or (evaluation.verdict == "review" and self.on_review == "raise"):
            raise OverwingGuardrailError(evaluation, phase)

    async def on_llm_start(self, serialized: dict[str, Any], prompts: list[str], *, run_id: UUID, **kwargs: Any) -> None:
        if self.check_input:
            await self._score("\n".join(prompts).strip(), "input")

    async def on_chat_model_start(self, serialized: dict[str, Any], messages: list[list[BaseMessage]], *, run_id: UUID, **kwargs: Any) -> None:
        if self.check_input:
            last = [m for batch in messages for m in batch if getattr(m, "type", "") == "human"]
            await self._score(text_of(last[-1]) if last else "", "input")

    async def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        for batch in response.generations:
            for gen in batch:
                await self._score(gen.text.strip() if gen.text else text_of(getattr(gen, "message", None)), "output")
