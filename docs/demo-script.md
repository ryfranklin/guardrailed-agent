# Demo Script — Analyst vs Admin

The 90-second moment that pays for the rest of the conversation.

## Setup before walking on stage

Run `./scripts/deploy-demo.sh && ./scripts/seed-data.sh` beforehand. Open three terminal windows side by side and pre-load:

1. The smoke test command, ready to press Enter
2. The Langfuse project URL
3. The CloudTrail event search filtered to `eventName = InvokeAgent` for the last hour

## The talk track

> "Same agent, same prompt, two different users. One is an analyst. One is an admin. Watch what changes."

Run:

```bash
./scripts/smoke-test.sh
```

The output shows two passes:

**As Analyst:** the model returns ambassador `a1b2c3` with `email: REDACTED`, `phone: REDACTED`, `ssn_last4: REDACTED`, `street_address: REDACTED`, `city: REDACTED`, `postal_code: REDACTED`, `date_of_birth: REDACTED`. Non-PII fields (rank, status, region, enrollment_date) are real.

**As Admin:** the same model with the same prompt returns the same record with realistic-looking PII values. No code change. No agent re-prompting. The boundary is in Lake Formation.

> "Three things to notice. First, this is the same Bedrock Agent, the same Claude model, the same Lambda. Nothing about the agent or its prompts changed. Second, the redaction isn't a model behavior — the model literally never sees the PII when an analyst asks. Lake Formation hides those columns at query time. Third, both calls just appeared in CloudTrail with the assumed-role identity, so your audit trail already knows what each user did."

Switch to the Langfuse window:

> "And here's every prompt, response, tool call, guardrail decision, and token count, in real time. Same trace shape across both calls — but the response payloads diverge exactly where Lake Formation said they should."

Switch to the CloudTrail window:

> "And here are both `InvokeAgent` events under the assumed-role principal. Your security team can answer 'who saw what?' with a query they already know how to write."

## What to do if a question lands

- **"Could the model leak PII via inference?"** — The Guardrail's PII filter is set to ANONYMIZE on the response side too. Even if the model fabricated PII, the Guardrail would replace it with `<EMAIL>` / `<PHONE>` / etc. before the user sees it.
- **"What stops a prompt-injection bypass?"** — Bedrock Guardrails' prompt-attack filter at HIGH. The red-team eval corpus has injection cases that exercise this path; runs on every push.
- **"What if the analyst is curious about another region?"** — Show the RegionalManager persona. Same agent, different LF row filter. Region tag on the session restricts which rows even reach the model.

## What this demo does NOT show, and what's next

- **Slack / web / mobile clients.** Phase 2. The agent is headless; the CLI is one of N clients.
- **Multi-tenant SaaS.** Out of scope by design. Per ADR-002 we ship into client accounts.
- **Self-hosted Langfuse.** Cloud for v1. Self-host on request for clients with data-residency constraints.
- **Knowledge bases.** Phase 2 — once the data plane is proved out, add RAG over governance docs.
