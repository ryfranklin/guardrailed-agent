# Demo Environment

Deployment for the ms3dm.tech `Demo` account. Composes the data-plane, identity, guardrails, tools, agent, and observability modules.

## First-time deploy

```bash
cp terraform.tfvars.example terraform.tfvars
# tweak settings as needed (defaults are usually fine)
terraform init
terraform plan
terraform apply
```

Then seed data and run the smoke test from the repo root:

```bash
./scripts/seed-data.sh
./scripts/smoke-test.sh
```

## Two-phase nature

`terraform apply` provisions infrastructure and Lake Formation tag *definitions*, but column-level LF-Tag *attachments* (which require existing tables) are applied by the synthesizer in `./scripts/seed-data.sh`. Re-running `terraform apply` after seeding is idempotent and harmless.

## Inputs

See `variables.tf`. None are required — every variable has a sensible default. Observability now uses the AgentCore-native CloudWatch log group `/gagent/invocations` (override via `invocation_log_group` / `invocation_log_retention_days`); the previously-required `langfuse_public_key` / `langfuse_secret_key` are gone.

## Outputs

See `outputs.tf`. The smoke test reads `analyst_role_arn`, `admin_role_arn`, `agent_id`, and `agent_alias_id` via `terraform output -raw`.

## State backend

Local backend by default. For production deployments, configure an S3 + DynamoDB remote backend in a `backend.tf` file (gitignored when env-specific) before `terraform init`.
