# Guardrailed Agent

A Bedrock-native, governed agentic-AI reference architecture that ms3dm.tech ships into clients' AWS accounts as a consulting deliverable.

The architecture demonstrably enforces data governance (Lake Formation row/column-level security), demonstrably blocks PII leakage (Bedrock Guardrails), and produces reviewable IaC artifacts a client's security team can sign off on without surprise.

> *"Every layer of this is reviewable IaC, auditable in CloudTrail, governed by Lake Formation, and inside your AWS perimeter. Your security and legal teams won't have new questions — only the same questions they already know how to answer."*

## Try it

Live demo: **<https://demo.ms3dm.tech>** — sign in (Cognito Hosted UI; email/password, Google, or Slack), pick a persona, and either chat with the agent or browse the **Data** tab to see the same SQL surface different rows under different personas. Lake Formation is the gate; same dataset, three personas, three different views.

## Status

**Phases 1, 2, and 3.a shipped.** The system is running end-to-end against synthetic HVAC home-services data in the ms3dm.tech Demo AWS account.

| Phase | What shipped | Source of truth |
|---|---|---|
| **1 — Private demo** | Bedrock Agent + Guardrails + Lake Formation governed dataset (HVAC) + governed_query Lambda action group + the `Dispatcher / TechnicianLead / Owner` personas with ABAC session tags + Athena workgroup + eval harness. Operator runs `./scripts/deploy-demo.sh` then `./scripts/seed-data.sh` then `./scripts/smoke-test.sh`. | [`docs/repo-bootstrap-brief.md`](docs/repo-bootstrap-brief.md) |
| **2 — Headless backend** | `gagent_client` shared library + `gra` CLI + MCP server (governance-tool readers + agent invocation) + `/gagent/invocations` AgentCore-Observability log group + SMUS notebook. Every surface is a thin wrapper over the same `gagent_client.invoke()` pipeline. | ADR-006 / ADR-009 |
| **3.a — Public web demo** | Cognito user pool + 3 federated IdPs (Google, Slack, Cognito-native; GitHub deferred to 3.5) + API Gateway HTTP API + JWT authorizer + WAF v2 ACL + gateway Lambda + Vite/React/TypeScript SPA + S3+CloudFront with ACM cert at `demo.ms3dm.tech`. | [`docs/phase-3a-brief.md`](docs/phase-3a-brief.md) |

Open items (Phase 3.5 / 3.b backlog, not blocking):

- Streaming responses (Lambda Function URL or WebSocket) — removes the 30s API Gateway integration ceiling.
- WAF enforcement on the gateway HTTP API stage — AWS WAFv2 doesn't yet support API Gateway HTTP API v2 stages; the ACL exists with three rules but is not associated.
- GitHub IdP — Cognito's generic OIDC provider rejects the `username = id` mapping because GitHub doesn't expose a `sub` claim. Reintroducing it is a code-only change.
- Custom Hosted UI domain (`auth.ms3dm.tech`) and custom API Gateway domain (`api.demo.ms3dm.tech`).
- Persistent chat history (DynamoDB per Cognito user).
- Slack adapter (Phase 3.b — invokes the gateway Lambda directly).

## Architecture

The full architectural deep-dive is at [`ARCHITECTURE.md`](ARCHITECTURE.md). Stack at a glance:

| Layer | Choice |
|---|---|
| Agent runtime | Amazon Bedrock Agents |
| Guardrails | Amazon Bedrock Guardrails |
| Model | Anthropic Claude (Sonnet 4.6 default; Opus selectable) |
| Tools | AWS Lambda action groups |
| Data plane | S3 + Apache Iceberg + AWS Glue + AWS Lake Formation (LF-Tags + grants) |
| Identity (private demo) | ABAC via session tags; persona role assumed via STS |
| Identity (public demo) | Cognito User Pool + JWT; persona resolution per-request via `CognitoPersonaResolver` |
| Observability | Bedrock-native traces + CloudWatch Logs `/gagent/invocations` + AgentCore Observability + X-Ray on Lambdas |
| API surface | API Gateway HTTP API (`POST /ask`, `POST /preview`) behind a Cognito JWT authorizer |
| Web | Vite + React 18 + TypeScript SPA on S3 + CloudFront, ACM cert in `us-east-1` |
| IaC | Terraform `>= 1.7`, AWS provider `~> 5.0`, module-per-concern |
| Topology | One AWS account per environment / client (no pooled SaaS, no shared backends) |

## Repo layout

```
terraform/
  modules/
    auth/             Cognito user pool + IdPs + Hosted UI domain
    gateway/          HTTP API + JWT authorizer + WAF + gateway Lambda
    web-demo/         S3 + CloudFront + ACM (us-east-1)
    agent/            Bedrock Agent + alias + action group attachment
    guardrails/       Bedrock Guardrail
    identity/         3 persona IAM roles with ABAC trust policies
    data-plane/       S3 buckets, Glue DB, Athena workgroup, LF-Tags
    tools/            governed_query Lambda + OpenAPI schema mirror
    observability/    /gagent/invocations log group
  envs/demo/          The single composing env layer (Demo account)
  bootstrap/oidc/     One-shot OIDC role for GitHub Actions

lambdas/
  governed_query/     6 SQL templates over the HVAC schema; persona-aware
  gateway/            POST /ask + POST /preview; Cognito-claims aware

gagent_client/        Shared invoke + identity + trace library
gra/                  CLI surface (gra ask | personas | traces)
mcp_server/           MCP server (ADR-009)

web/                  The public demo SPA (auth-gated chat + data view)

data/                 Synthetic HVAC dataset generator (Faker + Parquet)
eval/                 Prompt corpora + runner for golden + red-team cases
scripts/              Operator entry points (deploy, seed, smoke tests)
docs/                 Briefs, runbooks, getting started, MCP whitepaper
.github/workflows/    Terraform fmt+validate+tflint, eval, web build+deploy
```

## Local prerequisites

| Tool | Version |
|---|---|
| Terraform | `>= 1.7` (AWS provider `~> 5.0`) |
| Python | `3.12` |
| Node | 20.x LTS (for `web/`) |
| pnpm | 9 (for `web/`; `corepack enable pnpm` if not installed) |
| AWS CLI | v2, with credentials for the target account |
| Bedrock model access | `anthropic.claude-sonnet-4-6` (or whichever `foundation_model_id` you set) — request via Bedrock console |

Region: `us-east-1` is the default. CloudFront ACM certs MUST be in `us-east-1`; the `web-demo` module preconditions on this.

No additional runtime dependencies — every component is AWS-managed.

## Operator scripts

| Script | What it does |
|---|---|
| `scripts/deploy-demo.sh` | `terraform init` + `apply` against `terraform/envs/demo/`. Pulls IdP credentials from `terraform.tfvars`. |
| `scripts/seed-data.sh` | Generate synthetic HVAC data and register the Iceberg tables in Glue. |
| `scripts/smoke-test.sh` | Phase 1 demo moment — same prompt under Dispatcher vs Owner via `gra` CLI, asserting redacted vs full PII. |
| `scripts/smoke-test-sensitivity.sh` | Sister script for the sensitivity-tag gate (Owner-only columns like `parts_inventory.unit_cost_usd`). |
| `scripts/smoke-web.sh` | Phase 3.a public-demo smoke test. Hits `POST /ask` under each persona with a fixed prompt; requires `SMOKE_TEST_JWT` (Cognito ID token) in env. See [`docs/phase-3a-brief.md` §14](docs/phase-3a-brief.md). |
| `gra ask` | Headless CLI: invoke the Bedrock Agent under an assumed persona (installed by `pip install -e .`). |

Typical first-time deploy:

```bash
# 1. Phase 1 — agent + dataset + governance
cd terraform/envs/demo
cp terraform.tfvars.example terraform.tfvars
# fill in IdP OAuth credentials for Phase 3.a (google_*, github_*, slack_*)
terraform init && terraform apply

cd ../../..
./scripts/seed-data.sh
./scripts/smoke-test.sh   # verify Phase 1 + 2

# 2. Phase 3.a — public web demo
# ACM cert validation prompts you to add a CNAME at IONOS during apply.
# After apply, paste the second CNAME (demo → CloudFront) at IONOS.
# Build + deploy the SPA bundle to S3 + invalidate CloudFront:
cd web
pnpm install --frozen-lockfile
pnpm build  # uses VITE_* env vars from /gagent/demo/* SSM parameters
aws s3 sync dist/ "s3://$(aws ssm get-parameter --name /gagent/demo/web_bucket_name --query Parameter.Value --output text)/" --delete
aws cloudfront create-invalidation --distribution-id "$(aws ssm get-parameter --name /gagent/demo/web_distribution_id --query Parameter.Value --output text)" --paths "/*"
```

Step-by-step walkthroughs:

- [`docs/getting-started.md`](docs/getting-started.md) — happy-path deploy of Phases 1+2.
- [`docs/operator-runbook.md`](docs/operator-runbook.md) — runtime operator playbook (cold-start to demo, day-2 verification).
- [`docs/phase-3a-brief.md`](docs/phase-3a-brief.md) — the Phase 3.a build brief (auth, gateway, web SPA, web-demo TF, DNS plan, smoke test).
- [`docs/domains-and-dns.md`](docs/domains-and-dns.md) — IONOS DNS records for `demo.ms3dm.tech` and ACM validation.
- [`docs/runbook.md`](docs/runbook.md) — incident response + decommission.

## Engagement modes

The same Terraform module supports three deployment modes:

- **A. Managed** — operator runs `terraform apply` against an account in the ms3dm.tech AWS Org.
- **B. Delivery** — operator runs `terraform apply` via cross-account role into the client's account.
- **C. DIY** — client's own team runs `terraform apply` in their account.

The deployable module in `terraform/modules/` and `terraform/envs/` is account-agnostic; nothing about the org or Control Tower leaks in.

## Contributing

Before changing structural decisions, check:

1. [`docs/repo-bootstrap-brief.md`](docs/repo-bootstrap-brief.md) — the bootstrap brief (Phases 1+2).
2. [`docs/phase-3a-brief.md`](docs/phase-3a-brief.md) — the public-demo brief (Phase 3.a).
3. [`ARCHITECTURE.md`](ARCHITECTURE.md) — the architectural deep-dive.
4. The ADRs in the ms3dm.tech vault under `consulting/guardrailed-agent/decisions/`.

Conventions:

- No emojis in code, comments, or docs.
- No comments unless the *why* is non-obvious.
- Commits start with an area prefix: `tf:`, `lambda:`, `lib:`, `web:`, `auth:`, `gateway:`, `data:`, `eval:`, `docs:`.
- Integration tests run against real AWS in the Demo account; do not mock Lake Formation or Bedrock.
- Greenfield repo — no backwards-compatibility cruft.

## License

Apache 2.0 — see [LICENSE](./LICENSE). Copyright 2026 Moonshot 3DM (ms3dm.tech).
