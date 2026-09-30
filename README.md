<p align="center">
  <a href="https://overwing.ai">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/frod27/overwing-python/main/assets/wordmark-dark.svg">
      <img src="https://raw.githubusercontent.com/frod27/overwing-python/main/assets/wordmark.svg" alt="Overwing" width="220">
    </picture>
  </a>
</p>

<p align="center"><strong>Guardrails for LLM output, in one line.</strong><br>
OpenAI Agents SDK guardrails, LangChain runnables and callbacks, and a typed client. Also: <a href="#atlas-who-is-this-user-agent-no-key-needed">Atlas</a> user-agent lookups with no key, and <a href="#tower-let-an-agent-operate-a-legacy-system">Tower</a> for agents operating legacy systems. Every message gets a <code>pass</code> / <code>fail</code> / <code>review</code> verdict with calibrated confidence before it reaches your user.</p>

<p align="center">
  <a href="https://pypi.org/project/overwing/"><img alt="PyPI" src="https://img.shields.io/pypi/v/overwing?color=0B1220&label=overwing"></a>
  <a href="https://github.com/frod27/overwing-python/actions"><img alt="CI" src="https://github.com/frod27/overwing-python/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://overwing.ai/docs"><img alt="API reference" src="https://img.shields.io/badge/API-reference-0B1220"></a>
  <a href="https://overwing.ai"><img alt="agents welcome" src="https://overwing.ai/badge.svg"></a>
</p>

---

```bash
pip install overwing               # client, Atlas, Tower
pip install "overwing[agents]"      # OpenAI Agents SDK guardrails
pip install "overwing[langchain]"   # LangChain guard runnable + callbacks
```

It works with no key: `Overwing().evaluate(text)` runs 10 evaluations a day on inputs up to 2,000 characters, and text sent without a key is not stored. For more, get a free API key at [overwing.ai](https://overwing.ai/login) (250 evaluations a day), or let your agent sign itself up with one `POST` to `/api/v1/signup`.

The text can be in any language. It was tested on 2026-09-29 in Spanish, Portuguese, French, German, Japanese, Simplified Chinese, Korean, Arabic and Hindi: a small test, not a benchmark. Results come back in English.

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

## LangChain

Pipe a guard after your model. It scores the answer and acts on the verdict before anything downstream sees it.

```python
from overwing.langchain import overwing_guard, OverwingGuardrailError

chain = prompt | llm | overwing_guard(on_fail="replace")   # or on_fail="raise" (default) / "annotate"
msg = chain.invoke({"question": "..."})
msg.response_metadata["overwing"]   # {"verdict": "pass", "confidence": 0.97, "failed_rules": [], ...}
```

Or observe every LLM call with a callback handler, which aborts the run on `fail`:

```python
from overwing.langchain import OverwingCallbackHandler

handler = OverwingCallbackHandler(check_input=True)   # also scores the user's prompt
llm.invoke("...", config={"callbacks": [handler]})
handler.verdicts   # [("input", Evaluation), ("output", Evaluation), ...]
```

Both accept `rule_set`, `metadata`, `on_verdict`, and `fail_open`. There is an `AsyncOverwingCallbackHandler` too.

## Atlas: who is this user agent? (no key needed)

[Overwing Atlas](https://overwing.ai/atlas) is a registry of AI crawlers, fetchers and browser agents. Give it a `User-Agent` string and it says what the string claims to be and whether that claim can be trusted.

```python
from overwing import Atlas

atlas = Atlas()   # no key: 10 lookups a day. With OVERWING_API_KEY set: 100, or your Atlas plan's limit.

who = atlas.lookup(request.headers.get("user-agent", ""))
who.identified       # True
who.agent            # "GPTBot"
who.operator         # "OpenAI"
who.purpose_class    # "Training / bulk crawl"
who.verification     # "User-agent string only (spoofable)"
who.signed           # False: the claim is only a string, so treat it as unverified
who.remaining_today  # 9
```

Read `verification` before you act on the claim. `who.signed` is true only when the operator signs its requests with Web Bot Auth, which you can check. When the allowance is spent, `lookup` raises `OverwingError` with `status == 429` and `retry_after_seconds`.

`atlas.agents(purpose=..., operator=..., verification=..., q=..., limit=...)` searches the registry and `atlas.summary()` returns traffic shares and field-scan headlines. `AsyncAtlas` is the async twin. With an API key, `Overwing().atlas_lookup(...)` does the same lookup.

## Tower: let an agent operate a legacy system

[Overwing Tower](https://overwing.ai/products/tower) sits between an agent and a system of record. The agent calls typed operations. Tower rules on each one: execute it, ask a person, or reject it. Every step gets a signed receipt.

There are two keys. The **organization key** sets things up. Each **agent key** is scoped to the operations that agent may call.

```python
from overwing import Overwing, Tower

# Once, as the organization
ow = Overwing()                                   # OVERWING_API_KEY
ow.tower_load_template()                          # starter workflow: email PO to order entry (mock IBM i)
agent = ow.tower_create_agent("order-intake", ["create_order", "cancel_order"])
agent.key                                         # ow_agent_... shown once: store it as OVERWING_AGENT_KEY

# Then, as the agent
tower = Tower()                                   # OVERWING_AGENT_KEY
tower.capabilities()["operations"]                # what you may call, with JSON Schema inputs

action = tower.submit("create_order", order, idempotency_key=email.message_id)

if action.executed:
    action.result                                 # what the system returned
elif action.pending:
    action = tower.wait_for_review(action.action_id)   # a person must approve; do not resubmit
elif action.rejected:
    action.decision.reason                        # why; do not retry unchanged
```

`submit` returns for every ruling and raises only when the request itself is wrong. Those errors are typed for agents: `e.code` (`invalid_input`, `forbidden_scope`, `quota_exceeded`, ...), `e.field`, `e.retryable`, and `e.suggested_fix`.

Use a stable `idempotency_key` per business request. Repeating it returns the original outcome, with `action.replayed` set, instead of acting twice.

Also on the agent client: `decide` (a ruling with no side effects), `submit(..., dry_run=True)`, `get`, `compensate` (undo an executed action), `get_receipt`, `verify_receipts` and `public_key`. On the organization client: `tower_list_agents`, `tower_revoke_agent`, and `tower_agent`, which creates an agent and returns a ready `Tower`. `AsyncTower` and `AsyncOverwing` mirror all of it.

## Client

```python
from overwing import Overwing, AsyncOverwing

ow = Overwing()   # reads OVERWING_API_KEY; with no key, evaluate() uses the free allowance

e = ow.evaluate("Reach me at dana@example.com to sort out the refund.")
e.verdict              # "fail"
e.recommended_action   # "redact": remove the contact details, the message itself is fine
e.failed_rules         # ["pii_detected"]

# Give the rules context and use the context-aware prebuilt set
ok = ow.evaluate(
    "Reach me at dana@example.com to sort out the refund.",
    rule_set="outbound-message",
    context={"recipient": "one known customer", "channel": "email", "owns_contact_info": True},
)
ok.verdict             # "pass": the details are the sender's own, deliberately shared
e.results[1]         # RuleResult(rule="pii_detected", answer=True, confidence=0.98, verdict="fail", ...)

batch = ow.evaluate_batch([{"id": "a", "input": "..."}, {"id": "b", "input": "..."}])
ow.create_rule_set(name="Support tone", slug="support-tone", rules=[...])
ow.usage()

async with AsyncOverwing() as aow:
    e = await aow.evaluate("...")
```

`OverwingError` carries `status` and `retry_after_seconds`, plus `code`, `field`, `retryable` and `suggested_fix` when the API supplies them. 429s with a short `Retry-After` and 5xx are retried automatically. Pass `idempotency_key=` to make retries safe. Python 3.10+.

## How verdicts work

Each rule has a fail condition, an optional review threshold, and a weight. The prebuilt `content-safety` set checks toxicity, personal data, self-harm, sexual content, and severity. **fail** means a rule matched. **review** means a rule was unsure. **pass** is everything else. Full guide: [overwing.ai/llms.txt](https://overwing.ai/llms.txt). Reference: [overwing.ai/docs](https://overwing.ai/docs).

## Also from Overwing

- [`overwing`](https://github.com/frod27/overwing-js) on npm: the same client, a Vercel AI SDK middleware, and Agents SDK guardrails for JavaScript.
- MCP: the same tools for Claude, Cursor and any MCP client, hosted at `https://overwing.ai/mcp` with no install, or from npm as [`overwing-mcp`](https://github.com/frod27/overwing-mcp).
- A2A: `https://overwing.ai/a2a` answers "send message" for evaluations and User-Agent lookups.

MIT © Overwing. Verdicts are produced by TypeSafe's Jev System One model; Overwing is not affiliated with TypeSafe.
