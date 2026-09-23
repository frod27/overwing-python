<p align="center">
  <a href="https://overwing.ai">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/frod27/overwing-python/main/assets/wordmark-dark.svg">
      <img src="https://raw.githubusercontent.com/frod27/overwing-python/main/assets/wordmark.svg" alt="Overwing" width="220">
    </picture>
  </a>
</p>

<p align="center"><strong>Guardrails for LLM output, in one line.</strong><br>
OpenAI Agents SDK guardrails and a typed client. Every message gets a <code>pass</code> / <code>fail</code> / <code>review</code> verdict with calibrated confidence before it reaches your user.</p>

<p align="center">
  <a href="https://pypi.org/project/overwing/"><img alt="PyPI" src="https://img.shields.io/pypi/v/overwing?color=0B1220&label=overwing"></a>
  <a href="https://github.com/frod27/overwing-python/actions"><img alt="CI" src="https://github.com/frod27/overwing-python/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://overwing.ai/docs"><img alt="API reference" src="https://img.shields.io/badge/API-reference-0B1220"></a>
  <a href="https://overwing.ai"><img alt="agents welcome" src="https://overwing.ai/badge.svg"></a>
</p>

---

```bash
pip install "overwing[agents]"     # or: uv add "overwing[agents]"
```

Get a free API key at [overwing.ai](https://overwing.ai/login) (250 evaluations a day), or let your agent sign itself up with one `POST` to `/api/v1/signup`. Try it first with no key: paste anything into the console at [overwing.ai](https://overwing.ai).

## OpenAI Agents SDK guardrails

```python
from agents import Agent, Runner, InputGuardrailTripwireTriggered, OutputGuardrailTripwireTriggered
from overwing.openai_agents import overwing_input_guardrail, overwing_output_guardrail

agent = Agent(
    name="Support",
    instructions="Help the customer.",
    input_guardrails=[overwing_input_guardrail()],     # scores the user's message
    output_guardrails=[overwing_output_guardrail()],   # scores the agent's final answer
)

try:
    result = await Runner.run(agent, "Reach me at dana@example.com to sort out the refund.")
except (InputGuardrailTripwireTriggered, OutputGuardrailTripwireTriggered) as exc:
    evaluation = exc.guardrail_result.output.output_info["evaluation"]
    print(evaluation.verdict, evaluation.failed_rules)   # "fail" ["pii_detected"]
```

Both accept `rule_set`, `trip_on="fail" | "fail-or-review"`, `metadata`, `on_verdict`, and `fail_open`. Input guardrails run in parallel with the agent by default; pass `run_in_parallel=False` to block before the model is called. Reads `OVERWING_API_KEY` from the environment, or pass `client=AsyncOverwing(api_key=...)`.

## Client

```python
from overwing import Overwing, AsyncOverwing

ow = Overwing()   # or Overwing(api_key="ow_live_...")

e = ow.evaluate("Reach me at dana@example.com to sort out the refund.")
e.verdict            # "fail"
e.failed_rules       # ["pii_detected"]
e.results[1]         # RuleResult(rule="pii_detected", answer=True, confidence=0.98, verdict="fail", ...)

batch = ow.evaluate_batch([{"id": "a", "input": "..."}, {"id": "b", "input": "..."}])
ow.create_rule_set(name="Support tone", slug="support-tone", rules=[...])
ow.usage()

async with AsyncOverwing() as aow:
    e = await aow.evaluate("...")
```

`OverwingError` carries `status` and `retry_after_seconds`. 429s with a short `Retry-After` and 5xx are retried automatically. Pass `idempotency_key=` to make retries safe. Python 3.10+.

## How verdicts work

Each rule has a fail condition, an optional review threshold, and a weight. The prebuilt `content-safety` set checks toxicity, personal data, self-harm, sexual content, and severity. **fail** means a rule matched. **review** means a rule was unsure. **pass** is everything else. Full guide: [overwing.ai/llms.txt](https://overwing.ai/llms.txt). Reference: [overwing.ai/docs](https://overwing.ai/docs).

## Also from Overwing

- [`overwing`](https://github.com/frod27/overwing-js) on npm: the same client, a Vercel AI SDK middleware, and Agents SDK guardrails for JavaScript.
- [`overwing-mcp`](https://github.com/frod27/overwing-mcp): the guardrails as MCP tools for Claude, Cursor, and any MCP client.

MIT © Overwing. Verdicts are produced by TypeSafe's Jev System One model; Overwing is not affiliated with TypeSafe.
