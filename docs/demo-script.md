# Demo Script — Dispatcher vs Owner

The 90-second moment that pays for the rest of the conversation.

> ADR-008 renamed personas: Analyst → **Dispatcher**, RegionalManager → **TechnicianLead**, Admin → **Owner**. The mechanics — same Bedrock Agent, ABAC session tags, Lake Formation as the gate — are unchanged.

## Setup before walking on stage

Run `./scripts/deploy-demo.sh && ./scripts/seed-data.sh` beforehand. Open three terminal windows side by side and pre-load:

1. The smoke test command, ready to press Enter
2. The CloudWatch GenAI Observability console (or the `/gagent/invocations` log group in CloudWatch Logs Insights)
3. The CloudTrail event search filtered to `eventName = InvokeAgent` for the last hour

## The talk track

> "Same agent, same prompt, two different users. One is a dispatcher. One is the owner. Watch what changes."

Run:

```bash
./scripts/smoke-test.sh
```

The output shows two passes:

**As Dispatcher:** the model returns the customer record with `email: REDACTED`, `phone: REDACTED`, `street_address: REDACTED`, `city: REDACTED`, `postal_code: REDACTED`, `billing_notes: REDACTED`. Non-PII fields (service_tier, service_region, customer_type) are real.

**As Owner:** the same model with the same prompt returns the same record with realistic-looking PII values. No code change. No agent re-prompting. The boundary is in Lake Formation.

> "Three things to notice. First, this is the same Bedrock Agent, the same Claude model, the same Lambda. Nothing about the agent or its prompts changed. Second, the redaction isn't a model behavior — the model literally never sees the PII when a dispatcher asks. Lake Formation hides those columns at query time. Third, both calls just appeared in CloudTrail with the assumed-role identity, so your audit trail already knows what each user did."

Open the CloudWatch GenAI Observability console for the agent:

> "And here's every prompt, response, tool call, guardrail decision, and token count, in real time — surfaced via AgentCore Observability over the `/gagent/invocations` log group, alongside the agent's auto-emitted X-Ray traces. Same trace shape across both calls — but the response payloads diverge exactly where Lake Formation said they should."

Switch to the CloudTrail window:

> "And here are both `InvokeAgent` events under the assumed-role principal. Your security team can answer 'who saw what?' with a query they already know how to write."

## The follow-up: sensitivity tag

> "PII is one axis. Cost data is another. Watch."

Run:

```bash
./scripts/smoke-test-sensitivity.sh
```

The output: same `SELECT unit_cost_usd FROM parts_inventory` query, three personas. Dispatcher and TechnicianLead get an Athena access denial; Owner gets the rows.

> "Same column, three personas, three outcomes. Lake Formation enforces a second tag — `sensitivity` — that gates margin-bearing data even from a TechnicianLead who otherwise sees full PII for their region. Owner is the only persona that sees costs and supplier terms. That's the second governance dimension small businesses need but rarely get cleanly."

## What to do if a question lands

- **"Could the model leak PII via inference?"** — The Guardrail's PII filter is set to ANONYMIZE on the response side too. Even if the model fabricated PII, the Guardrail would replace it with `<EMAIL>` / `<PHONE>` / etc. before the user sees it.
- **"What stops a prompt-injection bypass?"** — Bedrock Guardrails' prompt-attack filter at HIGH. The red-team eval corpus has injection cases that exercise this path; runs on every push.
- **"What if the dispatcher is curious about another service region?"** — Show the TechnicianLead persona. Same agent, different LF row filter. The `service_region` session tag restricts which rows even reach the model.

## What this demo does NOT show, and what's next

- **Slack / web / mobile clients.** Phase 2. The agent is headless; the CLI is one of N clients.
- **Multi-tenant SaaS.** Out of scope by design. Per ADR-002 we ship into client accounts.
- **External observability vendor.** Not needed — observability is fully AWS-native via AgentCore Observability + CloudWatch Logs, so client deployments stay self-contained inside their AWS perimeter.
- **Knowledge bases.** Phase 2 — once the data plane is proved out, add RAG over governance docs.
