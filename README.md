<p align="center">
  <a href="https://overwing.ai">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/frod27/overwing-python/main/assets/wordmark-dark.svg">
      <img src="https://raw.githubusercontent.com/frod27/overwing-python/main/assets/wordmark.svg" alt="Overwing" width="220">
    </picture>
  </a>
</p>

<p align="center"><strong>Guardrails for LLM output, in one line.</strong><br>
OpenAI Agents SDK guardrails, LangChain runnables and callbacks, and a typed client. Also: <a href="#atlas-who-is-this-user-agent-no-key-needed">Atlas</a> user-agent lookups with no key, <a href="#preflight-should-the-agent-sign-this-solana-transaction">Preflight</a> checks before an agent signs a Solana transaction, and <a href="#tower-let-an-agent-operate-a-legacy-system">Tower</a> for agents operating legacy systems. Every message gets a <code>pass</code> / <code>fail</code> / <code>review</code> verdict with calibrated confidence before it reaches your user.</p>

<p align="center">
  <a href="https://pypi.org/project/overwing/"><img alt="PyPI" src="https://img.shields.io/pypi/v/overwing?color=0B1220&label=overwing"></a>
  <a href="https://github.com/frod27/overwing-python/actions"><img alt="CI" src="https://github.com/frod27/overwing-python/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://overwing.ai/docs"><img alt="API reference" src="https://img.shields.io/badge/API-reference-0B1220"></a>
  <a href="https://overwing.ai"><img alt="agents welcome" src="https://overwing.ai/badge.svg"></a>
</p>

---

```bash
pip install overwing               # client, Atlas, Beacon, Preflight, Tower
pip install "overwing[agents]"      # OpenAI Agents SDK guardrails
pip install "overwing[langchain]"   # LangChain guard runnable + callbacks
```

It works with no key: `Overwing().evaluate(text)` runs 10 evaluations a day on inputs up to 2,000 characters, and text sent without a key is not stored. For more, get a free API key at [overwing.ai](https://overwing.ai/login) (250 evaluations a day), or let your agent make its own account with `Overwing.signup()`, which needs no email (see [An account for an agent](#an-account-for-an-agent-no-email)).

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

### Register your own agent

If you run an agent or a crawler, add it to the registry so a lookup of its User-Agent names you. It is free and needs an API key.

```python
atlas = Atlas()   # OVERWING_API_KEY, or Atlas("ow_live_...")

reg = atlas.register("AcmeBot", operator="Acme, Inc.", domain="acme.com", tokens=["AcmeBot"], purpose="user_fetch")
reg.dns_name, reg.dns_value    # the TXT record: _overwing-atlas.acme.com, overwing-atlas-verification=...
reg.file_url, reg.file_body    # or serve the same value at https://acme.com/.well-known/overwing-atlas.txt

# publish either one, then:
reg = atlas.verify_registration(reg.id)
if reg.published: ...          # in the registry
elif reg.pending_verification: reg.error   # not found yet: what was looked for
```

`atlas.registrations()` lists yours and `atlas.withdraw_registration(id)` takes one out. Registration proves control of the operator's domain. A User-Agent is still a string anyone can send, so the entry is listed as user-agent only unless `key_directory_url` is a Web Bot Auth key directory on that domain.

## Beacon: is your product reachable by agents? (free)

[Overwing Beacon](https://overwing.ai/beacon) checks one site and answers three questions: can an agent find it, read it, and use it. It looks for robots.txt rules for AI agents, llms.txt, an MCP server card and endpoint, an A2A agent card and an OpenAPI document, and reads the home page the way an agent does. A check is free: with a key you get the full report, saved to your dashboard; with no key you get the summary (the score, the three answers and the first fix).

```python
from overwing import Beacon

beacon = Beacon()                         # api_key= or OVERWING_API_KEY for the full report
beacon.sample().top_fixes                 # a real report in full, to see the shape

check = beacon.start("example.com")
check = beacon.wait_for_report(check.id)  # the first read runs the check
if check.complete:
    check.score        # 0 to 100
    check.verdict      # "yes" | "partly" | "no"
    check.categories   # find, read, use, each with an answer
    check.top_fixes    # [{"check", "fix", "gain"}], most valuable first
    check.full         # False with no key: the summary, with check.counts
    check.checks       # every check, with what was found and a fix (full report)
```

A key is free: `POST https://overwing.ai/api/v1/signup` returns one. An agent with a wallet and no account can pass `beacon.x402_url("example.com")` to any x402 client, pay $1 in USDC, and get the full report as the response. `AsyncBeacon` is the async twin.

## Preflight: should the agent sign this Solana transaction?

[Overwing Preflight](https://overwing.ai/preflight) checks one unsigned Solana transaction against your policy before the agent signs it. It simulates the transaction against the chain as it is now, works out what it would take from the wallet you name, and answers `allow` or `refuse` with every reason. It never sees a private key and never sends anything to the chain.

The check only protects a wallet when it sits in the signing path, so put it there. `guarded_sign` calls your signing function only after an allow:

```python
from overwing import Preflight
from overwing.solana import guarded_sign, PreflightRefused

preflight = Preflight()                    # OVERWING_API_KEY
policy = {
    "wallet": str(keypair.pubkey()),       # the address to protect; it must sign the transaction
    "max_sol_out": 0.05,                   # the most SOL that may leave, fees included
    # optional: "max_token_out", "min_token_in", "allowed_programs", "allow_delegation"
}

try:
    signed = guarded_sign(preflight, sign, tx, policy)   # sign(tx) runs only after an allow
except PreflightRefused as e:
    e.verdict.reasons                      # [{"code": "sol_out_exceeds_limit", "detail": "..."}]: do not sign
```

`tx` is the serialized transaction as base64 text, as `bytes`, or as any object `bytes()` can serialize, which a solders transaction is. No Solana library is installed or needed. Your signing function gets the same object that was checked.

It fails closed. A refusal raises `PreflightRefused`, which carries the verdict. No verdict at all (the API unreachable, a timeout, a 5xx, an answer that cannot be read) raises `OverwingError`. In both cases the signing function is never called. `on_unavailable="allow"` signs anyway when there is no verdict and logs a warning; a refusal, a rejected request (400, 401) and a spent allowance (429) still raise.

`require_allow(preflight, tx, policy)` is the check alone: it returns the verdict on an allow and raises otherwise. A verdict describes the chain for `verdict.valid_for_seconds` (120), so sign and send at once. With an async client use `guarded_sign_async` and `require_allow_async`.

```python
verdict = preflight.check(tx, policy)      # returns for allow and for refuse; branch on verdict.allowed
verdict.effects                            # what the simulation says would leave and arrive
verdict.covered, verdict.receipt           # inside the guarantee or not, and the signed receipt

preflight.report(verdict.id, signature)    # after it lands: .outcome is "miss" or "not_a_miss"
preflight.verdict(verdict.id)              # the public record of one verdict (no key)
preflight.record()                         # totals, misses and the latest verdicts (no key)
preflight.overview()                       # policy fields, reason codes, prices (no key)
```

One check counts as one evaluation. `AsyncPreflight` is the async twin, and `Overwing` and `AsyncOverwing` have the same calls as `preflight_check`, `preflight_verdict`, `preflight_report`, `preflight_record` and `preflight_overview`. An agent with a wallet and no account can post the same body to `preflight.x402_url()` with any x402 client for $0.01 in USDC.

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

### An account for an agent (no email)

An agent has no inbox, and should not put a person's address on an account that person did not ask for. So an account needs no email:

```python
account, ow = Overwing.signup()        # nothing is sent to anyone
save_somewhere_safe(account.api_key)   # shown once; there is no reset link
ow.evaluate("...")                     # 50 evaluations a day to start
```

A domain the account proves it controls takes the place of the email. It raises the limits to the normal free tier, opens full Beacon reports, and makes a lost key recoverable:

```python
proof = ow.prove_domain("acme.com")
proof.dns_name, proof.dns_value        # publish this TXT record, or serve proof.file_body at proof.file_url
proof = ow.verify_domain()
if not proof.verified: proof.error     # not there yet; DNS can take a few minutes

# Key lost: prove the domain again. Every old key is revoked and one new key is returned.
Overwing.start_recovery("acme.com")
account, ow = Overwing.finish_recovery("acme.com")
```

Registering an agent in Atlas on a domain (`Atlas.register`) proves that domain for the account in the same step. `ow.claim(email, password)` lets a person take charge later and get a dashboard login. `AsyncOverwing` has the same methods.

## Data handling

Text you evaluate is sent to the Overwing API and from there to TypeSafe, whose Jev model produces the verdict. It is not used to train models. Without a key it is never stored. With a key, the text, context and verdict are stored so you can read them back, until you delete them; pass `store=False` to keep no text or context for a call:

```python
ow.evaluate(text, store=False)
```

Organization-wide settings (`store_inputs`, `retention_days`) and keys restricted to running checks (`scope: "evaluate"`) are described at [overwing.ai/security](https://overwing.ai/security), along with subprocessors and how to report a vulnerability.

## How verdicts work

Each rule has a fail condition, an optional review threshold, and a weight. The prebuilt `content-safety` set checks toxicity, personal data, self-harm, sexual content, and severity. **fail** means a rule matched. **review** means a rule was unsure. **pass** is everything else. Full guide: [overwing.ai/llms.txt](https://overwing.ai/llms.txt). Reference: [overwing.ai/docs](https://overwing.ai/docs).

## Also from Overwing

- [`overwing`](https://github.com/frod27/overwing-js) on npm: the same client, a Vercel AI SDK middleware, and Agents SDK guardrails for JavaScript.
- MCP: the same tools for Claude, Cursor and any MCP client, hosted at `https://overwing.ai/mcp` with no install, or from npm as [`overwing-mcp`](https://github.com/frod27/overwing-mcp).
- A2A: `https://overwing.ai/a2a` answers "send message" for evaluations and User-Agent lookups.

MIT © Overwing. Verdicts are produced by TypeSafe's Jev System One model; Overwing is not affiliated with TypeSafe.
