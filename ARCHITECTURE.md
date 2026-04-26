# Architecture

One-page summary of the decisions that shape this repo. The full rationale lives in `docs/repo-bootstrap-brief.md` (the bootstrap brief) and the ADRs.

## Decisions table

| Layer | Choice | Source |
|---|---|---|
| Agent runtime | Amazon Bedrock Agents | [ADR-001](../ms3dm.tech/consulting/guardrailed-agent/decisions/001-bedrock-agents-and-guardrails.md) |
| Guardrails | Amazon Bedrock Guardrails | [ADR-001](../ms3dm.tech/consulting/guardrailed-agent/decisions/001-bedrock-agents-and-guardrails.md) |
| Model | Anthropic Claude on Bedrock (Sonnet default; Opus selectable) | [ADR-001](../ms3dm.tech/consulting/guardrailed-agent/decisions/001-bedrock-agents-and-guardrails.md) |
| Tools | AWS Lambda action groups (OpenAPI 3 schemas) | [ADR-001](../ms3dm.tech/consulting/guardrailed-agent/decisions/001-bedrock-agents-and-guardrails.md) |
| Data plane | S3 + Apache Iceberg + AWS Glue Catalog + AWS Lake Formation; cataloged in SMUS | [ADR-003](../ms3dm.tech/consulting/guardrailed-agent/decisions/003-data-plane-and-identity.md) |
| Identity propagation | ABAC via session tags (`aws:PrincipalTag/role`, `aws:PrincipalTag/client`); LF policies key off these | [ADR-003](../ms3dm.tech/consulting/guardrailed-agent/decisions/003-data-plane-and-identity.md) |
| Observability | Bedrock-native traces + Langfuse (cloud-hosted v1; self-hosted later if needed) | [ADR-004](../ms3dm.tech/consulting/guardrailed-agent/decisions/004-observability.md) |
| Front-end | Out of scope for v1; agent is **headless**. CLI invocation script for smoke test only. | — |
| IaC | Terraform (HCL); module-per-concern | brief §6 |
| Deployment topology | One AWS account per environment/client, inside the ms3dm.tech AWS Org (Control Tower–managed) | [ADR-002](../ms3dm.tech/consulting/guardrailed-agent/decisions/002-deployment-topology.md) |

Deprecated path (do not revisit):

- [ADR-000 — Retire NeMo Guardrails / EC2](../ms3dm.tech/consulting/guardrailed-agent/decisions/000-retire-nemo-guardrails-ec2.md)

## Why these choices, in one breath

The selling sentence is: *"Every layer is reviewable IaC, auditable in CloudTrail, governed by Lake Formation, and inside your AWS perimeter."* Every layer above was chosen so that a client's security and legal teams encounter only AWS-shaped questions they already know how to answer. Self-hosted alternatives (NeMo, EC2) were rejected explicitly in ADR-000.

## Engagement model

| Mode | Where `terraform apply` runs | Account context |
|---|---|---|
| A. Managed | ms3dm.tech operator | An account in the ms3dm.tech AWS Org named `client-<name>` |
| B. Delivery | ms3dm.tech operator via cross-account role | The client's own AWS account |
| C. DIY | Client's team | The client's own AWS account |

The same Terraform module supports all three. Anything Org / Control Tower–related lives outside the deployable module.

## Non-MVP design constraints (must remain accommodated)

These ship later but cannot be retrofitted cheaply. Phase 1 designs must keep them in scope.

- **Slack channel adapter and other future front-ends.** The agent backend is headless. The Phase 1 CLI is one client of `InvokeAgent`. Do not couple session state, conversation history, or auth to the CLI.
- **MCP server tool channel.** Lambdas keep business logic free of Bedrock-specific glue. The action group adapter is the only Bedrock-aware code, so wrapping (or replacing) the Lambda with an MCP server is a swap, not a refactor.
- **Mobile-private access (Tailscale or Cognito-fronted API).** Same headless principle — no assumption of a single front-end.
- **Per-client deployments via Topology C.** The deployable Terraform module is account-agnostic. No hardcoded ARNs, account IDs, or region-specific resource references. Variables for everything client-specific.
- **Terraform module reusability.** Every module in `terraform/modules/` is usable from a fresh `terraform/envs/<new>/` with only variables — no surgery inside the module to onboard a new env.

If a Phase 1 design choice would make any of the above hard to add later, raise it.

## Where to read more

- `docs/repo-bootstrap-brief.md` — full brief, including data schemas (§8), Lake Formation policy (§9), Guardrails policy (§10), agent config (§11), observability (§12), eval harness (§13), and smoke test (§14)
- ADRs in the ms3dm.tech vault: `consulting/guardrailed-agent/decisions/`
- Cross-engagement practice doc: `consulting/practice/deployment-topology.md`
