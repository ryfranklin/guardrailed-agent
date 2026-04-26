# Guardrailed Agent

A Bedrock-native, governed agentic-AI reference architecture that ms3dm.tech ships into clients' AWS accounts as a consulting deliverable.

The architecture demonstrably enforces data governance (Lake Formation row/column-level security), demonstrably blocks PII leakage (Bedrock Guardrails), and produces reviewable IaC artifacts a client's security team can sign off on without surprise.

> *"Every layer of this is reviewable IaC, auditable in CloudTrail, governed by Lake Formation, and inside your AWS perimeter. Your security and legal teams won't have new questions — only the same questions they already know how to answer."*

## Status

**Phase 0: scaffolded.** The repo layout, docs, and stubs are in place. Phase 1 implementation has not started.

What is **not** yet built:
- Terraform modules (`terraform/modules/*`) — stubs only
- Lambda action group (`lambdas/query_ambassadors/`) — stub only
- Synthetic data generator (`data/synthesizer/`) — stub only
- Eval harness (`eval/`) — stub only
- Operator scripts (`scripts/`) — stubs only

Phase 1 acceptance criteria are defined in §7 of `docs/repo-bootstrap-brief.md`. Do not declare Phase 1 done until every numbered criterion holds.

## Architecture summary

See `ARCHITECTURE.md` for the one-page summary of decisions and links to ADRs. The full bootstrap brief is at `docs/repo-bootstrap-brief.md`.

Stack at a glance:

| Layer | Choice |
|---|---|
| Agent runtime | Amazon Bedrock Agents |
| Guardrails | Amazon Bedrock Guardrails |
| Model | Anthropic Claude (Sonnet default; Opus selectable) |
| Tools | AWS Lambda action groups |
| Data plane | S3 + Apache Iceberg + AWS Glue + AWS Lake Formation |
| Identity | ABAC via session tags |
| Observability | Bedrock-native traces + Langfuse cloud |
| IaC | Terraform (HCL), module-per-concern |
| Topology | One AWS account per environment/client |

## Repo layout

```
terraform/      Terraform modules and per-environment compositions
lambdas/        Lambda action group implementations (Python 3.12)
data/           Synthetic ambassador data generator (Faker + Parquet)
eval/           Prompt corpora and runner for golden + red-team cases
scripts/        Operator entry points (deploy, seed, smoke test, invoke)
docs/           Brief, getting-started, demo script, runbook
.github/        CI workflows (terraform fmt/validate/tflint, eval smoke)
```

## Local prerequisites

For Phase 1 work:

- **Terraform** `>= 1.7` (AWS provider `~> 5.0`)
- **Python** `3.12`
- **AWS CLI** v2 with credentials for the target account (Demo or client)
- **Langfuse** account (cloud, `cloud.langfuse.com`) and a public/secret key pair; the secret is stored in AWS Secrets Manager at deploy time
- **Region:** `us-east-1` is the default

No additional runtime dependencies — every component is AWS-managed except Langfuse.

## Running the scripts (once Phase 1 ships)

These scripts will exist after Phase 1. They are stubs today.

| Script | What it does |
|---|---|
| `scripts/deploy-demo.sh` | `terraform apply` against `terraform/envs/demo/` for the ms3dm.tech Demo account |
| `scripts/seed-data.sh` | Run the synthetic data generator and register the four Iceberg tables in Glue |
| `scripts/smoke-test.sh` | The demo moment — same prompt under Analyst vs Admin, asserting redacted vs full PII |
| `scripts/invoke-agent.py` | Headless CLI to invoke the Bedrock Agent under an assumed role with session tags |

Typical first-time deploy will look like:

```bash
cd terraform/envs/demo
cp terraform.tfvars.example terraform.tfvars
# fill in langfuse_public_key, langfuse_secret_key_arn, etc.
terraform init
terraform plan
terraform apply

cd ../../..
./scripts/seed-data.sh
./scripts/smoke-test.sh
```

## Engagement modes

The same Terraform module supports three deployment modes:

- **A. Managed** — operator runs `terraform apply` against an account in the ms3dm.tech AWS Org
- **B. Delivery** — operator runs `terraform apply` via cross-account role into the client's account
- **C. DIY** — client's own team runs `terraform apply` in their account

The deployable module in `terraform/modules/` and `terraform/envs/` is account-agnostic; nothing about the org or Control Tower leaks in.

## Contributing

Before changing structural decisions, check:

1. `docs/repo-bootstrap-brief.md` — the bootstrap brief
2. `ARCHITECTURE.md` — the decisions summary
3. The ADRs in the ms3dm.tech vault under `consulting/guardrailed-agent/decisions/`

Conventions:

- No emojis in code, comments, or docs
- No comments unless the *why* is non-obvious
- Commits start with an area prefix: `tf:`, `lambda:`, `data:`, `eval:`, `docs:`
- Integration tests run against real AWS in the Demo account; do not mock Lake Formation or Bedrock

## License

Apache 2.0 — see [LICENSE](./LICENSE). Copyright 2026 Moonshot 3DM (ms3dm.tech).
