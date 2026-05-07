# Getting Started

Happy-path deploy of the Guardrailed Agent into the ms3dm.tech `Demo` AWS account, end to end.

## Prerequisites

- Terraform `>= 1.7`
- Python `3.12`
- AWS CLI v2 with credentials for the Demo account (or a client account in Topology B/C). The deploying principal needs admin-equivalent permissions in the target account for the first deploy — IAM, Lake Formation, S3, Glue, Athena, Bedrock, CloudWatch Logs, Lambda.
- Bedrock model access enabled in the target account for `anthropic.claude-sonnet-4-6-v1:0` (or whichever model you set in `foundation_model_id`). In the Bedrock console, request access under "Model access" if not already enabled.

## 1. Configure the env

```bash
cd terraform/envs/demo
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars` if you need to override defaults. Everything has a sensible default — override `region`, `foundation_model_id`, `lf_admin_principal_arns`, or `invocation_log_group` / `invocation_log_retention_days` only if needed.

## 2. Deploy

```bash
./scripts/deploy-demo.sh
```

The script runs `terraform init` then `terraform apply`. It does **not** seed data — Lake Formation column-tagging happens after tables exist.

The first apply provisions:

- S3 buckets (`gagent-data-demo-<account>`, `gagent-athena-demo-<account>`)
- Glue database `guardrailed_agent_demo`
- Athena workgroup `gagent-demo`
- Lake Formation: data-lake settings, the `pii` LF-Tag, principal grants via tag expressions
- IAM roles: `gagent-dispatcher-demo`, `gagent-technician-lead-demo`, `gagent-owner-demo` (per ADR-008)
- Bedrock Guardrail with PII anonymization, prompt-attack HIGH, denied topics, contextual grounding
- Bedrock Agent with the `governed_query` action group
- Lambda action group function and execution role
- CloudWatch invocation log group `/gagent/invocations` (AgentCore Observability)

Apply takes 5–10 minutes. Bedrock Agent preparation is the slowest step.

## 3. Seed the dataset

```bash
./scripts/seed-data.sh
```

This script:

1. Creates a Python virtualenv under `data/synthesizer/.venv` if missing.
2. Generates the ADR-008 HVAC dataset (~10k customers, ~500 technicians, ~50k service jobs, ~300k daily signals + telemetry rollups) as Parquet.
3. Uploads Parquet to `s3://gagent-data-demo-<account>/staging/`.
4. Issues Athena CTAS to land each table as Iceberg under `s3://gagent-data-demo-<account>/guardrailed_agent_demo/`.
5. Applies LF-Tag `pii=true` to the PII columns (per `data/synthesizer/schemas.py`).

Re-running is safe — each table is dropped and recreated.

## 4. Smoke test the demo moment

```bash
./scripts/smoke-test.sh
```

The script runs the same prompt twice, once under the Dispatcher role and once under the Owner role. Pass criteria: visible difference in PII fields. See `docs/demo-script.md` for the talk track. To verify the sensitivity-tag gate (Owner-only access to cost/margin columns), run `./scripts/smoke-test-sensitivity.sh`.

## 5. Run the eval suite

```bash
cd eval
pip install -r requirements.txt
python runner.py
```

The runner reads agent IDs and persona role ARNs from `terraform output`, assumes each persona role with session tags, calls `InvokeAgent` with `enableTrace=True`, and writes `eval/reports/eval-report-<timestamp>.md`. Exit code is non-zero on any failure.

## Decommission

`terraform destroy` from `terraform/envs/demo/`. The S3 buckets are retained — the lifecycle rule on Athena results expires query output after 30 days, but the data bucket holds Iceberg metadata you may want to keep for audit. Empty and delete the buckets manually if a clean teardown is required.
